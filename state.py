import json
import os
from pathlib import Path

STATE_VERSION = 2  # نسخه‌ی ۲: کلیدها HMAC هستند و peers (شناسه/هش کانال) ذخیره نمی‌شود


class State:
    """وضعیت کوچک JSON: فقط آخرین شناسه‌ی پردازش‌شده‌ی هر کانال (کلیدها HMAC با راز خصوصی هستند)."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = {"version": STATE_VERSION, "checkpoints": {}, "fails": {}, "alerts": {}, "seen": [], "metrics": {}, "reset_token": ""}
        self.dirty = False
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if loaded.get("version") == STATE_VERSION:
                    for name in ("checkpoints", "fails", "alerts"):
                        self.data[name].update(loaded.get(name, {}))
                    self.data["seen"] = list(loaded.get("seen", []))
                    self.data["metrics"] = dict(loaded.get("metrics", {}))
                    self.data["reset_token"] = loaded.get("reset_token", "")
                    if "ai" in loaded:
                        self.data["ai"] = loaded["ai"]
                else:
                    self.dirty = True  # state قدیمی (با کلید هش‌ساده و peers) دور ریخته و بازنویسی می‌شود
            except (ValueError, OSError):
                pass

    def last_id(self, key):
        return int(self.data["checkpoints"].get(key, 0))

    def set_last_id(self, key, message_id):
        if int(message_id) > self.last_id(key):
            self.data["checkpoints"][key] = int(message_id)
            self.dirty = True

    def fail_count(self, key, message_id):
        return int(self.data["fails"].get(f"{key}:{message_id}", 0))

    def add_fail(self, key, message_id):
        k = f"{key}:{message_id}"
        self.data["fails"][k] = self.fail_count(key, message_id) + 1
        self.dirty = True

    def clear_fail(self, key, message_id):
        if self.data["fails"].pop(f"{key}:{message_id}", None) is not None:
            self.dirty = True

    def alert_due(self, code, now, every=86400):
        return now - float(self.data["alerts"].get(code, 0)) >= every

    def mark_alert(self, code, now):
        self.data["alerts"][code] = now
        self.dirty = True

    def recent_seen(self, now, window=43200):
        return [e for e in self.data["seen"] if now - e.get("t", 0) < window]

    def add_seen(self, sig, now, cap=600):
        self.data["seen"] = (self.recent_seen(now) + [{"t": int(now), "w": sig["w"], "n": sig["n"]}])[-cap:]
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, sort_keys=True, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self.dirty = False
