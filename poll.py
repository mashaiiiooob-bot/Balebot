"""بررسی دوره‌ای کانال‌های تلگرام و ارسال خبرها به بله (برای GitHub Actions)."""
import asyncio
import random
from collections import deque
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import requests
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon import utils
from telethon.tl.functions.messages import CheckChatInviteRequest, GetPeerDialogsRequest
from telethon.tl.types import Channel, ChatInviteAlready, InputDialogPeer, InputPeerChannel

import settings
from bale_client import BaleClient
from cleaner import clean_message
from news_filter import is_news, is_ad, has_dropword
from profanity import has_profanity
from dedupe import signature, is_duplicate, is_duplicate_v1, compare as dedupe_compare, context as dedupe_context
import ai as ai_mod
import semantic
from promo import promo_loop
from state import State
from telegram_out import FanoutBale, TelegramLane
import tg_panel
import supa_sink
import admin_brain

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("balebot")

# شمارنده‌های بی‌نام (فقط عدد) برای عیب‌یابی؛ در status.json نوشته می‌شود
STATS = {"seen": 0, "no_text": 0, "emptied_by_cleaner": 0, "ad": 0, "dropword": 0, "profanity": 0, "not_news": 0, "duplicate": 0, "sent": 0, "failed": 0,
         "ai_banned": 0, "ai_ad": 0, "gate_calls": 0, "gate_errors": 0, "sh_gate_banned": 0, "sh_gate_ad": 0,
         "analyst_calls": 0, "analyst_errors": 0, "analyst_rescued": 0, "analyst_veto_confirmed": 0, "analyst_veto_overruled": 0, "sem_calls": 0, "sem_errors": 0, "sem_dup": 0, "sem_new": 0, "sem_llm": 0, "sem_rescued": 0, "sem_caught": 0, "sem_vetoed": 0, "dup_v1_only": 0, "dup_v2_only": 0, "low_importance": 0, "rate_cap": 0, "sh_gate_low": 0, "imp_1": 0, "imp_2": 0, "imp_3": 0, "imp_4": 0, "imp_5": 0, "bale_outage": 0, "too_old": 0, "ai_calls": 0, "ai_errors": 0, "ai_skipped": 0, "ai_rescued": 0, "ai_caught": 0,
         "brain_errors": 0, "brain_shadow_would_dup": 0, "sh_dup_ai_dup": 0, "sh_dup_ai_new": 0, "sh_new_ai_dup": 0, "sh_new_ai_new": 0}
AIC = None
ANALYST = {"on": False}   # تحلیل کامل در یک بار خواندن (جایگزین پرسش‌های جدای دروازه/تکراری)
SEM = {"olds": [], "c": None, "emb": None, "mem": semantic.Memory(), "cal": semantic.Calibrator(), "mode": "off", "vec": None, "top": None}   # معنایی (فقط RAM + کالیبراسیون)
SENT_TS = deque(maxlen=300)   # زمان ارسال‌های اخیر (برای سقف ساعتی)
SINK = supa_sink.from_env()      # آمار/رویدادها به Supabase (بدون env غیرفعال)؛ هرگز ارسال را مختل نمی‌کند
BRAIN = admin_brain.from_env()    # ادمین هوشمند: حافظه‌ی چندروزه (ADMIN_BRAIN=off|shadow|on؛ پیش‌فرض off)
GATE_IMP = {"v": None}        # اهمیتِ پیامِ در حال بررسی (از دروازه)
# ---- قطعی/خطای سراسری Bale: پیام‌ها رد نمی‌شوند؛ ربات صبر می‌کند و دوباره تلاش می‌کند ----
OUTAGE = {"until": 0.0, "step": 0, "since": 0.0, "diag_at": 0.0}
SERVICE_ERR = re.compile(r"Bale HTTP (?:401|403|404|408|429|5\d\d)|Bale network error")
# ---- مقایسه‌ی عوامل: قوانین (مرجع) در برابر هر سرویس هوش مصنوعی؛ فقط عدد، بدون متن ----
BENCH = {}          # نام -> tp/fp/fn/tn/skipped/err
BENCH_PAIR = {}     # "a|b" -> [موافق, کل]
BENCH_DAY = {"day": "", "n": 0}
LAT_E2E = deque(maxlen=500)  # تأخیر واقعی انتشار در تلگرام تا رسیدن به بله (ms)
_BG = set()
RECENT = []  # فقط در حافظه (متن پاک‌شده‌ی پیام‌های ارسالی اخیر)؛ هرگز ذخیره یا منتشر نمی‌شود
BASE_STATUS = {}
STARTED = time.time()


INVITE_RE = re.compile(r"(?:t\.me|telegram\.me)/(?:joinchat/|\+)([\w-]+)", re.I)


def source_label(entity):
    title = getattr(entity, "title", None)
    username = getattr(entity, "username", None)
    return title or (f"@{username}" if username else str(getattr(entity, "id", "Telegram")))


def key_for(entity):
    # HMAC با راز خصوصی: از روی state عمومی نمی‌توان فهمید یک کانال جزو منابع هست یا نه
    secret = (os.environ.get("TG_API_HASH", "") + "|balebot-key").encode()
    return hmac.new(secret, str(entity.id).encode(), hashlib.sha256).hexdigest()[:16]


def collect_names(entities):
    """عنوان و یوزرنیم همه‌ی منابع؛ چون یک کانال اغلب پایین‌نویس کانال‌های دیگر را هم در متن می‌گذارد."""
    names = []
    for e in entities:
        names.append(getattr(e, "title", None))
        names.append(getattr(e, "username", None))
        for u in getattr(e, "usernames", None) or []:
            names.append(getattr(u, "username", None))
    return [n for n in names if n]


def build_caption(text):
    parts = []
    if text:
        parts.append(text.strip())
    parts.append(settings.SIGNATURE)
    return "\n\n".join(parts)


def media_kind(message):
    mime = getattr(message.file, "mime_type", "") or ""
    if mime.startswith("image/"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


def sanitize_media(path, kind):
    """نام اصلی فایل را خنثی می‌کند و (در صورت وجود ffmpeg) متادیتای ویدیو/صدا را پاک می‌کند."""
    ext = os.path.splitext(path)[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", ext):
        ext = ""
    neutral = os.path.join(os.path.dirname(path), "media" + ext)
    os.replace(path, neutral)
    if kind in ("video", "audio") and shutil.which("ffmpeg"):
        out = os.path.join(os.path.dirname(path), "clean" + ext)
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", neutral,
                 "-map", "0", "-map_metadata", "-1", "-c", "copy", out],
                timeout=120, capture_output=True,
            )
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
                os.replace(out, neutral)
        except Exception:
            pass
        finally:
            if os.path.exists(out):
                os.remove(out)
    return neutral


