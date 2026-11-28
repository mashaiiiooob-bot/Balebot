"""ادمین هوشمند: حافظه‌ی چندروزه‌ی پیام‌ها در Supabase (pgvector) برای جلوگیری از ورود پیام تکراری.

نقش:
- هر پیامی که به مرحله‌ی تشخیص معنایی می‌رسد (ارسال‌شده یا ردشده) با متن پاک‌شده، امضا و بردار ذخیره می‌شود (`remember`).
- پیام تازه با پیام‌های «ارسال‌شده»ی چند روز اخیر (نه فقط ۱۲ ساعت RAM) مقایسه می‌شود (`match`) و نامزدها به همان منطق
  موجود (قفل مکان/عدد، آستانه‌های کالیبره، تحلیل‌گر LLM) داده می‌شوند. این ماژول خودش «حذف» نمی‌کند؛ فقط حافظه را بلندمدت می‌کند.
- بازبینی دوره‌ای (`audit`): جفت‌های بسیار شبیهِ هر دو ارسال‌شده را پیدا می‌کند (تکراری‌هایی که رد نشده‌اند).

اصول (مثل supa_sink):
- هرگز ارسال را کند/متوقف نمی‌کند: هر خطا = None/[] و برگشت به روش فعلی (fail-open)؛ circuit breaker با backoff.
- ذخیره‌سازی در صف حافظه و دسته‌ای در پس‌زمینه.
- سه حالت با ADMIN_BRAIN: off (پیش‌فرض) | shadow (ذخیره + اندازه‌گیری، بدون اثر روی تصمیم) | on (نامزدهای چندروزه وارد تصمیم می‌شوند).
- کلید هرگز در لاگ/خطا نمی‌آید. متن پیام فقط به Edge Function خودتان (balebot-admin) می‌رود و آنجا در جدول خصوصی می‌ماند.
"""
import asyncio
import collections
import json
import os
import sys
import threading
import time

import requests

DIMS = 1024                     # BGE-M3؛ Gemini با outputDimensionality=1024 (semantic.py)
MODES = ("off", "shadow", "on")
EARLY_REASONS = frozenset({"no_text", "emptied_by_cleaner", "ad", "dropword", "profanity", "not_news", "too_old"})


def _env_int(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(name, "") or default)))
    except ValueError:
        return default


def _env_float(name, default, lo, hi):
    try:
        return max(lo, min(hi, float(os.environ.get(name, "") or default)))
    except ValueError:
        return default


def merge(ram, db, k=3):
    """ادغام نامزدهای RAM و دیتابیس؛ بدون متن تکراری؛ بر پایه‌ی شباهت (نزولی). هر عنصر: (cos, entry)."""
    seen, out = set(), []
    for c, e in sorted(list(ram) + list(db), key=lambda x: -x[0]):
        key = (e.get("text") or "")[:200]
        if key in seen:
            continue
        seen.add(key)
        out.append((c, e))
    return out[:k]


def filter_pairs(pairs, compare, ctx=({}, 0)):
    """از جفت‌های بازبینی، آن‌هایی که قفل مکان/عدد/جزئیات «واقعه‌ی دیگر» می‌گوید حذف می‌شوند."""
    out = []
    for p in pairs:
        a = {"w": (p.get("a_sig") or {}).get("w", []), "n": (p.get("a_sig") or {}).get("n", []), "p": (p.get("a_sig") or {}).get("p", [])}
        b = {"w": (p.get("b_sig") or {}).get("w", []), "n": (p.get("b_sig") or {}).get("n", []), "p": (p.get("b_sig") or {}).get("p", [])}
        if not a["w"] or not b["w"]:
            out.append(p)               # بدون امضا نمی‌توان حذف کرد؛ به بازبین انسانی نشان داده می‌شود
            continue
        c = compare(a, b, ctx)
        if c["conflict"] or c["mismatch"]:
            continue
        out.append(p)
    return out


