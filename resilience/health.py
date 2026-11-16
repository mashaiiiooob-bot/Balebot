"""پایش سلامت: وضعیت صف، سکوت کلکتور (ضربان)، و هشدار بدون اسپم (cooldown)."""
import time


def evaluate(outbox, now=None, max_pending_age_s=900, collector_silent_s=900, breaker_open=False):
    """فهرست (کد، پیام) مشکلات فعلی."""
    now = now or time.time()
    st = outbox.stats()
    issues = []
    if st["dead"]:
        issues.append(("dead", f"{st['dead']} پیام پس از چند تلاش ارسال نشد (/dead و /retry)"))
    if st["pending"] + st["inflight"] and st["oldest_pending_age_s"] > max_pending_age_s:
        issues.append(("backlog", f"قدیمی‌ترین پیام منتظر {st['oldest_pending_age_s'] // 60} دقیقه است ({st['pending']} در صف)"))
    last = outbox.kv_get("collector_last_seen")
    if last is not None and now - float(last) > collector_silent_s:
        issues.append(("collector_silent", f"کلکتور {int((now - float(last)) // 60)} دقیقه است ضربان نفرستاده"))
    if breaker_open:
        issues.append(("breaker", "ارتباط با API بله قطع است (circuit breaker باز است)؛ پیام‌ها در صف می‌مانند"))
    if outbox.is_paused():
        issues.append(("paused", "ارسال متوقف شده است (/resume)"))
    return issues


def format_status(outbox, breaker_open=False, now=None):
    st = outbox.stats()
    last = outbox.kv_get("collector_last_seen")
    now = now or time.time()
    lines = ["📊 وضعیت Balebot",
             f"صف: {st['pending']} منتظر | {st['inflight']} در حال ارسال | {st['dead']} ناموفق | {st['sent']} ارسال‌شده",
             f"قدیمی‌ترین منتظر: {st['oldest_pending_age_s'] // 60} دقیقه",
             "کلکتور: " + ("نامشخص" if last is None else f"{int((now - float(last)) // 60)} دقیقه پیش"),
             "ارسال: " + ("⏸ متوقف" if outbox.is_paused() else "▶️ فعال") + (" | ⚠️ اتصال بله قطع" if breaker_open else "")]
    return "\n".join(lines)


class Alerter:
    """هر کد هشدار را حداکثر یک‌بار در هر cooldown می‌فرستد (ماندگار در همان SQLite، پس از ری‌استارت هم اسپم نمی‌شود)."""

    def __init__(self, outbox, cooldown_s=1800, clock=time.time):
        self.outbox, self.cooldown, self.clock = outbox, cooldown_s, clock

    def due(self, issues):
        now = self.clock()
        out = []
        for code, msg in issues:
            last = float(self.outbox.kv_get("alert:" + code, "0"))
            if now - last >= self.cooldown:
                out.append((code, msg))
        return out

    def mark(self, code):
        self.outbox.kv_set("alert:" + code, self.clock())
