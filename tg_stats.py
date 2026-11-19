"""آمار کانال تلگرام: جمع‌آوری با Telethon (حساب کاربری) + محاسبه‌ی خالص (قابل‌تست بدون شبکه).

نکته: Bot API بازدید/ری‌اکشن پست‌های کانال را نمی‌دهد؛ این داده‌ها فقط با حساب کاربری (MTProto) خوانده می‌شوند.
آمار پیشرفته‌ی تلگرام (رشد، اعلان‌ها، ...) فقط اگر حساب در کانال «ادمین با دسترسی آمار» باشد در دسترس است.
"""
import statistics
import time

TEHRAN = 3.5 * 3600        # ایران بدون ساعت تابستانی: UTC+03:30
WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]


def _reaction_key(r):
    kind = type(r).__name__
    if kind == "ReactionEmoji":
        return getattr(r, "emoticon", "?")
    if kind == "ReactionPaid":
        return "⭐"
    if kind == "ReactionCustomEmoji":
        return "✨"          # ایموجی سفارشی (premium)
    return "❔"


def media_kind(m):
    media = getattr(m, "media", None)
    if media is None:
        return "text"
    k = type(media).__name__
    if "Photo" in k:
        return "photo"
    if "Document" in k:
        mime = getattr(getattr(media, "document", None), "mime_type", "") or ""
        return "video" if mime.startswith("video/") else ("audio" if mime.startswith("audio/") else "file")
    return "other"


def post_dict(m):
    """یک پیام Telethon → دیکشنری ساده (فقط اعداد و ۸۰ نویسه‌ی اول متن؛ پست‌های کانال عمومی‌اند)."""
    rx = {}
    for r in (getattr(getattr(m, "reactions", None), "results", None) or []):
        k = _reaction_key(r.reaction)
        rx[k] = rx.get(k, 0) + int(getattr(r, "count", 0) or 0)
    replies = getattr(getattr(m, "replies", None), "replies", 0) or 0
    text = " ".join((getattr(m, "message", "") or "").split())[:80]
    return {"id": m.id, "ts": int(m.date.timestamp()), "views": int(getattr(m, "views", 0) or 0),
            "forwards": int(getattr(m, "forwards", 0) or 0), "replies": int(replies), "reactions": rx,
            "text": text, "kind": media_kind(m), "pinned": bool(getattr(m, "pinned", False))}


async def collect(client, channel, limit=500, admin=True):
    """→ {"channel": {...}, "posts": [...], "admin": {...}|None, "fetched": ts}. خطاها به‌صورت مقدار برمی‌گردند، نه استثنا."""
    from telethon.tl import functions
    ent = await client.get_entity(channel)
    info = {"title": getattr(ent, "title", None), "username": getattr(ent, "username", None), "subs": None,
            "about": None, "linked_chat": None}
    try:
        full = await client(functions.channels.GetFullChannelRequest(ent))
        fc = full.full_chat
        info.update({"subs": getattr(fc, "participants_count", None), "about": (getattr(fc, "about", "") or "")[:200],
                     "linked_chat": bool(getattr(fc, "linked_chat_id", None))})
    except Exception as exc:                          # noqa: BLE001
        info["full_error"] = type(exc).__name__
    posts = []
    async for m in client.iter_messages(ent, limit=limit):
        if getattr(m, "action", None) is not None or getattr(m, "date", None) is None:
            continue                                   # پیام‌های سرویس (پین، عضویت، ...)
        posts.append(post_dict(m))
    out = {"channel": info, "posts": posts, "admin": None, "fetched": int(time.time())}
    if admin:
        out["admin"] = await collect_admin(client, ent, functions)
    return out


async def collect_admin(client, ent, functions=None):
    """آمار پیشرفته‌ی تلگرام؛ اگر در دسترس نباشد {available: False, reason}."""
    try:
        if functions is None:
            from telethon.tl import functions
        st = await client(functions.stats.GetBroadcastStatsRequest(channel=ent))
    except Exception as exc:                          # noqa: BLE001
        return {"available": False, "reason": type(exc).__name__}

    def val(x):
        return {"current": getattr(x, "current", None), "previous": getattr(x, "previous", None)} if x is not None else None
    notif = getattr(st, "enabled_notifications", None)
    period = getattr(st, "period", None)
    return {"available": True,
            "period": [int(period.min_date.timestamp()), int(period.max_date.timestamp())] if period else None,
            "followers": val(getattr(st, "followers", None)), "views_per_post": val(getattr(st, "views_per_post", None)),
            "shares_per_post": val(getattr(st, "shares_per_post", None)),
            "reactions_per_post": val(getattr(st, "reactions_per_post", None)),
            "notifications_pct": round(100 * notif.part / notif.total, 1) if notif and getattr(notif, "total", 0) else None}