async def forward(bale, message, text, tmpdir, text_only=False):
    caption = build_caption(text)
    media_path = None
    try:
        size = getattr(message.file, "size", 0) or 0
        if message.media and not text_only and size <= settings.MAX_FILE_MB * 1024 * 1024:
            media_path = await message.download_media(file=tmpdir)
            if media_path:
                media_path = await asyncio.to_thread(sanitize_media, media_path, media_kind(message))
        if media_path:
            await asyncio.to_thread(
                bale.send_media, settings.BALE_DESTINATION, media_path, caption, media_kind(message)
            )
        else:
            await asyncio.to_thread(bale.send_text, settings.BALE_DESTINATION, caption)
    finally:
        if media_path and os.path.exists(media_path):
            try:
                os.remove(media_path)
            except OSError:
                pass


async def alert(bale, state, code, text):
    """هشدار خطا به چت خصوصی مدیر در بله (حداکثر یک بار در ۲۴ ساعت برای هر نوع خطا)."""
    chat = settings.BALE_ADMIN_CHAT
    now = time.time()
    if not chat or not state.alert_due(code, now):
        return
    try:
        await asyncio.to_thread(bale.send_text, chat, "⚠️ Balebot: " + text)
        state.mark_alert(code, now)
        state.save()
    except Exception:
        log.warning("Admin alert could not be sent")


def ping_healthcheck():
    try:
        requests.get(settings.HEALTHCHECK_URL, timeout=10)
    except Exception:
        pass  # آدرس هرگز چاپ نمی‌شود


async def fetch_new(client, entity, last_id):
    if last_id:
        return [m async for m in client.iter_messages(entity, min_id=last_id, reverse=True, limit=100)]
    return None


def ai_candidates(sig, now, state=None):
    """نامزدهای مقایسه برای هوش مصنوعی: فقط پیام‌های اخیرِ «بدون تضاد مکان/عدد/جزئیات» که واژه‌ی اختصاصیِ مشترک دارند
    (شباهت فقط بر پایه‌ی واژه‌های رایج کافی نیست). خروجی: [(wo_min, wj, entry)] به ترتیب شباهت."""
    ctx = dedupe_context(state.recent_seen(now)) if state is not None else ({}, 0)
    out = []
    for e in RECENT:
        if now - e["t"] > 21600:
            continue
        c = dedupe_compare(sig, {"w": e["w"], "n": e["n"], "p": e.get("p", ())}, ctx)
        if c["conflict"] or c["mismatch"] or c["n_inter"] < 3 or c["distinct_inter"] < 1:
            continue
        out.append((c["wo_min"], c["wj"], e))
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return out[:3]


async def apply_semantic(reason, sig, text, state):
    """تصمیم معنایی. خروجی: (reason, decided). decided=False یعنی نظری نداد (به روش واژگانی/AI برگرد)."""
    SEM["vec"], SEM["top"] = None, None
    SEM["olds"], SEM["c"] = [], None
    emb = SEM["emb"]
    if SEM["mode"] == "off" or emb is None or not emb.provider or reason not in (None, "duplicate"):
        return reason, False
    STATS["sem_calls"] += 1
    vecs = await asyncio.to_thread(emb.embed, [text])
    if not vecs:
        STATS["sem_errors"] += 1
        return reason, False
    vec = vecs[0]
    SEM["vec"] = vec
    now = time.time()
    mem = SEM["mem"]
    ctx = dedupe_context(state.recent_seen(now))
    cands = mem.top(vec, sig, now, dedupe_compare, ctx)
    warm = mem.warm()
    if BRAIN.enabled:                           # حافظه‌ی چندروزه (Supabase): بعد از ریستارت هم فراموش نمی‌شود
        db = await asyncio.to_thread(BRAIN.match, vec, getattr(emb, "model", None), sig, now, dedupe_compare, ctx)
        if db is None:
            STATS["brain_errors"] += 1          # خطا/توقف: همان روش فعلی (fail-open)
        elif BRAIN.live:
            cands = admin_brain.merge(cands, db)
            warm = warm or BRAIN.warm
        elif db and (not cands or db[0][0] > cands[0][0]) and db[0][0] >= SEM["cal"].thresholds()[1]:
            STATS["brain_shadow_would_dup"] += 1    # حالت shadow: فقط اندازه‌گیری؛ تصمیم عوض نمی‌شود
    if cands:
        SEM["top"] = (cands[0][0], cands[0][1].get("id"))
    lo, hi = SEM["cal"].thresholds()
    if cands and cands[0][0] > lo:
        SEM["olds"], SEM["c"] = [x[1]["text"] for x in cands], cands[0][0]   # همتاها برای تحلیل‌گر
    if not cands:
        # هیچ همتای بدون تضاد در حافظه نیست. اگر روش واژگانی «تکراری» گفته بود و حافظه گرم نیست، به او اعتماد می‌کنیم.
        if reason == "duplicate" and not warm:
            return reason, False
        if reason == "duplicate":
            STATS["sem_vetoed"] += 1    # همتا فقط به‌خاطر تضاد مکان/عدد کنار رفت: واقعه‌ی دیگر
            STATS["sem_rescued"] += 1
        return None, not ANALYST["on"]
    c, entry = cands[0]
    if c >= hi and not entry.get("old"):         # نامزدِ قدیمی (بیش از ۱۲ ساعت) فقط «راهنما» است؛ حذف خودکار نمی‌شود، تحلیل‌گر/LLM تصمیم می‌گیرد
        verdict = True
    elif ANALYST["on"]:
        # شباهت متوسط/پایین: تحلیل‌گرِ متن کاملِ LLM تصمیم می‌گیرد. تکراریِ واژگانیِ بدون همتای نزدیک (c ≤ t_low) نجات می‌یابد.
        if reason == "duplicate" and c <= lo and warm:
            STATS["sem_rescued"] += 1
            return None, False
        return reason, False
    elif c <= lo:
        verdict = False
    else:
        if AIC is None or AIC.mode == "off" or not AIC.allow(state):
            return reason, False                # مبهم و LLM در دسترس نیست: تصمیم روش واژگانی
        STATS["sem_llm"] += 1
        res = await asyncio.to_thread(AIC.judge_duplicate, text, [x[1]["text"] for x in cands], state)
        if res is None:
            return reason, False
        verdict = res
        SEM["cal"].add(c, verdict)              # برچسب خودکار برای کالیبراسیون
    STATS["sem_dup" if verdict else "sem_new"] += 1
    if verdict:
        if reason is None:
            STATS["sem_caught"] += 1
        return "duplicate", True
    if reason == "duplicate":
        STATS["sem_rescued"] += 1
    return None, True