class NullBrain:
    enabled = False
    mode = "off"
    live = False
    warm = False

    def match(self, *a, **k): return None
    def remember(self, *a, **k): pass
    def flush_sync(self): return True
    async def run(self): return None
    def stop(self): pass
    def status_info(self): return {"enabled": False, "mode": "off"}


class Brain:
    enabled = True

    def __init__(self, url, key, mode="shadow", session=None, clock=time.time, hours=72, k=5, old_after_h=12,
                 old_margin=0.03, retention_days=14, timeout=8, flush_every=10, batch=20, cap=600, purge_every=86400):
        self.url, self.key, self.mode = url, key, mode if mode in MODES else "shadow"
        self.session = session or requests.Session()
        self.clock = clock
        self.hours, self.k, self.old_after_h, self.old_margin = hours, k, old_after_h, old_margin
        self.retention_days, self.timeout = retention_days, timeout
        self.flush_every, self.batch, self.cap, self.purge_every = flush_every, batch, cap, purge_every
        self.q = collections.deque()
        self.lock = threading.Lock()
        self.c = collections.Counter()
        self.warm = False               # آخرین match: دست‌کم ۳۰ پیام ارسال‌شده در بازه
        self.last_error = None
        self.last_ok = None
        self._pause_until = 0.0
        self._fails = 0
        self._last_purge = clock()
        self._stop = False

    @property
    def live(self):
        return self.mode == "on"

    # ---- شبکه
    def _post(self, payload, timeout=None):
        """(status_code|None, json|None). پیام خطای requests شامل URL است؛ فقط نوع خطا نگه داشته می‌شود."""
        try:
            r = self.session.post(self.url, json=payload, headers={"x-ingest-key": self.key}, timeout=timeout or self.timeout)
        except requests.RequestException as exc:
            return None, type(exc).__name__
        try:
            data = r.json()
        except Exception:                               # noqa: BLE001
            data = None
        return r.status_code, data

    def _fail(self, code):
        self._fails += 1
        self.c["errors"] += 1
        self.last_error = code if isinstance(code, str) else f"http_{code}"
        if code in (401, 403):
            self._pause_until = self.clock() + 600
        else:
            self._pause_until = self.clock() + min(300, 10 * 2 ** min(self._fails, 5))

    def _ok(self):
        self._fails, self.last_error, self.last_ok = 0, None, self.clock()

    def _paused(self):
        return self.clock() < self._pause_until

    # ---- جست‌وجو
    def match(self, vec, model, sig, now, compare, ctx):
        """نامزدهای چندروزه. خروجی: لیست [(cos_موثر, entry)] یا None (خطا/توقف → فراخواننده به روش فعلی برمی‌گردد).
        پیام‌های قدیمی‌تر از old_after_h به‌اندازه‌ی old_margin جریمه می‌شوند (خبر دیروز نباید به‌راحتی خبر امروز را حذف کند).
        قفل مکان/عدد (compare) هنوز veto قطعی است."""
        if not vec or len(vec) != DIMS or not model:
            self.c["skipped_dims"] += 1
            return []
        if self._paused():
            self.c["skipped_paused"] += 1
            return None
        code, data = self._post({"kind": "match", "vec": [round(float(x), 5) for x in vec], "model": model,
                                 "hours": self.hours, "k": self.k})
        if code != 200 or not isinstance(data, dict) or not data.get("ok"):
            self._fail(code if code is not None else (data or "error"))
            return None
        self._ok()
        self.c["matches"] += 1
        self.warm = int(data.get("window_sent") or 0) >= 30
        out = []
        for r in data.get("rows") or []:
            try:
                sg = r.get("sig") or {}
                entry_sig = {"w": sg.get("w", []), "n": sg.get("n", []), "p": sg.get("p", [])}
                if entry_sig["w"] and compare(sig, entry_sig, ctx)["conflict"]:
                    continue            # مکان یا عدد متفاوت = واقعه‌ی دیگر
                age = float(r.get("age_s") or 0)
                old = age > self.old_after_h * 3600
                cos = float(r["sim"]) - (self.old_margin if old else 0.0)
                out.append((cos, {"text": r.get("text") or "", "t": now - age, "sig": entry_sig, "id": r.get("id"), "db": True, "old": old}))
            except (KeyError, TypeError, ValueError):
                continue
        out.sort(key=lambda x: -x[0])
        return out

    # ---- ذخیره
    def remember(self, src, msg_id, text, sig, vec, model, outcome, reason=None, dup_of=None, score=None, importance=None):
        try:
            if not (text or "").strip() or outcome not in ("sent", "rejected", "failed"):
                return
            row = {"src": str(src)[:32], "msg_id": int(msg_id), "text": text[:1500], "sig": sig, "outcome": outcome,
                   "reason": (str(reason)[:40] if reason else None), "dup_of": dup_of,
                   "score": (round(float(score), 4) if score is not None else None), "importance": importance}
            if vec and len(vec) == DIMS and model:
                row["vec"], row["model"] = [round(float(x), 5) for x in vec], model
            with self.lock:
                self.q.append(row)
                while len(self.q) > self.cap:
                    self.q.popleft()
                    self.c["dropped_overflow"] += 1
        except Exception:                               # noqa: BLE001
            pass

    def flush_sync(self):
        if self._paused():
            return False
        while True:
            with self.lock:
                rows = list(self.q)[: self.batch]
            if not rows:
                return True
            code, data = self._post({"kind": "remember", "rows": rows}, timeout=20)
            if code == 200 and isinstance(data, dict) and data.get("ok"):
                with self.lock:
                    for _ in rows:
                        if self.q:
                            self.q.popleft()
                self.c["rows_sent"] += len(rows)
                self._ok()
                continue
            if code in (400, 413, 422):                 # دسته‌ی معیوب: کنار گذاشته می‌شود تا صف گیر نکند
                with self.lock:
                    for _ in rows:
                        if self.q:
                            self.q.popleft()
                self.c["dropped_bad"] += len(rows)
                self._fail(code)
                continue
            self._fail(code if code is not None else (data or "error"))
            return False

    async def run(self):
        while not self._stop:
            try:
                await asyncio.to_thread(self.flush_sync)
                if self.clock() - self._last_purge >= self.purge_every:
                    self._last_purge = self.clock()
                    await asyncio.to_thread(self._post, {"kind": "purge", "days": self.retention_days})
            except Exception:                           # noqa: BLE001
                self.c["loop_errors"] += 1
            for _ in range(int(self.flush_every)):
                if self._stop:
                    return
                await asyncio.sleep(1)

    def stop(self):
        self._stop = True

    def audit(self, model, hours=48, min_sim=0.86, limit=60):
        code, data = self._post({"kind": "audit", "model": model, "hours": hours, "min_sim": min_sim, "limit": limit}, timeout=60)
        if code != 200 or not isinstance(data, dict) or not data.get("ok"):
            return None
        return data

    def status_info(self):
        with self.lock:
            queued = len(self.q)
        return {"enabled": True, "mode": self.mode, "queued": queued, "matches": self.c["matches"], "rows_sent": self.c["rows_sent"],
                "errors": self.c["errors"], "skipped_paused": self.c["skipped_paused"], "skipped_dims": self.c["skipped_dims"],
                "dropped_bad": self.c["dropped_bad"], "dropped_overflow": self.c["dropped_overflow"], "warm": self.warm,
                "last_error": self.last_error,
                "last_ok_utc": time.strftime("%Y-%m-%d %H:%M", time.gmtime(self.last_ok)) if self.last_ok else None}


