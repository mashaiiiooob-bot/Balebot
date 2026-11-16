"""تحویل پیام‌های صف به بله، مستقل از دریافت تلگرام، با circuit breaker و دسته‌بندی خطا."""
import re
import time

_HTTP = re.compile(r"Bale HTTP (\d+)")


def classify(exc):
    """→ (permanent, retry_after_s, label). خطای ۴۰۰ = محتوای نامعتبر (دائمی)؛ ۴۰۱/۴۰۳/۴۰۴ = مشکل توکن/دسترسی
    (موقت با backoff بلند تا با اصلاح تنظیمات تحویل ادامه یابد)؛ ۴۲۹/۵xx/شبکه = موقت."""
    msg = str(exc)
    hinted = int(getattr(exc, "retry_after", 0) or 0)      # parameters.retry_after از پاسخ رسمی بله
    m = _HTTP.search(msg)
    if m:
        code = int(m.group(1))
        if code == 400:
            return True, 0, "http_400"
        if code in (401, 403, 404):
            return False, max(300, hinted), f"http_{code}"
        if code == 429:
            return False, max(30, hinted), "http_429"
        return False, hinted, f"http_{code}"
    if "network error" in msg or "invalid JSON" in msg:
        return False, 0, "network"
    return False, hinted, "other"


class Breaker:
    """بعد از N شکست موقت پیاپی باز می‌شود؛ مدت باز بودن دو برابر می‌شود (حداکثر max_open_s)."""

    def __init__(self, threshold=5, open_s=60, max_open_s=900, clock=time.time):
        self.threshold, self.open_s, self.max_open_s, self.clock = threshold, open_s, max_open_s, clock
        self.fails = 0
        self.opened_until = 0.0
        self.cur_open = open_s

    @property
    def is_open(self):
        return self.clock() < self.opened_until

    def allow(self):
        return not self.is_open     # پس از پایان مهلت، یک «پروب» مجاز است

    def ok(self):
        self.fails = 0
        self.cur_open = self.open_s
        self.opened_until = 0.0

    def fail(self):
        self.fails += 1
        if self.fails >= self.threshold:
            self.opened_until = self.clock() + self.cur_open
            self.cur_open = min(self.max_open_s, self.cur_open * 2)


class Deliverer:
    def __init__(self, outbox, bale, destination, breaker=None, rate_per_s=1.0, sleep=time.sleep, clock=time.time):
        self.outbox, self.bale, self.dest = outbox, bale, destination
        self.breaker = breaker or Breaker(clock=clock)
        self.interval = 1.0 / rate_per_s if rate_per_s else 0
        self.sleep, self.clock = sleep, clock

    def _send(self, payload):
        kind = payload.get("type")
        chat = payload.get("chat") or self.dest
        if kind == "text":
            self.bale.send_text(chat, payload["text"])
        elif kind == "media":
            import os
            path = self.outbox.spool_path(payload)
            if not os.path.exists(path):
                raise FileNotFoundError("spool_missing")
            self.bale.send_media(chat, path, payload.get("caption", ""), payload.get("kind", "document"))
        else:
            raise ValueError(f"unknown payload type {kind!r}")

    def run_once(self, max_items=20):
        res = {"sent": 0, "retry": 0, "dead": 0, "released": 0, "skipped": None}
        if self.outbox.is_paused():
            res["skipped"] = "paused"
            return res
        if not self.breaker.allow():
            res["skipped"] = "breaker_open"
            return res
        items = self.outbox.claim(max_items)
        for idx, it in enumerate(items):
            p = it["payload"]
            if p.get("type") == "heartbeat":          # ضربان کلکتور: ثبت می‌شود و ارسال نمی‌شود
                self.outbox.kv_set("collector_last_seen", p.get("ts", self.clock()))
                self.outbox.mark_sent(it["id"])
                continue
            if idx and self.interval:
                self.sleep(self.interval)
            try:
                self._send(p)
            except (FileNotFoundError, ValueError) as exc:
                self.outbox.mark_failed(it["id"], exc, permanent=True)
                res["dead"] += 1
                continue
            except Exception as exc:                  # noqa: BLE001
                permanent, retry_after, label = classify(exc)
                state = self.outbox.mark_failed(it["id"], label, permanent=permanent, retry_after=retry_after)
                res["dead" if state == "dead" else "retry"] += 1
                if not permanent:
                    self.breaker.fail()
                    rest = [x["id"] for x in items[idx + 1:]]     # بقیه را رها کن تا مدام فشار نیاید
                    self.outbox.release(rest)
                    res["released"] = len(rest)
                    break
                continue
            self.outbox.mark_sent(it["id"])
            self.breaker.ok()
            res["sent"] += 1
        return res
