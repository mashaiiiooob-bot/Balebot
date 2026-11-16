"""آداپتور: به‌جای ارسال مستقیم به بله، پیام را در صف ماندگار می‌گذارد. همان دو متدی را دارد که poll.py صدا می‌زند
(send_text / send_media) و بقیه‌ی متدها (get_me, delete_message, ...) را به کلاینت واقعی می‌سپارد."""
import hashlib
import time

from resilience.outbox import file_sha256


def _key(*parts):
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()


class OutboxBale:
    def __init__(self, outbox, real=None, clock=time.time):
        self.outbox, self.real, self.clock = outbox, real, clock

    def send_text(self, chat_id, text):
        return self.outbox.enqueue(_key("t", chat_id, text), {"type": "text", "text": text, "chat": str(chat_id)})

    def send_media(self, chat_id, path, caption, media_type):
        return self.outbox.enqueue_media(_key("m", chat_id, file_sha256(path), caption, media_type), path, caption or "", media_type, str(chat_id))

    def heartbeat(self, every_s=60):
        """ضربان کلکتور؛ در هر بازه‌ی every_s حداکثر یک‌بار وارد صف می‌شود."""
        slot = int(self.clock() // every_s)
        return self.outbox.enqueue(_key("hb", slot), {"type": "heartbeat", "ts": self.clock()})

    def __getattr__(self, name):
        if self.real is None:
            raise AttributeError(name)
        return getattr(self.real, name)