def brain_remember(key, msg, text, sig, outcome, reason):
    """ثبت پیام (متن پاک‌شده + امضا + بردار + تصمیم) در حافظه‌ی چندروزه. هرگز خطا نمی‌دهد."""
    try:
        if not BRAIN.enabled or reason in admin_brain.EARLY_REASONS or not (text or "").strip():
            return
        top = SEM["top"]
        BRAIN.remember(key[:12], msg.id, text, sig or signature(text), SEM["vec"], getattr(SEM["emb"], "model", None), outcome, reason,
                       dup_of=(top[1] if reason == "duplicate" and top else None), score=(top[0] if top else None), importance=GATE_IMP["v"])
    except Exception:                           # noqa: BLE001
        pass


def analysis_verdict(res):
    """از روی تحلیل کامل: None = ارسال؛ وگرنه کدِ دلیلِ حذف."""
    if res["banned"]:
        return "ai_banned"
    if res["ad"]:
        return "ai_ad"
    if res["same_event_as"] > 0 and not res["adds_new_info"]:
        return "duplicate"
    if settings.MIN_IMPORTANCE and res["importance"] < settings.MIN_IMPORTANCE:
        return "low_importance"
    return None


def veto_confirmed(verdict, second):
    """نظر دوم: حذف فقط با توافق. اگر نظر دوم در دسترس نباشد: ممنوع/تبلیغ/کم‌اهمیت پذیرفته می‌شود، ولی «تکراری» نه (پیام تازه نباید حذف شود)."""
    if second is None:
        return verdict != "duplicate"
    if verdict == "ai_banned":
        return second["banned"]
    if verdict == "ai_ad":
        return second["ad"]
    if verdict == "duplicate":
        return second["same_event_as"] > 0 and not second["adds_new_info"]
    return bool(settings.MIN_IMPORTANCE) and second["importance"] < settings.MIN_IMPORTANCE


async def apply_analyst(reason, sig, text, state):
    """متن کامل پیام را (با نزدیک‌ترین خبرهای قبلی) یک بار تحلیل می‌کند و تصمیم می‌گیرد. خروجی: (reason, decided)."""
    GATE_IMP["v"] = None
    if not ANALYST["on"] or AIC is None or AIC.mode == "off" or AIC.gate == "off" or reason not in (None, "duplicate"):
        return reason, False
    for _ in range(4):                       # سرویس‌ها شلوغ‌اند: چند ثانیه صبر، نه ارسال بدون بررسی
        if AIC.can_analyze(state):
            break
        await asyncio.sleep(2)
    else:
        STATS["ai_skipped"] += 1
        return reason, False
    olds = list(SEM["olds"]) or ([c[2]["text"] for c in ai_candidates(sig, time.time(), state)] if reason == "duplicate" else [])
    res, first = await asyncio.to_thread(AIC.analyze, text, olds, settings.DROP_WORDS, state)
    if res is None:
        STATS["analyst_errors"] += 1
        return reason, False
    STATS["analyst_calls"] += 1
    STATS[f"imp_{res['importance']}"] += 1
    GATE_IMP["v"] = res["importance"]
    if SEM["c"] is not None and olds and olds == SEM["olds"]:
        SEM["cal"].add(SEM["c"], res["same_event_as"] > 0 and not res["adds_new_info"])   # برچسب خودکار برای آستانه‌ها
    verdict = analysis_verdict(res)
    if verdict is None:
        if reason == "duplicate":
            STATS["analyst_rescued"] += 1
        return None, True
    second = None
    if AIC.can_analyze(state, (first,)):
        second, _ = await asyncio.to_thread(AIC.analyze, text, olds, settings.DROP_WORDS, state, (first,))
    if veto_confirmed(verdict, second):
        STATS["analyst_veto_confirmed"] += 1
        return verdict, True
    STATS["analyst_veto_overruled"] += 1
    return None, True


AUDIT_REASONS = ("not_news", "dropword", "ad", "profanity")
AUDIT_DAY = {"day": "", "n": 0}


def audit_wanted(reason):
    """نمونه‌ای از پیام‌های ردشده‌ی قوانین را هم هوش مصنوعی می‌خواند، تا اشتباه‌ات قوانین (پیام خوبِ ردشده) سنجیده شود."""
    if not ANALYST["on"] or AIC is None or AIC.mode == "off" or reason not in AUDIT_REASONS or settings.AUDIT_PER_DAY <= 0:
        return False
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if AUDIT_DAY["day"] != day:
        AUDIT_DAY.update({"day": day, "n": 0})
    if AUDIT_DAY["n"] >= settings.AUDIT_PER_DAY or random.random() > 0.12:
        return False
    AUDIT_DAY["n"] += 1
    return True


async def audit_task(text, reason, state):
    try:
        if not AIC.can_analyze(state):
            return
        res, _ = await asyncio.to_thread(AIC.analyze, text, [], settings.DROP_WORDS, state)
        if res is None:
            return
        STATS[f"audit_{reason}_n"] = STATS.get(f"audit_{reason}_n", 0) + 1
        good = res["newsworthy"] and not res["banned"] and not res["ad"] and res["importance"] >= max(1, settings.MIN_IMPORTANCE)
        if good:
            STATS[f"audit_{reason}_good"] = STATS.get(f"audit_{reason}_good", 0) + 1   # قوانین رد کردند ولی هوش مصنوعی می‌فرستاد
    except Exception:
        pass


