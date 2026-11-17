"""مسیر دوم تحویل: همان پیام‌های فیلترشده، با یک «ربات تلگرام» به کانال تلگرامی مقصد هم ارسال می‌شود.

اصول:
- مستقل از بله: پیام ابتدا وارد صف تلگرام می‌شود و بعد به بله می‌رود؛ قطعی بله تلگرام را نمی‌بندد و خرابی تلگرام
  (مثلاً ادمین‌نبودن ربات) هرگز ارسال بله را نمی‌بندد.
- ضدتکرار: کلید هش‌شده‌ی محتوا (بدون خود متن) در state ماندگار است؛ اگر بله شکست بخورد و پیام دوباره امتحان شود،
  دوباره به تلگرام نمی‌رود.
- رعایت سقف تلگرام برای کانال (~۲۰ پیام در دقیقه) و `retry_after`؛ backoff؛ حذف پیام‌های بسیار قدیمی؛ سقف صف.
- توکن و متن پیام هرگز در لاگ/خطا نمی‌آید.
"""
import asyncio
import collections
import hashlib
import os
import shutil
import tempfile
import threading
import time

import requests

from bale_client import split_text

CAPTION_LIMIT = 1024
_METHOD = {"photo": ("sendPhoto", "photo"), "video": ("sendVideo", "video"),
           "audio": ("sendAudio", "audio"), "document": ("sendDocument", "document")}


class TgError(RuntimeError):
    def __init__(self, message, status=None, retry_after=0):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


class TelegramBot:
    def __init__(self, token, session=None, timeout=60):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.session = session or requests.Session()
        self.timeout = timeout

    def _call(self, method, data=None, path=None, field=None):
        try:
            if path:
                with open(path, "rb") as fh:
                    r = self.session.post(self.base + method, data=data, files={field: fh}, timeout=self.timeout)
            else:
                r = self.session.post(self.base + method, data=data, timeout=self.timeout)
        except requests.RequestException as exc:     # پیام requests شامل URL (و توکن) است؛ چاپ نمی‌شود
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

    def send_text(self, chat, text):
        res = None
        for chunk in split_text(text):
            res = self._call("sendMessage", {"chat_id": chat, "text": chunk, "disable_web_page_preview": "true"})
        return res

    def send_media(self, chat, path, caption, kind):
        method, field = _METHOD.get(kind, _METHOD["document"])
        caption = caption or ""
        too_long = len(caption) > CAPTION_LIMIT
        data = {"chat_id": chat}
        if caption and not too_long:
            data["caption"] = caption
        res = self._call(method, data, path, field)
        if too_long:
            self.send_text(chat, caption)
        return res


