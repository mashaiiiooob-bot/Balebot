"""بررسی دوره‌ای کانال‌های تلگرام و ارسال خبرها به بله (برای GitHub Actions)."""
import asyncio
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
from dedupe import signature, is_duplicate
import ai as ai_mod
from state import State

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("balebot")

# شمارنده‌های بی‌نام (فقط عدد) برای عیب‌یابی؛ در status.json نوشته می‌شود
STATS = {"seen": 0, "no_text": 0, "emptied_by_cleaner": 0, "ad": 0, "dropword": 0, "profanity": 0, "not_news": 0, "duplicate": 0, "sent": 0, "failed": 0,
         "ai_calls": 0, "ai_errors": 0, "ai_skipped": 0, "ai_rescued": 0, "ai_caught": 0,
         "sh_dup_ai_dup": 0, "sh_dup_ai_new": 0, "sh_new_ai_dup": 0, "sh_new_ai_new": 0}
AIC = None
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


async def forward(bale, message, text, tmpdir):
    caption = build_caption(text)
    media_path = None
    try:
        size = getattr(message.file, "size", 0) or 0
        if message.media and size <= settings.MAX_FILE_MB * 1024 * 1024:
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


def ai_candidates(sig, now):
    a, an = set(sig["w"]), set(sig["n"])
    out = []
    for e in RECENT:
        if now - e["t"] > 21600 or (an and e["n"] and not (an <= e["n"] or e["n"] <= an)):
            continue
        inter = len(a & e["w"])
        if inter < 3:
            continue
        out.append((inter / min(len(a), len(e["w"])), inter / len(a | e["w"]), e))
    out.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return out[:3]


async def apply_ai(reason, sig, text, state):
    """فقط موارد مرزی به هوش مصنوعی می‌رسد. حالت shadow: ثبت می‌کند ولی تصمیم را عوض نمی‌کند."""
    if AIC is None or AIC.mode == "off" or reason not in (None, "duplicate"):
        return reason
    cands = ai_candidates(sig, time.time())
    if not cands:
        return reason
    ov, jc, _ = cands[0]
    if (ov >= 0.9 and jc >= 0.7) or ov < 0.45:
        return reason  # یا قطعاً تکراری، یا آشکارا متفاوت
    if not AIC.allow(state):
        STATS["ai_skipped"] += 1
        return reason
    STATS["ai_calls"] += 1
    verdict = await asyncio.to_thread(AIC.judge_duplicate, text, [c[2]["text"] for c in cands])
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
    if is_duplicate(sig, state.recent_seen(time.time()), time.time()):
        return "duplicate", None
    return None, sig


async def process_source(client, bale, state, entity, tmpdir, scrub=()):
    key = key_for(entity)
    tag = key[:6]  # نام کانال در لاگ عمومی چاپ نمی‌شود
    names = [getattr(entity, "title", None), getattr(entity, "username", None), *scrub]
    last = state.last_id(key)

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
        reason, sig = classify(raw, text, state)
        if reason in (None, "duplicate"):
            sig = sig or signature(text)
            reason = await apply_ai(reason, sig, text, state)
        if reason:
            STATS[reason] += 1
        else:
            try:
                await forward(bale, msg, text, tmpdir)
                state.add_seen(sig, time.time())
                RECENT.append({"w": set(sig["w"]), "n": set(sig["n"]), "text": text[:700], "t": time.time()})
                del RECENT[:-40]
                STATS["sent"] += 1
                log.info("Forwarded source=%s", tag)
            except FloodWaitError:
                raise
            except Exception as exc:
                STATS["failed"] += 1
                state.add_fail(key, msg.id)
                fails = state.fail_count(key, msg.id)
                log.error("Forward failed source=%s (%d/%d): %s", tag, fails,
                          settings.MAX_FAILS, type(exc).__name__)
                state.save()
                if fails < settings.MAX_FAILS:
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
    tmpdir = tempfile.mkdtemp(prefix="balebot_")
    deadline = time.monotonic() + settings.LOOP_SECONDS

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
        scrub = collect_names(entities)
        last_ping = 0.0
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
            write_status({**BASE_STATUS, "stats": dict(STATS), "uptime_min": int((time.time() - STARTED) / 60)})
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