async def apply_ai(reason, sig, text, state):
    """فقط موارد مرزی به هوش مصنوعی می‌رسد. حالت shadow: ثبت می‌کند ولی تصمیم را عوض نمی‌کند."""
    if AIC is None or AIC.mode == "off" or reason not in (None, "duplicate"):
        return reason
    cands = ai_candidates(sig, time.time(), state)
    if not cands:
        return reason
    ov, jc, _ = cands[0]
    if (ov >= 0.9 and jc >= 0.7) or ov < 0.5:
        return reason  # یا قطعاً تکراری، یا آشکارا متفاوت
    if not AIC.allow(state):
        STATS["ai_skipped"] += 1
        return reason
    STATS["ai_calls"] += 1
    verdict = await asyncio.to_thread(AIC.judge_duplicate, text, [c[2]["text"] for c in cands], state)
    if verdict is None:
        STATS["ai_errors"] += 1
        return reason
    rule_dup = reason == "duplicate"
    STATS[f"sh_{'dup' if rule_dup else 'new'}_ai_{'dup' if verdict else 'new'}"] += 1
    if AIC.mode != "on":
        return reason
    if rule_dup and not verdict:
        STATS["ai_rescued"] += 1
        return None
    if not rule_dup and verdict:
        STATS["ai_caught"] += 1
        return "duplicate"
    return reason


async def sem_backfill(client, chat):
    """بعد از ریستارت: آخرین پست‌های خودِ کانال را می‌خوانیم و دوباره embed می‌کنیم (متنی در ریپو ذخیره نمی‌شود)."""
    try:
        posts = []
        async for m in client.iter_messages(chat, limit=120):
            body = " ".join((m.message or "").split()).split("✍")[0].strip()
            if len(body) >= 12 and getattr(m, "date", None):
                posts.append((m.date.timestamp(), body))
        posts.sort()
        vecs = await asyncio.to_thread(SEM["emb"].embed, [b for _, b in posts]) if posts else None
        if vecs:
            for (ts, body), v in zip(posts, vecs):
                SEM["mem"].add(v, body, signature(body), now=ts)
            log.info("Semantic memory warmed with %d posts", len(vecs))
        else:
            STATS["sem_errors"] += 1
    except Exception as exc:
        STATS["sem_errors"] += 1
        log.warning("Semantic backfill failed: %s", type(exc).__name__)


def sem_info():
    emb = SEM["emb"]
    lo, hi = SEM["cal"].thresholds()
    return {"mode": SEM["mode"], "provider": getattr(emb, "provider", None), "model": getattr(emb, "model", None),
            "dims": getattr(emb, "dims", None), "probe": getattr(emb, "probe", None), "memory": len(SEM["mem"].items),
            "t_low": lo, "t_high": hi, "calibration_pairs": SEM["cal"].total(),
            "calibration": {k: v for k, v in sorted(SEM["cal"].b.items())}}


def rate_limit_reason(imp, now):
    """سقف نرم ساعتی برای حفاظت از بله (۴۲۹) و کم‌کردن حجم: بالاتر از سقف فقط اهمیت ۵ (یا نامشخص نه)؛ دو برابر سقف هیچ."""
    cap = settings.MAX_PER_HOUR
    if not cap:
        return None
    recent = sum(1 for t in SENT_TS if now - t < 3600)
    if recent >= 2 * cap or (recent >= cap and imp != 5):
        return "rate_cap"
    return None


async def apply_gate(text, state):
    """دروازه‌ی نهایی: فقط می‌تواند پیامِ آماده‌ی ارسال را «رد» کند؛ هرگز چیزی را که قانون‌ها رد کرده‌اند آزاد نمی‌کند."""
    GATE_IMP["v"] = None
    if AIC is None or AIC.mode == "off" or AIC.gate == "off":
        return None
    for _ in range(4):                      # سرویس‌ها شلوغ‌اند: چند ثانیه صبر می‌کنیم، نه ارسال بدون بررسی
        if AIC.allow(state):
            break
        await asyncio.sleep(2)
    else:
        STATS["ai_skipped"] += 1
        return None
    STATS["gate_calls"] += 1
    res = await asyncio.to_thread(AIC.judge_gate, text, settings.DROP_WORDS, state)
    if res is None:
        STATS["gate_errors"] += 1
        return None
    banned, ad, *rest = res
    imp = rest[0] if rest else None
    GATE_IMP["v"] = imp
    if imp:
        STATS[f"imp_{imp}"] += 1
    if banned or ad:
        STATS["sh_gate_banned" if banned else "sh_gate_ad"] += 1
        if AIC.gate == "on":
            return "ai_banned" if banned else "ai_ad"
    if imp is not None and settings.MIN_IMPORTANCE and imp < settings.MIN_IMPORTANCE:
        STATS["sh_gate_low"] += 1
        if AIC.gate == "on":
            return "low_importance"
    return None


def bench_update(results, positive):
    """positive=True: قوانین پیام را حتماً ممنوع/تبلیغ می‌دانند؛ False: پیام سالمِ ارسال‌شده."""
    pred = {}
    for name, r in results.items():
        b = BENCH.setdefault(name, {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "err": 0})
        if r is None:
            b["err"] += 1
            continue
        flagged = bool(r[0] or r[1])
        pred[name] = flagged
        b[("tp" if flagged else "fn") if positive else ("fp" if flagged else "tn")] += 1
    names = sorted(pred)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            rec = BENCH_PAIR.setdefault(f"{names[i]}|{names[j]}", [0, 0])
            rec[1] += 1
            rec[0] += pred[names[i]] == pred[names[j]]


def bench_wanted():
    if AIC is None or AIC.mode == "off" or settings.BENCH_PER_DAY <= 0:
        return False
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if BENCH_DAY["day"] != day:
        BENCH_DAY.update({"day": day, "n": 0})
    if BENCH_DAY["n"] >= settings.BENCH_PER_DAY or random.random() > settings.BENCH_RATE:
        return False
    BENCH_DAY["n"] += 1
    return True


async def bench_task(text, positive, state):
    try:
        res = await asyncio.to_thread(AIC.bench_gate, text, settings.DROP_WORDS, state)
        if res:
            bench_update(res, positive)
    except Exception:
        pass


STATS_BASE = {}      # آمار جمع‌شده‌ی اجراهای قبلی (از state)
SINCE = {"t": 0}


def restore_metrics(state):
    """آمار مقایسه بین اجراها (هر ~۵٫۵ ساعت) حفظ می‌شود: BENCH، توافق‌ها، تأخیرها و شمارنده‌ی سرویس‌ها."""
    m = state.data.get("metrics") or {}
    for k, v in (m.get("bench") or {}).items():
        BENCH[k] = dict(v)
    for k, v in (m.get("pair") or {}).items():
        BENCH_PAIR[k] = list(v)
    LAT_E2E.extend(m.get("e2e") or [])
    STATS_BASE.update({k: v for k, v in (m.get("stats") or {}).items() if isinstance(v, int)})
    SENT_TS.extend(m.get("sent_ts") or [])
    SINCE["t"] = m.get("since") or time.time()
    SEM["cal"] = semantic.Calibrator(m.get("sem_cal"))
    if AIC:
        for p in AIC.providers + AIC.failed:
            rec = (m.get("prov") or {}).get(p.name)
            if rec:
                p.calls, p.okc, p.errc = rec.get("calls", 0), rec.get("ok", 0), rec.get("err", 0)
                p.lat.extend(rec.get("lat") or [])


