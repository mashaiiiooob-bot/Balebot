"""ارسال «بهترین‌تلاش» آمار و رویدادها به Supabase (Edge Function balebot-ingest).

اصول:
- هرگز ارسال پیام را کند یا متوقف نمی‌کند: ثبت در صف حافظه (O(1))، ارسال دسته‌ای در پس‌زمینه؛ هر خطا بلعیده می‌شود.
- فقط عدد/برچسب: متن پیام، نام منبع و محتوای رسانه هرگز فرستاده نمی‌شود (منبع = هش کوتاه).
- بدون SUPABASE_INGEST_URL و SUPABASE_INGEST_KEY غیرفعال است (NullSink).
- کلید هرگز در لاگ/خطا نمی‌آید. خطای 401/403 → توقف ۱۰ دقیقه‌ای؛ 400/413 → دسته‌ی معیوب کنار گذاشته می‌شود؛ 5xx/شبکه → نگه‌داشتن و backoff.
"""
import asyncio
import collections
import os
import threading
import time

import requests

KINDS = ("events", "status", "channel", "posts")


def _clean(v, depth=0):
    """فقط نوع‌های ساده‌ی JSON؛ رشته‌ها کوتاه می‌شوند (از نشت متن/اشیای عجیب جلوگیری می‌کند)."""
    if depth > 3:
        return None
    if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return v[:120]
    if isinstance(v, dict):
        return {str(k)[:60]: _clean(x, depth + 1) for k, x in list(v.items())[:200]}
    if isinstance(v, (list, tuple)):
        return [_clean(x, depth + 1) for x in list(v)[:50]]
    return None


class NullSink:
    enabled = False
    dest = "bale"

    def event(self, *a, **k): pass
    def status(self, *a, **k): pass
    def channel_and_posts(self, *a, **k): pass
    def flush_sync(self): return True
    async def run(self): return None
    def stop(self): pass
    def status_info(self): return {"enabled": False}