# ------------------------------------------------------------------ محاسبه‌ی خالص
def summarize(posts, now=None, days=7, username=None):
    """آمار بازه‌ی [now-days, now]. days=None یعنی کل پست‌های موجود."""
    now = now or time.time()
    win = [p for p in posts if days is None or p["ts"] >= now - days * 86400]
    views = [p["views"] for p in win]
    mature = [p["views"] for p in win if now - p["ts"] >= 86400]
    rx_total = sum(sum(p["reactions"].values()) for p in win)
    fw = sum(p["forwards"] for p in win)
    rp = sum(p["replies"] for p in win)
    vt = sum(views)
    by_emoji = {}
    for p in win:
        for k, v in p["reactions"].items():
            by_emoji[k] = by_emoji.get(k, 0) + v
    span_days = days if days else max(1.0, (now - min((p["ts"] for p in posts), default=now)) / 86400)
    hours = {h: [] for h in range(24)}
    wdays = {d: [] for d in range(7)}
    for p in win:
        t = time.gmtime(p["ts"] + TEHRAN)
        hours[t.tm_hour].append(p["views"])
        wdays[t.tm_wday].append(p["views"])

    def best(group, n):
        rows = [(k, round(statistics.mean(v)), len(v)) for k, v in group.items() if len(v) >= 2]
        return sorted(rows, key=lambda r: -r[1])[:n]

    def top(key, n=5):
        rows = sorted(win, key=lambda p: -(sum(p["reactions"].values()) if key == "reactions" else p[key]))[:n]
        return [dict(p, link=(f"https://t.me/{username}/{p['id']}" if username else None)) for p in rows if (sum(p["reactions"].values()) if key == "reactions" else p[key]) > 0]

    return {"days": days, "posts": len(win), "views_total": vt, "views_avg": round(vt / len(win)) if win else 0,
            "views_median": round(statistics.median(views)) if views else 0, "views_max": max(views, default=0),
            "views_avg_mature": round(statistics.mean(mature)) if mature else None,
            "reactions_total": rx_total, "reactions_avg": round(rx_total / len(win), 1) if win else 0,
            "by_emoji": dict(sorted(by_emoji.items(), key=lambda kv: -kv[1])[:12]),
            "forwards_total": fw, "forwards_avg": round(fw / len(win), 1) if win else 0, "replies_total": rp,
            "er_pct": round(100 * (rx_total + fw + rp) / vt, 2) if vt else 0.0,
            "posts_per_day": round(len(win) / span_days, 1) if span_days else 0,
            "kinds": {k: sum(1 for p in win if p["kind"] == k) for k in sorted({p["kind"] for p in win})},
            "best_hours": best(hours, 3), "best_weekdays": [(WEEKDAYS[k], a, n) for k, a, n in best(wdays, 3)],
            "top_views": top("views"), "top_reactions": top("reactions"), "top_forwards": top("forwards")}


class Snapshots:
    """نمونه‌ی ساعتی تعداد مشترکین در state (فقط عدد؛ برای محاسبه‌ی رشد)."""

    def __init__(self, state, keep_days=45):
        self.state = state
        self.keep = keep_days * 86400
        self.rows = state.data.setdefault("tgstats", [])

    def record(self, subs, now=None):
        now = int(now or time.time())
        if subs is None:
            return False
        if self.rows and now - self.rows[-1][0] < 3300:
            return False
        self.rows.append([now, int(subs)])
        cutoff = now - self.keep
        self.rows[:] = [r for r in self.rows if r[0] >= cutoff]
        self.state.dirty = True
        return True

    def delta(self, days, now=None, subs=None):
        """تغییر مشترکین نسبت به نزدیک‌ترین نمونه‌ی «قدیمی‌تر از days روز»؛ None اگر تاریخچه کافی نیست."""
        now = int(now or time.time())
        old = [r for r in self.rows if r[0] <= now - days * 86400]
        if not old:
            return None
        cur = subs if subs is not None else (self.rows[-1][1] if self.rows else None)
        return None if cur is None else cur - old[-1][1]