def snapshot_metrics(state):
    tot = dict(STATS_BASE)
    for k, v in STATS.items():
        if isinstance(v, int):
            tot[k] = tot.get(k, 0) + v
    state.data["metrics"] = {
        "since": SINCE["t"] or time.time(), "sem_cal": SEM["cal"].b, "sent_ts": [round(t) for t in list(SENT_TS)[-200:]], "bench": BENCH, "pair": BENCH_PAIR, "e2e": list(LAT_E2E)[-300:], "stats": tot,
        "prov": {p.name: {"calls": p.calls, "ok": p.okc, "err": p.errc, "lat": [round(x) for x in list(p.lat)[-100:]]}
                 for p in ((AIC.providers + AIC.failed) if AIC else [])},
    }
    state.dirty = True
    return tot


def compare_report():
    lat = sorted(LAT_E2E)
    q = lambda f: round(lat[min(len(lat) - 1, int(len(lat) * f))] / 1000, 1) if lat else None
    out = {"github_e2e_sec": {"n": len(lat), "p50": q(0.5), "p95": q(0.95)},
           "providers": AIC.perf() if AIC else {}, "vs_rules": {}, "agreement": {}}
    for name, b in BENCH.items():
        pos, neg = b["tp"] + b["fn"], b["fp"] + b["tn"]
        out["vs_rules"][name] = {**b, "recall": round(b["tp"] / pos, 2) if pos else None,
                                 "false_alarm": round(b["fp"] / neg, 2) if neg else None}
    for k, (agree, tot) in BENCH_PAIR.items():
        out["agreement"][k] = {"agree": agree, "of": tot, "rate": round(agree / tot, 2) if tot else None}
    return out


def _http_code(exc):
    m = re.search(r"HTTP (\d{3})", str(exc))
    return f"http_{m.group(1)}" if m else type(exc).__name__


def apply_reset(state):
    """RESET_TOKEN جدید = «شروع تازه»: پیام‌های جامانده رد می‌شوند و هر منبع از «اکنون» ادامه می‌دهد (مثلاً بعد از عوض‌کردن کانال مقصد).
    هر مقدار فقط یک بار اعمال می‌شود (در state ثبت می‌شود)، پس ریستارت‌های بعدی چیزی را پاک نمی‌کنند."""
    tok = os.environ.get("RESET_TOKEN", "").strip()
    if not tok or state.data.get("reset_token") == tok:
        return False
    state.data["checkpoints"].clear()
    state.data["fails"].clear()
    state.data["reset_token"] = tok
    state.dirty = True
    state.save()
    log.info("Fresh start requested: backlog skipped, every source continues from now")
    return True


def bale_diag(bale, chat):
    """تشخیص علت: توکن (getMe) یا مقصد (getChat)؛ فقط کد وضعیت، بدون هیچ داده‌ی حساس."""
    out = {}
    for name, call in (("getMe", lambda: bale.get_me()), ("getChat", lambda: bale.get_chat(chat))):
        try:
            call(); out[name] = "ok"
        except Exception as exc:
            out[name] = _http_code(exc)
    return out


def classify(raw, text, state):
    """None = ارسال شود (همراه امضای محتوا)؛ وگرنه دلیلِ ارسال‌نشدن."""
    if not raw.strip():
        return "no_text", None
    if not text.strip():
        return "emptied_by_cleaner", None
    if is_ad(raw) or is_ad(text):
        return "ad", None
    if has_dropword(raw, settings.DROP_WORDS) or has_dropword(text, settings.DROP_WORDS):  # متن خام هم چک می‌شود (اسم‌هایی که پاک‌سازی حذف می‌کند)
        return "dropword", None
    if has_profanity(raw, settings.PROFANITY_MIN_LEVEL):
        return "profanity", None
    if not is_news(text):
        return "not_news", None
    sig = signature(text)
    now = time.time()
    entries = state.recent_seen(now)
    v2 = is_duplicate(sig, entries, now)
    v1 = is_duplicate_v1(sig, entries, now)
    if v1 and not v2:
        STATS["dup_v1_only"] += 1      # نسخه‌ی قدیمی «تکراری» می‌گفت، نسخه‌ی جدید نه (پیام نجات‌یافته)
    elif v2 and not v1:
        STATS["dup_v2_only"] += 1
    if (v1 if settings.DEDUPE_ENGINE == "v1" else v2):
        return "duplicate", None
    return None, sig


def project_snapshot(state, n_sources, n_failed, tg_lane):
    """وضعیت پروژه برای پنل تلگرام (فقط خواندنی؛ هیچ شمارنده‌ای تغییر نمی‌کند)."""
    tot = dict(STATS_BASE)
    for k, v in STATS.items():
        if isinstance(v, int):
            tot[k] = tot.get(k, 0) + v
    return {"totals": tot, "run": dict(STATS), "uptime_min": int((time.time() - STARTED) / 60),
            "sources": {"count": n_sources, "failed": n_failed}, "paused": bool(state.data.get("paused")),
            "bale": {"diag": BASE_STATUS.get("bale_diag"), "outage_active": time.monotonic() < OUTAGE["until"]},
            "tg_lane": tg_lane.status() if tg_lane else None,
            "ai_providers": ([{"name": p.name, "ok": True} for p in AIC.providers] + [{"name": p.name, "ok": False} for p in AIC.failed]) if AIC else []}