def _key(*parts):
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:32]


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class TelegramLane:
    """صف ارسال به تلگرام؛ یک coroutine (run) آن را در حلقه‌ی asyncio اصلی تخلیه می‌کند."""

    def __init__(self, bot, chat, state, min_interval=3.5, max_age_s=3 * 3600, cap=300, seen_ttl_s=86400,
                 seen_cap=3000, clock=time.time, sleep=None):
        self.bot, self.chat, self.state = bot, chat, state
        self.seen = state.data.setdefault("tg_seen", {})
        self.min_interval, self.max_age_s, self.cap = min_interval, max_age_s, cap
        self.seen_ttl_s, self.seen_cap, self.clock = seen_ttl_s, seen_cap, clock
        self._sleep = sleep or asyncio.sleep
        self._dq = collections.deque()
        self._pending = set()
        self._lock = threading.Lock()
        self._loop = None
        self._event = None
        self._stop = False
        self._last_send = 0.0
        self._tmp = tempfile.mkdtemp(prefix="tglane_")
        self.counters = collections.Counter()
        self.last_error = None
        self.last_ok = None

    @classmethod
    def from_env(cls, state):
        token = os.environ.get("TG_BOT_TOKEN", "").strip()
        chat = os.environ.get("TG_DESTINATION", "").strip()
        if not token or not chat:
            return None
        age_h = float(os.environ.get("TG_MAX_AGE_HOURS", "3") or 3)
        return cls(TelegramBot(token), chat, state, max_age_s=age_h * 3600)

    # ---- ورود (از هر thread)
    def _submit(self, key, item):
        with self._lock:
            if key in self.seen or key in self._pending:
                self.counters["skipped_duplicate"] += 1
                return False
            self._pending.add(key)
            item.update({"key": key, "t": self.clock(), "next": 0.0, "attempts": 0})
            self._dq.append(item)
            while len(self._dq) > self.cap:
                old = self._dq.popleft()
                self._pending.discard(old["key"])
                self._cleanup(old)
                self.counters["dropped_overflow"] += 1
        try:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:        # حلقه بسته است (خاموشی)؛ آیتم در صف می‌ماند و مشکلی نیست
            pass
        return True

    def submit_text(self, text):
        return self._submit(_key("t", text), {"type": "text", "text": text})

    def submit_media(self, path, caption, kind):
        key = _key("m", _file_hash(path), caption or "", kind)
        with self._lock:
            if key in self.seen or key in self._pending:
                self.counters["skipped_duplicate"] += 1
                return False
        dst = os.path.join(self._tmp, key + os.path.splitext(path)[1][:8])
        shutil.copyfile(path, dst)             # فایل اصلی بلافاصله پس از ارسال بله پاک می‌شود؛ کپی خودمان را داریم
        ok = self._submit(key, {"type": "media", "path": dst, "caption": caption or "", "kind": kind})
        if not ok:
            self._cleanup({"path": dst})
        return ok

    # ---- تخلیه
    def _cleanup(self, item):
        p = item.get("path")
        if p:
            try:
                os.remove(p)
            except OSError:
                pass

    def _send(self, item):
        try:
            if item["type"] == "text":
                self.bot.send_text(self.chat, item["text"])
            else:
                self.bot.send_media(self.chat, item["path"], item["caption"], item["kind"])
            return None
        except TgError as exc:
            return exc
        except Exception as exc:               # noqa: BLE001
            return TgError("Telegram unexpected error: " + type(exc).__name__)

    def _head(self):
        with self._lock:
            return self._dq[0] if self._dq else None

    def _finish(self, item):
        with self._lock:
            if self._dq and self._dq[0] is item:
                self._dq.popleft()
            self._pending.discard(item["key"])
        self._cleanup(item)

    def _remember(self, key, now):
        self.seen[key] = int(now)
        keep = sorted(((t, k) for k, t in self.seen.items() if now - t < self.seen_ttl_s), reverse=True)[: self.seen_cap]
        fresh = {k: t for t, k in keep}     # حذف منقضی‌ها و حفظ سقف؛ شیء dict همان می‌ماند (state به آن اشاره می‌کند)
        self.seen.clear()
        self.seen.update(fresh)
        self.state.dirty = True

    async def run(self):
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()
        try:
            await self._run()
        finally:
            self._loop = None

    async def _run(self):
        while not self._stop:
            item = self._head()
            if item is None:
                self._event.clear()
                try:
                    await asyncio.wait_for(self._event.wait(), 30)
                except asyncio.TimeoutError:
                    pass
                continue
            now = self.clock()
            if now - item["t"] > self.max_age_s:
                self._finish(item)
                self.counters["dropped_age"] += 1
                continue
            if item["next"] > now:
                await self._sleep(min(item["next"] - now, 30))
                continue
            gap = self._last_send + self.min_interval - now
            if gap > 0:
                await self._sleep(gap)
            err = await asyncio.to_thread(self._send, item)
            self._last_send = self.clock()
            if err is None:
                self._finish(item)
                self._remember(item["key"], self.clock())
                self.counters["sent"] += 1
                self.last_ok, self.last_error = self.clock(), None
            else:
                item["attempts"] += 1
                delay = max(err.retry_after, min(300, 5 * 2 ** (item["attempts"] - 1)))
                item["next"] = self.clock() + delay
                self.counters["failed_attempts"] += 1
                self.last_error = f"http_{err.status}" if err.status else "network"

    def stop(self):
        self._stop = True
        try:
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:
            pass

    def status(self):
        with self._lock:
            queued = len(self._dq)
        return {"enabled": True, "queued": queued, "sent": self.counters["sent"], "failed_attempts": self.counters["failed_attempts"],
                "dropped_age": self.counters["dropped_age"], "dropped_overflow": self.counters["dropped_overflow"],
                "skipped_duplicate": self.counters["skipped_duplicate"], "last_error": self.last_error,
                "last_ok_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(self.last_ok)) if self.last_ok else None}


class FanoutBale:
    """جایگزین کلاینت بله: برای مقصد اصلی، ابتدا پیام را در صف تلگرام می‌گذارد و بعد به بله می‌فرستد.
    پیام‌های مدیر (چت دیگر) به تلگرام نمی‌روند. بقیه‌ی متدها مستقیم به کلاینت واقعی می‌روند."""

    def __init__(self, real, lane, main_chat, signature="", tg_signature=""):
        self.real, self.lane, self.main = real, lane, str(main_chat)
        self.sig, self.tg_sig = signature, tg_signature

    def _tg(self, text):
        if self.tg_sig and self.sig and text.endswith(self.sig):
            return text[: -len(self.sig)] + self.tg_sig
        return text

    def _safe(self, fn, *a):
        """خطای هر نوع در مسیر تلگرام هرگز نباید ارسال بله را مختل کند."""
        try:
            fn(*a)
        except Exception:           # noqa: BLE001
            self.lane.counters["submit_errors"] += 1

    def send_text(self, chat_id, text):
        if str(chat_id) == self.main:
            self._safe(self.lane.submit_text, self._tg(text))
        return self.real.send_text(chat_id, text)

    def send_media(self, chat_id, path, caption, media_type):
        if str(chat_id) == self.main:
            self._safe(self.lane.submit_media, path, self._tg(caption or ""), media_type)
        return self.real.send_media(chat_id, path, caption, media_type)

    def __getattr__(self, name):
        return getattr(self.real, name)
