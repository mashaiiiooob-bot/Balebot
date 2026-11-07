import json
import os
from pathlib import Path


class State:
    """وضعیت کوچک JSON: فقط آخرین شناسه‌ی پردازش‌شده‌ی هر کانال (کلیدها هش هستند)."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = {"checkpoints": {}, "fails": {}, "peers": {}}
        self.dirty = False
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.data["checkpoints"].update(loaded.get("checkpoints", {}))
                self.data["fails"].update(loaded.get("fails", {}))
                self.data["peers"].update(loaded.get("peers", {}))
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

    def get_peer(self, src_key):
        return self.data["peers"].get(src_key)

    def set_peer(self, src_key, channel_id, access_hash):
        value = [int(channel_id), int(access_hash)]
        if self.data["peers"].get(src_key) != value:
            self.data["peers"][src_key] = value
            self.dirty = True

    def save(self):
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, sort_keys=True, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self.dirty = False