async def process_source(client, bale, state, entity, tmpdir, scrub=()):
    if state.data.get("paused"):       # توقف ارسال از پنل تلگرام؛ checkpoint جلو نمی‌رود (پس از ادامه، پیام‌های مجاز ارسال می‌شوند)
        return
    own = [getattr(entity, "username", None)] + [getattr(u, "username", None) for u in (getattr(entity, "usernames", None) or [])]
    text_only = any(settings.confuse(u) in settings.get_text_only() for u in own if u)
    key = key_for(entity)
    tag = key[:6]  # نام کانال در لاگ عمومی چاپ نمی‌شود
    names = [getattr(entity, "title", None), getattr(entity, "username", None), *scrub]
    last = state.last_id(key)
    if time.monotonic() < OUTAGE["until"]:
        return  # Bale در دسترس نیست؛ پیام‌ها در تلگرام می‌مانند و بعد از وصل شدن ارسال می‌شوند

    if last:
        messages = await fetch_new(client, entity, last)
    elif settings.BACKFILL_LIMIT > 0:
        messages = list(reversed(await client.get_messages(entity, limit=settings.BACKFILL_LIMIT)))
    else:
        latest = await client.get_messages(entity, limit=1)
        if latest:
            state.set_last_id(key, latest[0].id)
            log.info("First run for source %s: starting from now", tag)
        return

    for msg in messages:
        if msg.id <= last:
            continue
        raw = msg.message or ""
        text = clean_message(raw, getattr(msg, "entities", None), names, settings.BLOCKLIST)
        STATS["seen"] += 1
        too_old = (getattr(msg, "date", None) is not None and settings.MAX_AGE_HOURS > 0 and
                   (datetime.now(timezone.utc) - msg.date).total_seconds() > settings.MAX_AGE_HOURS * 3600)
        reason, sig = ("too_old", None) if too_old else classify(raw, text, state)
        analysed = False
        if reason in (None, "duplicate"):
            sig = sig or signature(text)
            reason, decided = await apply_semantic(reason, sig, text, state)
            if not decided:
                reason, analysed = await apply_analyst(reason, sig, text, state)
                if not analysed:
                    reason = await apply_ai(reason, sig, text, state)
        if reason is None and not analysed:
            reason = await apply_gate(text, state)
        if reason is None:
            reason = rate_limit_reason(GATE_IMP["v"], time.time())
        if audit_wanted(reason):
            t = asyncio.create_task(audit_task(text, reason, state)); _BG.add(t); t.add_done_callback(_BG.discard)
        bench_label = True if reason in ("dropword", "ad") else (False if reason is None else None)
        if bench_label is not None and bench_wanted():
            t = asyncio.create_task(bench_task(text, bench_label, state)); _BG.add(t); t.add_done_callback(_BG.discard)
        if reason:
            STATS[reason] += 1
            SINK.event(key[:12], msg.id, "rejected", reason, GATE_IMP["v"] if reason == "rate_cap" else None)
            brain_remember(key, msg, text, sig, "rejected", reason)
        else:
            try:
                await forward(bale, msg, text, tmpdir, text_only)
                state.add_seen(sig, time.time())
                RECENT.append({"w": set(sig["w"]), "n": set(sig["n"]), "p": set(sig.get("p", [])), "text": text[:700], "t": time.time()})
                del RECENT[:-40]
                STATS["sent"] += 1
                SINK.event(key[:12], msg.id, "sent", None, GATE_IMP["v"])
                brain_remember(key, msg, text, sig, "sent", None)
                SENT_TS.append(time.time())
                if SEM["vec"] is not None:
                    SEM["mem"].add(SEM["vec"], text, sig)
                if OUTAGE["since"]:
                    log.info("Bale is back after %d s", int(time.time() - OUTAGE["since"]))
                OUTAGE.update({"until": 0.0, "step": 0, "since": 0.0})
                if getattr(msg, "date", None):
                    LAT_E2E.append((datetime.now(timezone.utc) - msg.date).total_seconds() * 1000)
                log.info("Forwarded source=%s", tag)
            except FloodWaitError:
                raise
            except Exception as exc:
                STATS["failed"] += 1
                _c = _http_code(exc)
                SINK.event(key[:12], msg.id, "failed", _c or type(exc).__name__)
                # فقط نوع خطا و پیام کوتاهِ خود Bale/شبکه (بدون متن پیام یا اطلاعات منبع)
                STATS["last_fail"] = (type(exc).__name__ + ": " + re.sub(r"\d{6,}|https?://\S+", "?", str(exc))[:80])
                state.add_fail(key, msg.id)
                fails = state.fail_count(key, msg.id)
                if SERVICE_ERR.search(str(exc)):
                    # خطای سراسری (توکن/مقصد/شبکه/سرور Bale): پیام را رد نکن؛ عقب‌نشینی نمایی و تلاش مجدد
                    STATS["bale_outage"] += 1
                    OUTAGE["since"] = OUTAGE["since"] or time.time()
                    OUTAGE["step"] = min(OUTAGE["step"] + 1, 5)
                    OUTAGE["until"] = time.monotonic() + min(30 * 2 ** (OUTAGE["step"] - 1), 300)
                    log.error("Bale unavailable (%s); retry in %ds, message kept", _http_code(exc), int(OUTAGE["until"] - time.monotonic()))
                    if time.monotonic() - OUTAGE["diag_at"] > 600:
                        OUTAGE["diag_at"] = time.monotonic()
                        BASE_STATUS["bale_diag"] = await asyncio.to_thread(bale_diag, bale, settings.BALE_DESTINATION)
                    await alert(bale, state, "bale_down", f"Bale is unavailable ({_http_code(exc)}); messages are kept and will be retried.")
                    state.save()
                    if fails < settings.MAX_FAILS_SERVICE:
                        return
                log.error("Forward failed source=%s (%d/%d): %s", tag, fails,
                          settings.MAX_FAILS, type(exc).__name__)
                state.save()
                if not SERVICE_ERR.search(str(exc)) and fails < settings.MAX_FAILS:
                    return  # checkpoint جلو نمی‌رود؛ دور بعد دوباره تلاش می‌شود
                await alert(bale, state, "skipped",
                            f"a message was skipped after {settings.MAX_FAILS} failed sends (source {tag}).")
        state.clear_fail(key, msg.id)
        state.set_last_id(key, msg.id)
        state.save()


def write_status(info):
    """وضعیت غیرحساس برای عیب‌یابی (نوع خطاها) در کنار state ذخیره می‌شود."""
    try:
        path = os.path.join(os.path.dirname(settings.STATE_FILE) or ".", "status.json")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=1, sort_keys=True)
    except OSError:
        pass