def derive_url():
    url = os.environ.get("ADMIN_BRAIN_URL", "").strip()
    if url:
        return url
    base = os.environ.get("SUPABASE_INGEST_URL", "").strip()
    return base.replace("balebot-ingest", "balebot-admin") if "balebot-ingest" in base else ""


def from_env():
    mode = (os.environ.get("ADMIN_BRAIN", "") or "off").strip().lower()
    key = os.environ.get("SUPABASE_INGEST_KEY", "").strip()
    url = derive_url()
    if mode not in ("shadow", "on") or not url or not key:
        return NullBrain()
    return Brain(url, key, mode, hours=_env_int("BRAIN_HOURS", 72, 6, 336), old_after_h=_env_int("BRAIN_OLD_AFTER_H", 12, 1, 72),
                 old_margin=_env_float("BRAIN_OLD_MARGIN", 0.03, 0.0, 0.2), retention_days=_env_int("BRAIN_RETENTION_DAYS", 14, 3, 60))


# ------------------------------------------------------------------ بازبینی (CLI): python admin_brain.py audit
def render_report(data, pairs, hours, min_sim):
    st = data.get("stats") or {}
    lines = [f"🧠 گزارش ادمین هوشمند ({hours} ساعت اخیر)",
             f"پیام ذخیره‌شده: {st.get('total', 0)} | ارسال‌شده: {st.get('sent', 0)} | ردشده به‌عنوان تکراری: {st.get('dup', 0)} | بدون بردار: {st.get('no_emb', 0)}",
             f"جفت‌های مشکوک (شباهت ≥ {min_sim}، بدون تضاد مکان/عدد): {len(pairs)}"]
    for p in pairs[:8]:
        lines.append(f"\n• شباهت {p['sim']:.2f}\n  الف: {p['a_text'][:140]}\n  ب: {p['b_text'][:140]}")
    if len(pairs) > 8:
        lines.append(f"\n… و {len(pairs) - 8} مورد دیگر")
    if not pairs:
        lines.append("✅ تکراریِ ارسال‌شده‌ای پیدا نشد.")
    return "\n".join(lines)


