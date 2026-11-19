"""کنترل پنل تلگرام برای ربات: آمار کانال (بازدید، ری‌اکشن، فوروارد، ...) + فعالیت پروژه + کنترل ارسال.

- Bot API فقط رابط کاربری است؛ داده‌ی کانال را حساب کاربری (Telethon) می‌خواند (tg_stats).
- فقط ادمین‌ها (حساب صاحب TG_SESSION + شناسه‌های TG_ADMIN_IDS) پاسخ می‌گیرند؛ بقیه بی‌پاسخ نادیده گرفته می‌شوند
  (به‌جز /id که فقط شناسه‌ی خودِ فرستنده را می‌گوید).
- بدون میزبان: داخل همان اجرای GitHub Actions با getUpdates (long polling) کار می‌کند.
"""
import asyncio
import collections
import html
import os
import time

import requests

import tg_stats
from telegram_out import TgError

COMMANDS = [("start", "منوی اصلی"), ("stats", "آمار کانال"), ("top", "پست‌های برتر"), ("project", "فعالیت پروژه"),
            ("health", "سلامت سرویس‌ها"), ("id", "شناسه‌ی عددی من")]


# ------------------------------------------------------------------ Bot API
class PanelBot:
    def __init__(self, token, session=None):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.session = session or requests.Session()

    def _post(self, method, payload, timeout=30):
        try:
            r = self.session.post(self.base + method, json=payload, timeout=timeout)
        except requests.RequestException as exc:       # پیام requests شامل URL/توکن است؛ چاپ نمی‌شود
            raise TgError(f"Telegram network error on {method}: {type(exc).__name__}") from None
        try:
            body = r.json()
        except ValueError:
            raise TgError(f"Telegram invalid response ({r.status_code}) on {method}", status=r.status_code) from None
        if isinstance(body, dict) and body.get("ok"):
            return body.get("result")
        params = (body.get("parameters") or {}) if isinstance(body, dict) else {}
        try:
            ra = max(0, int(params.get("retry_after", 0)))
        except (TypeError, ValueError):
            ra = 0
        raise TgError(f"Telegram HTTP {r.status_code} on {method}", status=r.status_code, retry_after=ra)

    def get_updates(self, offset=0, timeout=25):
        p = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset:
            p["offset"] = offset
        return self._post("getUpdates", p, timeout=timeout + 15) or []

    def send(self, chat, text, markup=None):
        p = {"chat_id": chat, "text": clip(text), "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup:
            p["reply_markup"] = markup
        return self._post("sendMessage", p)

    def edit(self, chat, message_id, text, markup=None):
        p = {"chat_id": chat, "message_id": message_id, "text": clip(text), "parse_mode": "HTML", "disable_web_page_preview": True}
        if markup:
            p["reply_markup"] = markup
        return self._post("editMessageText", p)

    def answer(self, cb_id, text=None, alert=False):
        p = {"callback_query_id": cb_id}
        if text:
            p.update({"text": text[:190], "show_alert": bool(alert)})
        return self._post("answerCallbackQuery", p)

    def delete_webhook(self):
        return self._post("deleteWebhook", {"drop_pending_updates": False})

    def set_commands(self, commands=COMMANDS):
        return self._post("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in commands]})


# ------------------------------------------------------------------ قالب‌بندی
def clip(text, limit=4000):
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 20)
    return text[: cut if cut > limit // 2 else limit - 20] + "\n…"


def esc(s):
    return html.escape(str(s), quote=False)


def n(x):
    return "—" if x is None else f"{int(x):,}"


def sgn(x):
    return "—" if x is None else f"{int(x):+,}"


def pct(a, b):
    return "—" if not b else f"{100 * a / b:.0f}٪"


def btn(text, data):
    assert len(data.encode()) <= 64, data     # محدودیت callback_data در تلگرام
    return {"text": text, "callback_data": data}


PERIODS = [("۲۴س", 1), ("۷ روز", 7), ("۳۰ روز", 30), ("کل", 0)]


def _days(v):
    v = int(v)
    return None if v == 0 else v


def period_row(prefix, cur, suffix=""):
    return [btn(("✅ " if int(cur) == d else "") + label, f"{prefix}:{d}{suffix}") for label, d in PERIODS]


def nav_row(extra=None):
    row = list(extra or [])
    return row + [btn("🏠 منو", "m"), btn("🔄", "rf")]


def title_of(ch):
    return esc(ch.get("title") or ch.get("username") or "کانال")


def period_name(days):
    return {1: "۲۴ ساعت اخیر", 7: "۷ روز اخیر", 30: "۳۰ روز اخیر", None: "کل پست‌های بررسی‌شده"}.get(days, f"{days} روز")


def render_main(data, s1, s7, growth, paused):
    ch = data["channel"]
    g = " | ".join(f"{lbl}: {sgn(growth.get(d))}" for lbl, d in (("۲۴س", 1), ("۷روز", 7), ("۳۰روز", 30)))
    text = (f"<b>🎛 کنترل پنل — {title_of(ch)}</b>\n"
            f"👥 مشترکین: <b>{n(ch.get('subs'))}</b>  ({g})\n\n"
            f"<b>۲۴ ساعت اخیر</b>\n📝 {n(s1['posts'])} پست | 👁 {n(s1['views_total'])} بازدید | 😀 {n(s1['reactions_total'])} ری‌اکشن | ↗️ {n(s1['forwards_total'])} فوروارد\n"
            f"<b>۷ روز اخیر</b>\n📝 {n(s7['posts'])} پست | 👁 {n(s7['views_total'])} (میانگین {n(s7['views_avg'])}) | 😀 {n(s7['reactions_total'])} | ↗️ {n(s7['forwards_total'])}\n\n"
            f"ارسال خودکار: {'⏸ <b>متوقف</b>' if paused else '▶️ فعال'}")
    kb = [[btn("📊 نمای کلی کانال", "ch:7"), btn("🔥 پست‌های برتر", "top:7:v")],
          [btn("😀 ری‌اکشن‌ها", "rx:7"), btn("↗️ فوروارد و پاسخ", "fw:7")],
          [btn("🕒 بهترین زمان انتشار", "hr:30"), btn("📈 آمار پیشرفته", "adm")],
          [btn("🤖 فعالیت پروژه", "pj"), btn("🩺 سلامت", "hl")],
          [btn("⏯ کنترل ارسال", "ctl"), btn("🔄 به‌روزرسانی", "rf")]]
    return text, kb


def render_channel(data, s, growth, days):
    ch = data["channel"]
    k = " | ".join(f"{a}: {b}" for a, b in s["kinds"].items())
    mature = f"\n   (پست‌های بالای ۲۴ ساعت: میانگین {n(s['views_avg_mature'])})" if s["views_avg_mature"] is not None and days != 1 else ""
    text = (f"<b>📊 نمای کلی — {period_name(days)}</b>\n"
            f"👥 مشترکین: <b>{n(ch.get('subs'))}</b>\n"
            f"📝 پست‌ها: <b>{n(s['posts'])}</b>  (روزانه {s['posts_per_day']})\n"
            f"👁 بازدید کل: <b>{n(s['views_total'])}</b>\n"
            f"   میانگین {n(s['views_avg'])} | میانه {n(s['views_median'])} | بیشترین {n(s['views_max'])}{mature}\n"
            f"😀 ری‌اکشن: <b>{n(s['reactions_total'])}</b> (میانگین {s['reactions_avg']} در پست)\n"
            f"↗️ فوروارد: <b>{n(s['forwards_total'])}</b> (میانگین {s['forwards_avg']})\n"
            f"💬 پاسخ/کامنت: {n(s['replies_total'])}\n"
            f"📐 نرخ تعامل (ری‌اکشن+فوروارد+پاسخ ÷ بازدید): <b>{s['er_pct']}٪</b>\n"
            f"🗂 نوع پست‌ها: {k or '—'}")
    if growth:
        text += "\n👥 رشد: " + " | ".join(f"{lbl} {sgn(growth.get(d))}" for lbl, d in (("۲۴س", 1), ("۷روز", 7), ("۳۰روز", 30)))
    return text, [period_row("ch", days or 0), nav_row()]


METRIC = {"v": ("views", "👁 بازدید"), "r": ("reactions", "😀 ری‌اکشن"), "f": ("forwards", "↗️ فوروارد")}


def render_top(s, days, m):
    key, label = METRIC[m]
    rows = s[{"v": "top_views", "r": "top_reactions", "f": "top_forwards"}[m]]
    lines = [f"<b>🔥 پست‌های برتر — {label} — {period_name(days)}</b>"]
    if not rows:
        lines.append("\nدر این بازه داده‌ای نیست.")
    for i, p in enumerate(rows, 1):
        val = sum(p["reactions"].values()) if key == "reactions" else p[key]
        txt = esc(p["text"]) or "(بدون متن)"
        link = f' <a href="{p["link"]}">باز کردن</a>' if p.get("link") else ""
        lines.append(f"\n{i}. <b>{n(val)}</b> — 👁 {n(p['views'])} | 😀 {n(sum(p['reactions'].values()))} | ↗️ {n(p['forwards'])}\n   {txt}{link}")
    kb = [[btn(("✅ " if m == c else "") + METRIC[c][1], f"top:{days or 0}:{c}") for c in "vrf"], period_row("top", days or 0, f":{m}"), nav_row()]
    return "\n".join(lines), kb


def render_reactions(s, days):
    tot = s["reactions_total"]
    lines = [f"<b>😀 ری‌اکشن‌ها — {period_name(days)}</b>", f"\nمجموع: <b>{n(tot)}</b> | میانگین در هر پست: {s['reactions_avg']}"]
    if not s["by_emoji"]:
        lines.append("\nهنوز ری‌اکشنی ثبت نشده است.")
    for e, c in s["by_emoji"].items():
        lines.append(f"{e}  {n(c)}  ({pct(c, tot)})  {'█' * max(1, round(10 * c / tot))}")
    lines.append(f"\nسهم ری‌اکشن از بازدید: {round(100 * tot / s['views_total'], 2) if s['views_total'] else 0}٪")
    return "\n".join(lines), [period_row("rx", days or 0), nav_row()]


def render_forwards(s, days):
    lines = [f"<b>↗️ فوروارد و پاسخ — {period_name(days)}</b>",
             f"\nفوروارد: <b>{n(s['forwards_total'])}</b> (میانگین {s['forwards_avg']} در پست)",
             f"نسبت فوروارد به بازدید: {round(100 * s['forwards_total'] / s['views_total'], 2) if s['views_total'] else 0}٪",
             f"پاسخ/کامنت: {n(s['replies_total'])}", "\n<b>بیشترین فوروارد</b>"]
    if not s["top_forwards"]:
        lines.append("—")
    for i, p in enumerate(s["top_forwards"][:3], 1):
        link = f' <a href="{p["link"]}">باز کردن</a>' if p.get("link") else ""
        lines.append(f"{i}. ↗️ {n(p['forwards'])} | 👁 {n(p['views'])} — {esc(p['text']) or '(بدون متن)'}{link}")
    return "\n".join(lines), [period_row("fw", days or 0), nav_row()]


def render_hours(s, days):
    h = "\n".join(f"{hh:02d}:00 — میانگین {n(a)} بازدید ({c} پست)" for hh, a, c in s["best_hours"]) or "داده‌ی کافی نیست (حداقل ۲ پست در هر ساعت)"
    w = "\n".join(f"{name} — میانگین {n(a)} بازدید ({c} پست)" for name, a, c in s["best_weekdays"]) or "داده‌ی کافی نیست"
    text = (f"<b>🕒 بهترین زمان انتشار — {period_name(days)}</b>\n(ساعت تهران)\n\n<b>ساعت‌ها</b>\n{h}\n\n<b>روزهای هفته</b>\n{w}\n\n"
            f"آهنگ انتشار: {s['posts_per_day']} پست در روز")
    return text, [period_row("hr", days or 0), nav_row()]


def render_admin(adm, growth, subs):
    lines = ["<b>📈 آمار پیشرفته‌ی تلگرام</b>"]
    if adm and adm.get("available"):
        def line(lbl, v):
            return f"{lbl}: <b>{n(v['current'])}</b> (دوره‌ی قبل {n(v['previous'])})" if v else f"{lbl}: —"
        lines += [line("مشترکین", adm["followers"]), line("بازدید در هر پست", adm["views_per_post"]), line("اشتراک‌گذاری در هر پست", adm["shares_per_post"]),
                  line("ری‌اکشن در هر پست", adm["reactions_per_post"]), f"اعلان فعال: {adm['notifications_pct'] if adm['notifications_pct'] is not None else '—'}٪"]
    else:
        reason = (adm or {}).get("reason", "نامشخص")
        lines += [f"در دسترس نیست ({esc(reason)}).", "برای این آمار، حساب کاربری باید در کانال <b>ادمین با دسترسی مشاهده‌ی آمار</b> باشد و کانال به تعداد حداقلی مشترک رسیده باشد."]
    lines.append("\n<b>رشد مشترکین (از نمونه‌های ساعتی خود ربات)</b>")
    lines.append(" | ".join(f"{lbl}: {sgn(growth.get(d))}" for lbl, d in (("۲۴س", 1), ("۷روز", 7), ("۳۰روز", 30))))
    if all(growth.get(d) is None for d in (1, 7, 30)):
        lines.append("(تاریخچه هنوز کافی نیست؛ هر ساعت یک نمونه ثبت می‌شود.)")
    return "\n".join(lines), [nav_row()]


def render_project(p):
    t, run = p.get("totals") or {}, p.get("run") or {}
    sent, seen = t.get("sent", 0), t.get("seen", 0)
    lines = ["<b>🤖 فعالیت پروژه</b>",
             f"\nاجرای فعلی: {p.get('uptime_min', 0) // 60} ساعت و {p.get('uptime_min', 0) % 60} دقیقه | منابع: {n((p.get('sources') or {}).get('count'))}"
             f" (ناموفق: {n((p.get('sources') or {}).get('failed', 0))})",
             "\n<b>جمع از ابتدای ثبت</b>",
             f"دیده‌شده: {n(seen)} | ارسال‌شده: <b>{n(sent)}</b> ({pct(sent, seen)})",
             f"حذف‌شده — تبلیغ: {n(t.get('ad'))} | واژه‌ی ممنوعه: {n(t.get('dropword'))} | فحش: {n(t.get('profanity'))} | تکراری: {n(t.get('duplicate'))}",
             f"غیرخبری: {n(t.get('not_news'))} | بدون متن: {n(t.get('no_text'))} | قدیمی: {n(t.get('too_old'))} | ناموفق: {n(t.get('failed'))}",
             "\n<b>همین اجرا</b>",
             f"دیده‌شده: {n(run.get('seen'))} | ارسال‌شده: {n(run.get('sent'))} | ناموفق: {n(run.get('failed'))}",
             "\n<b>هوش مصنوعی</b>",
             f"فراخوانی: {n(t.get('ai_calls', 0) + t.get('gate_calls', 0))} | وتو (تبلیغ/ممنوعه): {n(t.get('ai_ad', 0) + t.get('ai_banned', 0))} | نجات خبر: {n(t.get('ai_rescued'))} | خطا: {n(t.get('ai_errors', 0) + t.get('gate_errors', 0))}"]
    lane = p.get("tg_lane")
    if lane:
        lines += ["\n<b>ارسال به تلگرام</b>", f"ارسال‌شده: {n(lane.get('sent'))} | در صف: {n(lane.get('queued'))} | تلاش ناموفق: {n(lane.get('failed_attempts'))}"]
    return "\n".join(lines), [nav_row([btn("🩺 سلامت", "hl")])]


def render_health(p, panel_status):
    bale = p.get("bale") or {}
    diag = bale.get("diag") or {}
    lane = p.get("tg_lane") or {}
    ok = lambda b: "✅" if b else "❌"
    lines = ["<b>🩺 سلامت سرویس‌ها</b>",
             f"\nبله — توکن: {ok(diag.get('getMe') == 'ok')} | مقصد: {ok(diag.get('getChat') == 'ok')}" + ("  ⚠️ قطعی فعال" if bale.get("outage_active") else ""),
             f"تلگرام (کانال) — آخرین ارسال موفق: {lane.get('last_ok_utc') or '—'} | آخرین خطا: {lane.get('last_error') or '—'}",
             f"ارسال خودکار: {'⏸ متوقف' if p.get('paused') else '▶️ فعال'}",
             f"پنل: {n(panel_status.get('updates'))} دستور، {n(panel_status.get('errors'))} خطا"]
    ai = p.get("ai_providers") or []
    if ai:
        lines.append("هوش مصنوعی: " + " | ".join(f"{a['name']} {ok(a.get('ok'))}" for a in ai))
    return "\n".join(lines), [nav_row()]


def render_control(paused):
    if paused:
        return "<b>⏯ کنترل ارسال</b>\nوضعیت: ⏸ <b>متوقف</b>\nپیام‌ها پردازش نمی‌شوند؛ پس از ادامه، پیام‌های جدیدتر از مهلت (MAX_AGE) ارسال می‌شوند.", [[btn("▶️ ادامه‌ی ارسال", "resume")], nav_row()]
    return "<b>⏯ کنترل ارسال</b>\nوضعیت: ▶️ فعال", [[btn("⏸ توقف ارسال", "pause")], nav_row()]


def render_confirm_pause():
    return ("<b>⚠️ توقف ارسال؟</b>\nتا زمان «ادامه»، هیچ پیامی به بله و تلگرام ارسال نمی‌شود و پیام‌های قدیمی‌تر از مهلت ممکن است از دست بروند.",
            [[btn("✅ بله، متوقف کن", "pause_y"), btn("❌ انصراف", "ctl")]])


# ------------------------------------------------------------------ پنل
class Panel:
    def __init__(self, bot, admins, state, client, channel, project_fn, collector=None, admin_collector=None, wake=None,
                 clock=time.time, cache_ttl=300, snapshot_every=3600):
        self.bot, self.admins, self.state, self.client, self.channel = bot, {int(a) for a in admins}, state, client, channel
        self.project_fn, self.wake, self.clock, self.ttl = project_fn, wake, clock, cache_ttl
        self.collector = collector or tg_stats.collect
        self.admin_collector = admin_collector or (lambda c, ch: _admin_via_entity(c, ch))
        self.snap = tg_stats.Snapshots(state)
        self.snapshot_every = snapshot_every
        self.counters = collections.Counter()
        self.last_error = None
        self.summary = None            # خلاصه‌ی آخرین دریافت موفق (فقط اعداد) برای status.json
        self._cache = None
        self._admin_cache = None
        self._lock = asyncio.Lock()
        self._stop = False

    # ---- داده
    async def data(self, force=False):
        async with self._lock:
            now = self.clock()
            if self._cache and not (force and now - self._cache["at"] > 20) and now - self._cache["at"] < self.ttl:
                return self._cache["d"]
            try:
                d = await self.collector(self.client, self.channel, 500, False)
                self._cache = {"at": now, "d": d}
                self.last_error = None
                ch = d.get("channel") or {}
                self.summary = {"posts": len(d.get("posts") or []), "subs": ch.get("subs"), "full_error": ch.get("full_error"),
                                "fetched_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(now))}
                self.snap.record(ch.get("subs"), now)
                return d
            except Exception as exc:                  # noqa: BLE001
                self.last_error = type(exc).__name__
                self.counters["collect_errors"] += 1
                if self._cache:
                    return self._cache["d"]
                raise

    async def admin_data(self, force=False):
        now = self.clock()
        if self._admin_cache and not force and now - self._admin_cache[0] < 1800:
            return self._admin_cache[1]
        try:
            a = await self.admin_collector(self.client, self.channel)
        except Exception as exc:                      # noqa: BLE001
            a = {"available": False, "reason": type(exc).__name__}
        self._admin_cache = (now, a)
        return a

    def growth(self, subs):
        return {d: self.snap.delta(d, self.clock(), subs) for d in (1, 7, 30)}

    def paused(self):
        return bool(self.state.data.get("paused"))

    # ---- صفحه‌ها
    async def page(self, key, force=False):
        """key مثل 'ch:7' → (text, keyboard)."""
        parts = key.split(":")
        kind = parts[0]
        if kind in ("pj", "hl", "ctl", "pause", "pause_y", "resume"):
            if kind == "pause":
                return render_confirm_pause()
            if kind == "pause_y":
                self.state.data["paused"] = True
                self.state.dirty = True
                return render_control(True)
            if kind == "resume":
                self.state.data["paused"] = False
                self.state.dirty = True
                if self.wake:
                    self.wake()
                return render_control(False)
            if kind == "ctl":
                return render_control(self.paused())
            p = self.project_fn()
            return render_project(p) if kind == "pj" else render_health(p, self.status())
        d = await self.data(force)
        posts, ch = d["posts"], d["channel"]
        now = self.clock()
        if kind == "adm":
            return render_admin(await self.admin_data(force), self.growth(ch.get("subs")), ch.get("subs"))
        days = _days(parts[1]) if len(parts) > 1 else 7
        s = tg_stats.summarize(posts, now, days, ch.get("username"))
        if kind == "ch":
            return render_channel(d, s, self.growth(ch.get("subs")), days)
        if kind == "top":
            return render_top(s, days, parts[2] if len(parts) > 2 and parts[2] in METRIC else "v")
        if kind == "rx":
            return render_reactions(s, days)
        if kind == "fw":
            return render_forwards(s, days)
        if kind == "hr":
            return render_hours(s, days)
        s1, s7 = tg_stats.summarize(posts, now, 1, ch.get("username")), tg_stats.summarize(posts, now, 7, ch.get("username"))
        return render_main(d, s1, s7, self.growth(ch.get("subs")), self.paused())

    # ---- دستورها و دکمه‌ها
    async def _send(self, fn, *a):
        return await asyncio.to_thread(fn, *a)

    def is_admin(self, user):
        return int((user or {}).get("id", 0) or 0) in self.admins

    async def on_message(self, msg):
        chat = msg.get("chat") or {}
        text = (msg.get("text") or "").strip()
        if chat.get("type") != "private" or not text.startswith("/"):
            return
        cmd = text.split()[0].split("@")[0].lower()
        user = msg.get("from") or {}
        if cmd == "/id":
            await self._send(self.bot.send, chat["id"], f"شناسه‌ی عددی شما: <code>{int(user.get('id', 0))}</code>")
            return
        if not self.is_admin(user):
            self.counters["unauthorized"] += 1
            return
        key = {"/start": "m", "/menu": "m", "/help": "m", "/stats": "ch:7", "/top": "top:7:v", "/project": "pj", "/health": "hl"}.get(cmd)
        if key is None:
            await self._send(self.bot.send, chat["id"], "دستور ناشناخته. /start")
            return
        try:
            text_, kb = await self.page(key)
        except Exception as exc:                      # noqa: BLE001
            text_, kb = f"⚠️ دریافت آمار ناموفق بود ({esc(type(exc).__name__)}). کمی بعد دوباره تلاش کن.", [[btn("🔄 تلاش دوباره", "m")]]
        await self._send(self.bot.send, chat["id"], text_, {"inline_keyboard": kb})

    async def on_callback(self, cq):
        cid, user = cq.get("id"), cq.get("from")
        if not self.is_admin(user):
            self.counters["unauthorized"] += 1
            await self._send(self.bot.answer, cid, "دسترسی ندارید.", True)
            return
        msg = cq.get("message") or {}
        key = (cq.get("data") or "m")
        force = key == "rf"
        if force:
            key = (self.state.data.get("panel_last", {}) or {}).get(str(msg.get("message_id")), "m")
        try:
            text_, kb = await self.page(key, force)
            self.state.data.setdefault("panel_last", {})[str(msg.get("message_id"))] = key
            panel_last = self.state.data["panel_last"]
            if len(panel_last) > 50:
                for k in list(panel_last)[:-50]:
                    panel_last.pop(k, None)
            self.state.dirty = True
        except Exception as exc:                      # noqa: BLE001
            await self._send(self.bot.answer, cid, f"خطا در دریافت آمار: {type(exc).__name__}", True)
            return
        try:
            await self._send(self.bot.edit, (msg.get("chat") or {}).get("id"), msg.get("message_id"), text_, {"inline_keyboard": kb})
        except TgError:
            pass                                       # «message is not modified» و مشابه
        await self._send(self.bot.answer, cid, "به‌روز شد ✓" if force else None)

    async def handle(self, update):
        self.counters["updates"] += 1
        if "message" in update:
            await self.on_message(update["message"])
        elif "callback_query" in update:
            await self.on_callback(update["callback_query"])

    # ---- حلقه‌ها
    async def run(self):
        for fn in (self.bot.delete_webhook, self.bot.set_commands):
            try:
                await self._send(fn)
            except TgError:
                pass
        backoff = 1
        while not self._stop:
            offset = int(self.state.data.get("panel_offset", 0))
            try:
                ups = await self._send(self.bot.get_updates, offset, 25)
                self.counters["polls"] += 1
                backoff = 1
            except TgError as exc:
                self.counters["errors"] += 1
                self.last_error = f"http_{exc.status}" if exc.status else "network"
                await asyncio.sleep(max(exc.retry_after, min(60, backoff)))
                backoff *= 2
                continue
            for u in ups:
                uid = int(u.get("update_id", 0))
                self.state.data["panel_offset"] = max(offset, uid + 1)
                offset = self.state.data["panel_offset"]
                self.state.dirty = True
                # پس از ری‌استارت، دستورهای قدیمی (>۲ دقیقه) اجرا نمی‌شوند
                m = u.get("message") or (u.get("callback_query") or {}).get("message") or {}
                if m.get("date") and self.clock() - int(m["date"]) > 120 and "callback_query" not in u:
                    continue
                try:
                    await self.handle(u)
                except Exception as exc:               # noqa: BLE001
                    self.counters["errors"] += 1
                    self.last_error = type(exc).__name__

    async def snapshot_loop(self):
        """هر ساعت یک نمونه‌ی مشترکین ثبت می‌شود تا رشد قابل محاسبه باشد (حتی اگر کسی پنل را باز نکند)."""
        first = True
        while not self._stop:
            try:
                await self.data(force=True)
                if first:                               # یک‌بار بررسی در دسترس بودن آمار پیشرفته (نتیجه در status.json)
                    await self.admin_data()
                    first = False
            except Exception:                           # noqa: BLE001
                pass
            for _ in range(int(self.snapshot_every)):
                if self._stop:
                    return
                await asyncio.sleep(1)

    def stop(self):
        self._stop = True

    def status(self):
        return {"enabled": True, "admins": len(self.admins), "polls": self.counters["polls"], "updates": self.counters["updates"],
                "errors": self.counters["errors"] + self.counters["collect_errors"], "unauthorized": self.counters["unauthorized"],
                "last_error": self.last_error, "channel_check": self.summary,
                "admin_stats": (None if not self._admin_cache else (True if self._admin_cache[1].get("available") else self._admin_cache[1].get("reason")))}


async def _admin_via_entity(client, channel):
    ent = await client.get_entity(channel)
    return await tg_stats.collect_admin(client, ent)


async def build_panel(client, state, project_fn, wake=None, bot_token=None, channel=None, extra_admins=None):
    """از env می‌سازد؛ ادمین‌ها = حساب صاحب نشست Telethon + TG_ADMIN_IDS. اگر تنظیمات نبود None."""
    token = (bot_token or os.environ.get("TG_BOT_TOKEN", "")).strip()
    channel = (channel or os.environ.get("TG_STATS_CHANNEL", "") or os.environ.get("TG_DESTINATION", "")).strip()
    if not token or not channel:
        return None
    admins = set(int(x) for x in (extra_admins or []))
    for x in os.environ.get("TG_ADMIN_IDS", "").replace(";", ",").split(","):
        if x.strip().lstrip("-").isdigit():
            admins.add(int(x))
    try:
        me = await client.get_me()
        admins.add(int(me.id))
    except Exception:                                   # noqa: BLE001
        pass
    return Panel(PanelBot(token), admins, state, client, channel, project_fn, wake=wake)