async def resolve_sources(client, state):
    sources = settings.get_sources()
    status = {"dialogs": "ok", "count": len(sources), "sources": {}}
    failed = []
    try:
        await client.get_dialogs()  # کش کردن موجودیت‌ها (کانال‌های عضو‌شده بدون درخواست resolve پیدا می‌شوند)
    except Exception as exc:
        status["dialogs"] = type(exc).__name__
        log.warning("get_dialogs failed: %s", type(exc).__name__)
    entities = []
    for index, source in enumerate(sources, 1):
        try:
            invite = INVITE_RE.search(source)
            if invite:
                res = await client(CheckChatInviteRequest(invite.group(1)))
                if not isinstance(res, ChatInviteAlready):
                    raise ValueError("not a member")
                entity = res.chat
            else:
                target = int(source) if re.fullmatch(r"-?\d+", source) else source
                entity = await client.get_entity(target)
                await asyncio.sleep(0.5)  # فاصله بین resolveها (جلوگیری از FloodWait)
            entities.append(entity)
            status["sources"][str(index)] = "ok"
            log.info("Source #%d ready", index)
        except FloodWaitError as exc:
            failed.append(index)
            status["sources"][str(index)] = f"FloodWait {exc.seconds}s"
            log.warning("Source #%d flood wait %ss; retry next run", index, exc.seconds)
        except Exception as exc:
            failed.append(index)
            # فقط نوع خطا ثبت می‌شود، نه متن آن (متن خطا ممکن است یوزرنیم منبع را داشته باشد)
            status["sources"][str(index)] = type(exc).__name__
            log.error("Could not resolve source #%d: %s", index, type(exc).__name__)
    # حذف منبع بر اساس یوزرنیم/عنوان (SOURCES_REMOVE)، حتی اگر در SOURCES به شکل دیگری آمده باشد
    terms = [settings.confuse(t) for t in settings.get_remove_terms()]
    add_terms = settings.get_add_terms()
    keep = []
    for e in entities:
        unames = [getattr(e, "username", None)] + [getattr(u, "username", None) for u in (getattr(e, "usernames", None) or [])]
        unames = [settings.confuse(u) for u in unames if u]
        title = settings.confuse(getattr(e, "title", None) or "")
        if any(u in add_terms for u in unames):
            keep.append(e)   # منبعی که صریحاً با SOURCES_ADD آمده، حذف نمی‌شود
            continue
        if any(u in terms for u in unames) or any(len(t) >= 6 and t in title for t in terms) \
                or any(len(t) >= 6 and t in u for t in terms for u in unames):
            continue
        keep.append(e)
    status["removed_by_rule"] = len(entities) - len(keep)
    entities = keep
    state.save()
    BASE_STATUS.update(status)
    write_status(status)
    return entities, failed


async def changed_entities(client, state, entities):
    """با یک درخواست، کانال‌هایی را که پیام جدید دارند پیدا می‌کند؛ در صورت خطا همه را برمی‌گرداند."""
    try:
        tops = {}
        for i in range(0, len(entities), 90):
            chunk = entities[i:i + 90]
            peers = [InputDialogPeer(await client.get_input_entity(e)) for e in chunk]
            res = await client(GetPeerDialogsRequest(peers=peers))
            for d in res.dialogs:
                tops[utils.get_peer_id(d.peer)] = d.top_message
        out = []
        for e in entities:
            top = tops.get(utils.get_peer_id(e))
            last = state.last_id(key_for(e))
            if top is None or not last or top > last:
                out.append(e)
        return out
    except Exception as exc:
        log.warning("Peer-dialog precheck failed (%s); polling all", type(exc).__name__)
        return entities


