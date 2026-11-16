"""صف خروجی ماندگار (SQLite/WAL) با ضدتکرار، قفل زمانی (lease)، backoff و بازیابی پس از ری‌استارت.

ضمانت: «حداقل یک‌بار» (at-least-once). dedupe_key مانع ورود دوباره‌ی یک پیام به صف می‌شود؛ اما اگر
پردازش درست بین «ارسال موفق» و «ثبت ارسال» قطع شود، همان یک پیام ممکن است یک‌بار دیگر ارسال شود
(API بله کلید idempotency ندارد). این مصالحه عمدی است: گم‌شدن خبر بدتر از تکرار نادر است.
"""
import hashlib
import json
import os
import random
import shutil
import sqlite3
import time
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key TEXT UNIQUE NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',      -- pending | inflight | sent | dead
  attempts INTEGER NOT NULL DEFAULT 0,
  next_try REAL NOT NULL,
  lease_until REAL NOT NULL DEFAULT 0,
  created REAL NOT NULL,
  sent_at REAL,
  last_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_due ON outbox(status, next_try);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def file_sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


class Outbox:
    def __init__(self, path, spool_dir=None, lease_s=180, max_attempts=12, clock=time.time, rng=random.random):
        self.path = path
        self.spool = spool_dir or (path + ".spool")
        self.lease_s, self.max_attempts, self.clock, self.rng = lease_s, max_attempts, clock, rng
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        os.makedirs(self.spool, exist_ok=True)
        c = self._conn()
        try:
            c.executescript(SCHEMA)
        finally:
            c.close()

    # ---- اتصال/تراکنش
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    @contextmanager
    def _tx(self):
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.execute("COMMIT")
        except BaseException:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            c.close()

    # ---- ورود به صف
    def enqueue(self, dedupe_key, payload):
        """True اگر جدید بود؛ False اگر قبلاً (در صف/ارسال‌شده/مرده) وجود داشت."""
        now = self.clock()
        with self._tx() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO outbox(dedupe_key,payload,next_try,created) VALUES(?,?,?,?)",
                (dedupe_key, json.dumps(payload, ensure_ascii=False), now, now))
            return cur.rowcount == 1

    def has(self, dedupe_key):
        c = self._conn()
        try:
            return c.execute("SELECT 1 FROM outbox WHERE dedupe_key=?", (dedupe_key,)).fetchone() is not None
        finally:
            c.close()

    def enqueue_media(self, dedupe_key, path, caption="", kind="document", chat=None):
        """فایل را داخل spool کپی می‌کند تا حتی اگر فایل اصلی پاک شد، ارسال ممکن باشد."""
        if self.has(dedupe_key):
            return False
        name = file_sha256(path)[:32] + os.path.splitext(path)[1][:8]
        dst = os.path.join(self.spool, name)
        if not os.path.exists(dst):
            shutil.copyfile(path, dst + ".part")
            os.replace(dst + ".part", dst)
        ok = self.enqueue(dedupe_key, {"type": "media", "spool": name, "caption": caption, "kind": kind, "chat": chat})
        return ok

    # ---- برداشت
    def claim(self, n=10):
        now = self.clock()
        out = []
        with self._tx() as c:
            rows = c.execute(
                "SELECT id,payload,attempts,status FROM outbox WHERE (status='pending' AND next_try<=?) "
                "OR (status='inflight' AND lease_until<?) ORDER BY id LIMIT ?", (now, now, n)).fetchall()
            for r in rows:
                reclaimed = r["status"] == "inflight"   # پردازش قبلی نیمه‌کاره ماند (کرش/قطع) → یک تلاش محسوب می‌شود
                attempts = r["attempts"] + (1 if reclaimed else 0)
                c.execute("UPDATE outbox SET status='inflight', lease_until=?, attempts=? WHERE id=?",
                          (now + self.lease_s, attempts, r["id"]))
                out.append({"id": r["id"], "payload": json.loads(r["payload"]), "attempts": attempts})
        return out

    def release(self, ids):
        """برگرداندن موارد برداشته‌شده‌ی ارسال‌نشده بدون شمردن تلاش."""
        if not ids:
            return
        with self._tx() as c:
            c.executemany("UPDATE outbox SET status='pending', lease_until=0 WHERE id=? AND status='inflight'", [(i,) for i in ids])

    def mark_sent(self, item_id):
        with self._tx() as c:
            row = c.execute("SELECT payload FROM outbox WHERE id=?", (item_id,)).fetchone()
            c.execute("UPDATE outbox SET status='sent', sent_at=?, lease_until=0, last_error=NULL WHERE id=?", (self.clock(), item_id))
        if row:
            self._drop_spool(json.loads(row["payload"]))

    def mark_failed(self, item_id, error, permanent=False, retry_after=0, backoff_base=5, max_backoff=3600):
        """شکست: بعد از max_attempts (یا خطای دائمی) → dead؛ وگرنه pending با backoff نمایی + jitter."""
        err = str(error)[:200]
        with self._tx() as c:
            row = c.execute("SELECT attempts FROM outbox WHERE id=?", (item_id,)).fetchone()
            if not row:
                return "missing"
            attempts = row["attempts"] + 1
            if permanent or attempts >= self.max_attempts:
                c.execute("UPDATE outbox SET status='dead', attempts=?, last_error=?, lease_until=0 WHERE id=?", (attempts, err, item_id))
                return "dead"
            delay = min(max_backoff, backoff_base * 2 ** (attempts - 1)) * (0.8 + 0.4 * self.rng())
            c.execute("UPDATE outbox SET status='pending', attempts=?, last_error=?, lease_until=0, next_try=? WHERE id=?",
                      (attempts, err, self.clock() + max(delay, retry_after), item_id))
            return "retry"

    def _drop_spool(self, payload):
        name = payload.get("spool") if isinstance(payload, dict) else None
        if not name:
            return
        c = self._conn()
        try:   # اگر فایل هنوز مال یک آیتم دیگر است (محتوای یکسان) پاک نشود
            for r in c.execute("SELECT payload FROM outbox WHERE status IN ('pending','inflight')"):
                if json.loads(r["payload"]).get("spool") == name:
                    return
        finally:
            c.close()
        try:
            os.remove(os.path.join(self.spool, name))
        except OSError:
            pass

    def spool_path(self, payload):
        return os.path.join(self.spool, payload["spool"])

    # ---- مدیریت
    def stats(self):
        now = self.clock()
        c = self._conn()
        try:
            counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM outbox GROUP BY status")}
            old = c.execute("SELECT MIN(created) m FROM outbox WHERE status IN ('pending','inflight')").fetchone()["m"]
        finally:
            c.close()
        return {"pending": counts.get("pending", 0), "inflight": counts.get("inflight", 0), "sent": counts.get("sent", 0),
                "dead": counts.get("dead", 0), "oldest_pending_age_s": int(now - old) if old else 0}

    def retry_dead(self):
        with self._tx() as c:
            return c.execute("UPDATE outbox SET status='pending', attempts=0, next_try=?, lease_until=0 WHERE status='dead'",
                             (self.clock(),)).rowcount

    def dead_items(self, limit=5):
        c = self._conn()
        try:   # عمداً متن پیام برنگردانده می‌شود
            return [dict(r) for r in c.execute("SELECT id,attempts,last_error,created FROM outbox WHERE status='dead' ORDER BY id DESC LIMIT ?", (limit,))]
        finally:
            c.close()

    def purge(self, keep_sent_s=7 * 86400, keep_dead_s=30 * 86400):
        now = self.clock()
        with self._tx() as c:
            a = c.execute("DELETE FROM outbox WHERE status='sent' AND sent_at<?", (now - keep_sent_s,)).rowcount
            b = c.execute("DELETE FROM outbox WHERE status='dead' AND created<?", (now - keep_dead_s,)).rowcount
        return a + b

    def kv_get(self, key, default=None):
        c = self._conn()
        try:
            r = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
            return r["v"] if r else default
        finally:
            c.close()

    def kv_set(self, key, value):
        with self._tx() as c:
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, str(value)))

    def is_paused(self):
        return self.kv_get("paused", "0") == "1"