def run_audit():
    from dedupe import compare as dedupe_compare
    brain = from_env()
    if not brain.enabled:
        print("ADMIN_BRAIN و SUPABASE_INGEST_URL/KEY تنظیم نشده‌اند.", file=sys.stderr)
        return 2
    model = os.environ.get("BRAIN_MODEL", "@cf/baai/bge-m3").strip()
    hours = _env_int("AUDIT_HOURS", 48, 1, 96)
    min_sim = _env_float("AUDIT_MIN_SIM", 0.86, 0.5, 0.99)
    data = brain.audit(model, hours, min_sim)
    if data is None:
        print("بازبینی ناموفق بود (Edge Function در دسترس نیست یا کلید نامعتبر است).", file=sys.stderr)
        return 1
    pairs = filter_pairs(data.get("pairs") or [], dedupe_compare)
    report = render_report(data, pairs, hours, min_sim)
    st = data.get("stats") or {}
    # لاگ Actions عمومی است: فقط عدد چاپ می‌شود؛ متن پیام‌ها فقط به چت خصوصی ادمین می‌رود.
    print(f"audit: total={st.get('total', 0)} sent={st.get('sent', 0)} dup={st.get('dup', 0)} suspicious_pairs={len(pairs)}")
    chat = os.environ.get("BALE_ADMIN_CHAT", "").strip()
    token = os.environ.get("BALE_BOT_TOKEN", "").strip()
    if chat and token:
        from bale_client import BaleClient
        BaleClient(token).send_text(chat, report)
        print("audit: report sent to admin chat")
    else:
        print("audit: BALE_ADMIN_CHAT/BALE_BOT_TOKEN not set; report not delivered")
    with open("admin_audit.json", "w", encoding="utf-8") as f:
        json.dump({"stats": data.get("stats"), "pairs": [{"sim": p["sim"], "a_id": p["a_id"], "b_id": p["b_id"]} for p in pairs]}, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(run_audit() if (len(sys.argv) > 1 and sys.argv[1] == "audit") else 0)