async def run():
    required = ["TG_API_ID", "TG_API_HASH", "TG_SESSION", "BALE_BOT_TOKEN", "SOURCES"]
    missing = [n for n in required if not os.environ.get(n, "").strip()]
    if missing:
        log.error("Missing secrets: %s", ", ".join(missing))
        return 2

    client = TelegramClient(
        StringSession(os.environ["TG_SESSION"].strip()),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"].strip(),
    )
    bale = BaleClient(os.environ["BALE_BOT_TOKEN"].strip())
    state = State(settings.STATE_FILE)
    tg_lane = TelegramLane.from_env(state)       # مسیر دوم: ربات تلگرام → کانال تلگرام (اگر تنظیم شده باشد)
    tg_task = None
    if tg_lane:
        bale = FanoutBale(bale, tg_lane, settings.BALE_DESTINATION, settings.SIGNATURE, os.environ.get("TG_SIGNATURE", ""))
        tg_task = asyncio.create_task(tg_lane.run())
        log.info("Telegram lane enabled")
    SINK.dest = "bale+tg" if tg_lane else "bale"
    sink_task = asyncio.create_task(SINK.run()) if SINK.enabled else None
    if sink_task:
        log.info("Supabase sink enabled")
    brain_task = asyncio.create_task(BRAIN.run()) if BRAIN.enabled else None
    if brain_task:
        log.info("Admin brain enabled (mode=%s)", BRAIN.mode)
    tmpdir = tempfile.mkdtemp(prefix="balebot_")
    deadline = time.monotonic() + settings.LOOP_SECONDS

    promo_stop, promo_task = asyncio.Event(), None
    await client.connect()
    try:
        if not await client.is_user_authorized():
            log.error("Telegram session is not authorized. Regenerate TG_SESSION.")
            await alert(bale, state, "session", "the Telegram session is invalid. Regenerate TG_SESSION (python generate_session.py) and update the secret.")
            return 3
        entities, failed = await resolve_sources(client, state)
        if not entities:
            log.error("No source could be resolved.")
            await alert(bale, state, "nosources", "no source could be resolved. Check SOURCES and the Telegram membership.")
            return 4
        if failed:
            await alert(bale, state, "badsources",
                        f"{len(failed)} source(s) could not be resolved (positions in SOURCES: {', '.join(map(str, failed))}).")

        # رویداد تلگرام: به‌محض رسیدن پیام جدید حلقه بیدار می‌شود (تأخیر حدود ۱ ثانیه)
        wake = asyncio.Event()

        @client.on(events.NewMessage(chats=entities))
        async def _on_new_message(event):
            wake.set()

        global AIC
        AIC = ai_mod.AI()
        await asyncio.to_thread(AIC.init)
        BASE_STATUS["ai"] = AIC.info()
        apply_reset(state)
        restore_metrics(state)
        SEM["mode"] = os.environ.get("SEMANTIC_MODE", "on").strip().lower() or "on"
        ANALYST["on"] = (os.environ.get("ANALYST_MODE", "on").strip().lower() or "on") != "off"
        if SEM["mode"] != "off":
            emb = semantic.Embedder()
            await asyncio.to_thread(emb.init)
            SEM["emb"] = emb
            SEM["cal"] = semantic.Calibrator((state.data.get("metrics") or {}).get("sem_cal"))
            if emb.provider and tg_lane:
                asyncio.create_task(sem_backfill(client, tg_lane.chat))
        BASE_STATUS["bale_diag"] = await asyncio.to_thread(bale_diag, bale, settings.BALE_DESTINATION)
        scrub = collect_names(entities)
        panel, panel_tasks = None, []
        try:
            panel = await tg_panel.build_panel(client, state, lambda: project_snapshot(state, len(entities), len(failed), tg_lane), wake=wake.set)
        except Exception as exc:                 # noqa: BLE001  خرابی پنل هرگز نباید ارسال را مختل کند
            log.warning("Telegram panel disabled: %s", type(exc).__name__)
        if panel:
            panel.on_collect = SINK.channel_and_posts      # عکس ساعتی کانال و آمار پست‌ها → Supabase
            panel_tasks = [asyncio.create_task(panel.run()), asyncio.create_task(panel.snapshot_loop())]
            log.info("Telegram panel enabled (%d admin(s))", len(panel.admins))
        if settings.PROMO_LOOP == "on":
            promo_task = asyncio.create_task(promo_loop(bale, state, promo_stop, settings.BALE_DESTINATION,
                                                        settings.PROMO_TEXT, settings.PROMO_INTERVAL, settings.PROMO_VISIBLE))
            log.info("Promo loop is ON (interval=%ss, visible=%ss)", settings.PROMO_INTERVAL, settings.PROMO_VISIBLE)
        last_ping = 0.0
        last_revive = time.monotonic()
        wake.set()  # دور اول: بررسی فوری برای جبران پیام‌های زمان خاموشی
        while True:
            wake.clear()
            for entity in await changed_entities(client, state, entities):
                try:
                    await process_source(client, bale, state, entity, tmpdir, scrub)
                except FloodWaitError as exc:
                    log.warning("Telegram flood wait: %ss", exc.seconds)
                    if exc.seconds > max(0, deadline - time.monotonic()):
                        state.save()
                        return 0
                    await asyncio.sleep(exc.seconds + 1)
                except Exception as exc:
                    log.error("Source %s failed: %s", key_for(entity)[:6], type(exc).__name__)
            state.save()
            totals = snapshot_metrics(state)
            if tg_lane:
                BASE_STATUS["tg"] = tg_lane.status()
            if panel:
                BASE_STATUS["panel"] = panel.status()
            if BRAIN.enabled:
                BASE_STATUS["brain"] = BRAIN.status_info()
            if SINK.enabled:
                BASE_STATUS["supabase"] = SINK.status_info()
                SINK.status(os.environ.get("GITHUB_RUN_ID"), int((time.time() - STARTED) / 60), STATS,
                            {"tg": tg_lane.status() if tg_lane else None, "paused": bool(state.data.get("paused"))})
            write_status({**BASE_STATUS, "stats": dict(STATS), "stats_total": totals, "sem": sem_info(), "bale_outage": ({"since_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(OUTAGE["since"])), "retry_in_s": max(0, int(OUTAGE["until"] - time.monotonic()))} if OUTAGE["since"] else None),
                          "since_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(SINCE["t"] or time.time())), "ai_stats": dict(AIC.stats) if AIC else {}, "compare": compare_report(), "uptime_min": int((time.time() - STARTED) / 60)})
            if AIC is not None and AIC.failed and time.monotonic() - last_revive >= 600:
                last_revive = time.monotonic()
                back = await asyncio.to_thread(AIC.revive)     # سرویسی که موقتاً (۵۰۳/۴۲۹) افتاده بود برمی‌گردد
                if back:
                    BASE_STATUS["ai"] = AIC.info()
                    log.info("AI provider(s) back: %s", ",".join(back))
            if settings.HEALTHCHECK_URL and time.monotonic() - last_ping >= 240:
                last_ping = time.monotonic()
                await asyncio.to_thread(ping_healthcheck)
            if time.monotonic() + settings.POLL_INTERVAL >= deadline:
                break
            # تا رویداد جدید بیاید منتظر می‌ماند؛ POLL_INTERVAL فقط پشتیبان است
            try:
                await asyncio.wait_for(wake.wait(), timeout=settings.POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
        return 0
    finally:
        try:
            promo_stop.set()
            if promo_task:
                await asyncio.wait_for(promo_task, timeout=20)  # پیامِ در حال نمایش پاک شود
        except Exception:
            pass
        if panel_tasks:
            panel.stop()
            for t_ in panel_tasks:
                t_.cancel()
        if sink_task:                            # ارسال نهایی صف Supabase (حداکثر ۱۵ ثانیه)
            SINK.stop()
            try:
                await asyncio.wait_for(asyncio.to_thread(SINK.flush_sync), timeout=15)
            except Exception:
                pass
            sink_task.cancel()
        if brain_task:                           # ارسال نهایی صف حافظه‌ی چندروزه (حداکثر ۱۵ ثانیه)
            BRAIN.stop()
            try:
                await asyncio.wait_for(asyncio.to_thread(BRAIN.flush_sync), timeout=15)
            except Exception:
                pass
            brain_task.cancel()
        if tg_lane:                              # صف تلگرام تا ۳۰ ثانیه فرصت می‌گیرد خالی شود؛ سپس tg_seen ذخیره می‌شود
            try:
                for _ in range(30):
                    if not tg_lane.status()["queued"]:
                        break
                    await asyncio.sleep(1)
                tg_lane.stop()
                await asyncio.wait_for(tg_task, timeout=10)
            except Exception:
                pass
        state.save()
        await client.disconnect()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_bale():
    """یک پیام آزمایشی به مقصد بله می‌فرستد و نتیجه را در status.json می‌نویسد."""
    token = os.environ.get("BALE_BOT_TOKEN", "").strip()
    result = "ok"
    try:
        BaleClient(token).send_text(settings.BALE_DESTINATION, "✅ تست اتصال Balebot — این پیام آزمایشی است.")
    except Exception as exc:
        result = f"{type(exc).__name__}: {exc}"[:160]
    write_status({"bale_test": result})
    log.info("Bale test: %s", "ok" if result == "ok" else "failed")
    return 0 if result == "ok" else 5


if __name__ == "__main__":
    if "--test-bale" in sys.argv:
        sys.exit(test_bale())
    sys.exit(asyncio.run(run()))