class SupaSink:
    enabled = True

    def __init__(self, url, key, session=None, clock=time.time, flush_every=20, batch=200, cap=5000,
                 status_every=300, channel_every=3300, posts_every=21600, purge_every=86400, post_days=7):
        self.url, self.key = url, key
        self.session = session or requests.Session()
        self.clock = clock
        self.flush_every, self.batch, self.cap = flush_every, batch, cap
        self.status_every, self.channel_every, self.posts_every, self.purge_every = status_every, channel_every, posts_every, purge_every
        self.post_days = post_days
        self.dest = "bale"
        self.q = {k: collections.deque() for k in KINDS}
        self.lock = threading.Lock()
        self.c = collections.Counter()
        self.last_error = None
        self.last_ok = None
        self._pause_until = 0.0
        self._fails = 0
        self._last = {"status": 0.0, "channel": 0.0, "posts": 0.0, "purge": clock()}
        self._stop = False

    # ---- ورود (از هر thread)
    def _add(self, kind, row):
        try:
            with self.lock:
                dq = self.q[kind]
                dq.append(row)
                while len(dq) > self.cap:
                    dq.popleft()
                    self.c["dropped_overflow"] += 1
        except Exception:                       # noqa: BLE001
            pass

    def event(self, src, msg_id, outcome, reason=None, importance=None, dest=None):
        try:
            self._add("events", {"ts": int(self.clock()), "src": str(src)[:32], "msg_id": msg_id, "outcome": outcome,
                                 "reason": (str(reason)[:40] if reason else None), "importance": importance,
                                 "dest": (dest or (self.dest if outcome == "sent" else None))})
        except Exception:                       # noqa: BLE001
            pass

    def status(self, run_id, uptime_min, stats, extra=None):
        """حداکثر هر status_every ثانیه یک عکس از شمارنده‌ها."""
        try:
            now = self.clock()
            if now - self._last["status"] < self.status_every:
                return
            self._last["status"] = now
            self._add("status", {"ts": int(now), "run_id": str(run_id or "")[:40], "uptime_min": int(uptime_min or 0),
                                 "stats": _clean(dict(stats or {})), "extra": _clean(extra or {})})
        except Exception:                       # noqa: BLE001
            pass

    def channel_and_posts(self, data):
        """از خروجی tg_stats.collect: عکس ساعتی کانال + آمار پست‌های ۷ روز اخیر (هر ۶ ساعت)."""
        try:
            import tg_stats
            now = self.clock()
            ch = data.get("channel") or {}
            name = ch.get("username") or ch.get("title") or "channel"
            posts = data.get("posts") or []
            if now - self._last["channel"] >= self.channel_every:
                self._last["channel"] = now
                s1 = tg_stats.summarize(posts, now, 1, ch.get("username"))
                self._add("channel", {"channel": name, "ts": int(now), "subs": ch.get("subs"), "posts": s1["posts"],
                                      "views_24h": s1["views_total"], "reactions_24h": s1["reactions_total"],
                                      "forwards_24h": s1["forwards_total"], "extra": {"posts_per_day_7": tg_stats.summarize(posts, now, 7)["posts_per_day"]}})
            if now - self._last["posts"] >= self.posts_every:
                self._last["posts"] = now
                for p in posts:
                    if p["ts"] >= now - self.post_days * 86400:
                        self._add("posts", {"channel": name, "msg_id": p["id"], "captured_at": int(now), "posted_at": p["ts"],
                                            "views": p["views"], "forwards": p["forwards"], "replies": p["replies"],
                                            "reactions": p["reactions"], "kind": p["kind"]})
        except Exception:                       # noqa: BLE001
            pass

    # ---- ارسال
    def _post(self, payload):
        try:
            r = self.session.post(self.url, json=payload, headers={"x-ingest-key": self.key}, timeout=20)
        except requests.RequestException as exc:       # پیام requests شامل URL است؛ فقط نوع خطا
            return None, type(exc).__name__
        try:
            ok = bool(r.json().get("ok"))
        except Exception:                               # noqa: BLE001
            ok = False
        return (r.status_code if not (r.status_code == 200 and not ok) else 502), None

    def flush_sync(self):
        """همه‌ی صف‌ها را دسته‌دسته می‌فرستد؛ True اگر خطایی نبود."""
        now = self.clock()
        if now < self._pause_until:
            return False
        all_ok = True
        for kind in KINDS:
            while True:
                with self.lock:
                    rows = list(self.q[kind])[: self.batch]
                if not rows:
                    break
                code, err = self._post({"kind": kind, "rows": rows})
                if code == 200:
                    with self.lock:
                        for _ in rows:
                            if self.q[kind]:
                                self.q[kind].popleft()
                    self.c["rows_sent"] += len(rows)
                    self.last_ok, self.last_error, self._fails = self.clock(), None, 0
                    continue
                all_ok = False
                self.last_error = err or f"http_{code}"
                if code in (401, 403):                  # کلید نامعتبر/لغوشده: ۱۰ دقیقه کاری نکن
                    self._pause_until = self.clock() + 600
                elif code in (400, 413, 422):           # دسته‌ی معیوب: کنار گذاشته می‌شود تا صف گیر نکند
                    with self.lock:
                        for _ in rows:
                            if self.q[kind]:
                                self.q[kind].popleft()
                    self.c["dropped_bad"] += len(rows)
                    continue
                else:                                   # 5xx/شبکه: نگه دار و backoff
                    self._fails += 1
                    self._pause_until = self.clock() + min(300, 15 * 2 ** min(self._fails, 5))
                self.c["failed_posts"] += 1
                return False
        return all_ok

    async def run(self):
        while not self._stop:
            try:
                await asyncio.to_thread(self.flush_sync)
                now = self.clock()
                if now - self._last["purge"] >= self.purge_every:       # پاک‌سازی روزانه‌ی داده‌ی قدیمی (۱۲۰ روز)
                    self._last["purge"] = now
                    await asyncio.to_thread(self._post, {"kind": "purge", "days": 120})
            except Exception:                           # noqa: BLE001
                self.c["loop_errors"] += 1
            for _ in range(int(self.flush_every)):
                if self._stop:
                    return
                await asyncio.sleep(1)

    def stop(self):
        self._stop = True

    def status_info(self):
        with self.lock:
            queued = {k: len(v) for k, v in self.q.items()}
        return {"enabled": True, "queued": queued, "rows_sent": self.c["rows_sent"], "failed_posts": self.c["failed_posts"],
                "dropped_bad": self.c["dropped_bad"], "dropped_overflow": self.c["dropped_overflow"], "last_error": self.last_error,
                "last_ok_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(self.last_ok)) if self.last_ok else None}


def from_env():
    url = os.environ.get("SUPABASE_INGEST_URL", "").strip()
    key = os.environ.get("SUPABASE_INGEST_KEY", "").strip()
    return SupaSink(url, key) if url and key else NullSink()
