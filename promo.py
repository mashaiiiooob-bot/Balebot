"""حلقه‌ی پیام تکرارشونده (پیش‌فرض خاموش): هر N ثانیه یک پیام ثابت می‌فرستد، چند ثانیه می‌گذارد بماند و پاکش می‌کند.
فقط پیام‌های خودش را (با message_id که خودش گرفته) پاک می‌کند و هرگز به پیام‌های خبری دست نمی‌زند."""
import asyncio
import logging

log = logging.getLogger("balebot")


def _message_id(result):
    if isinstance(result, dict):
        mid = result.get("message_id")
        return int(mid) if mid is not None else None
    return None


async def _sleep(stop, seconds):
    """خواب که با stop بیدار می‌شود؛ True یعنی درخواست توقف آمده."""
    if seconds <= 0:
        return stop.is_set()
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _delete(bale, chat, mid, state):
    try:
        await asyncio.to_thread(bale.delete_message, chat, mid)
        state.data.pop("promo", None)
        state.dirty = True
        return True
    except Exception as exc:
        log.warning("Promo delete failed: %s", type(exc).__name__)
        return False


async def promo_loop(bale, state, stop, chat, text, interval, visible):
    """interval: فاصله‌ی ارسال‌ها (ثانیه)؛ visible: مدت ماندن پیام قبل از حذف (باید کمتر از interval باشد)."""
    visible = min(visible, max(1, interval - 1))
    # پاک‌سازی پیامی که اجرای قبلی (قطع ناگهانی) نتوانسته پاک کند
    left = (state.data.get("promo") or {}).get("mid")
    if left:
        await _delete(bale, chat, int(left), state)
    fails = 0
    while not stop.is_set():
        mid = None
        try:
            mid = _message_id(await asyncio.to_thread(bale.send_text, chat, text))
            fails = 0
            if mid is not None:
                state.data["promo"] = {"mid": mid}
                state.dirty = True
        except Exception as exc:
            fails += 1
            log.warning("Promo send failed: %s", type(exc).__name__)
        stopped = await _sleep(stop, visible if mid is not None else min(interval, 30 * (fails + 1)))
        if mid is not None:
            if not await _delete(bale, chat, mid, state):
                await asyncio.sleep(2)
                await _delete(bale, chat, mid, state)  # یک تلاش مجدد؛ اگر نشد در state می‌ماند تا اجرای بعدی پاک کند
        if stopped or await _sleep(stop, max(0, interval - visible) if mid is not None else 0):
            break
