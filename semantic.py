"""تشخیص تکراری «معنایی»: Embedding چندزبانه (Cloudflare BGE‑M3 یا Gemini، رایگان) + قضاوت LLM فقط در بازه‌ی مبهم + کالیبراسیون خودکار.
- بردارها فقط در حافظه‌اند (هرگز در ریپو ذخیره نمی‌شوند)؛ بعد از ریستارت از آخرین پست‌های خود کانال دوباره ساخته می‌شوند.
- قفل مکان/عدد (dedupe.compare) همیشه veto قطعی است، چون Embedding «اصفهان» و «تبریز» را بسیار شبیه می‌بیند.
- هر خطا = برگشت به روش واژگانی (fail-open). کلیدها هرگز لاگ نمی‌شوند."""
import math
import os
import re
import time

import requests

CF_MODEL = "@cf/baai/bge-m3"
GL = "https://generativelanguage.googleapis.com/v1beta"
WINDOW = 12 * 3600
CAP = 400
BATCH = 40


def normalize(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


class Embedder:
    def __init__(self):
        self.cf_token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        self.cf_acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        self.gem_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self.provider = None
        self.model = None
        self.dims = None
        self.probe = "no_keys"
        self._gem_model = None

    # ---------- سرویس‌ها ----------
    def _cf(self, texts):
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_acct}/ai/run/{CF_MODEL}"
        r = requests.post(url, json={"text": texts}, headers={"Authorization": f"Bearer {self.cf_token}"}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"cf_http_{r.status_code}")
        data = r.json()
        vecs = (data.get("result") or {}).get("data")
        if not vecs or len(vecs) != len(texts):
            raise RuntimeError("cf_bad_payload")
        return vecs

    def _gem(self, texts):
        extra = {"outputDimensionality": 1024} if "gemini-embedding" in (self._gem_model or "") else {}   # سازگار با ستون vector(1024)
        body = {"requests": [{"model": f"models/{self._gem_model}", "content": {"parts": [{"text": t}]},
                              "taskType": "SEMANTIC_SIMILARITY", **extra} for t in texts]}
        r = requests.post(f"{GL}/models/{self._gem_model}:batchEmbedContents", json=body,
                          headers={"x-goog-api-key": self.gem_key}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"gem_http_{r.status_code}")
        embs = r.json().get("embeddings")
        if not embs or len(embs) != len(texts):
            raise RuntimeError("gem_bad_payload")
        return [e["values"] for e in embs]

    def _pick_gem_model(self):
        try:
            r = requests.get(f"{GL}/models?pageSize=200", headers={"x-goog-api-key": self.gem_key}, timeout=20)
            names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                     if "embedding" in m["name"] and "embedContent" in m.get("supportedGenerationMethods", [])] if r.status_code == 200 else []
            self._gem_model = sorted(names)[-1] if names else "gemini-embedding-001"
        except Exception:
            self._gem_model = "gemini-embedding-001"

    def init(self):
        errors = []
        if self.cf_token and self.cf_acct:
            try:
                v = self._cf(["آزمایش"])
                self.provider, self.model, self.dims, self.probe = "cloudflare", CF_MODEL, len(v[0]), "ok"
                return
            except Exception as exc:
                errors.append(str(exc)[:20] if str(exc).startswith("cf_") else type(exc).__name__)
        if self.gem_key:
            self._pick_gem_model()
            try:
                v = self._gem(["آزمایش"])
                self.provider, self.model, self.dims, self.probe = "gemini", self._gem_model, len(v[0]), "ok"
                return
            except Exception as exc:
                errors.append(str(exc)[:20] if str(exc).startswith("gem_") else type(exc).__name__)
        self.probe = ",".join(errors) or "no_keys"

    def embed(self, texts):
        """لیست بردارِ نرمال‌شده یا None (خطا). متن‌ها به دسته‌های کوچک تقسیم می‌شوند."""
        if not self.provider:
            return None
        out = []
        try:
            for i in range(0, len(texts), BATCH):
                chunk = [t[:1500] for t in texts[i:i + BATCH]]
                vecs = self._cf(chunk) if self.provider == "cloudflare" else self._gem(chunk)
                out.extend(normalize(v) for v in vecs)
            return out
        except Exception:
            return None


class Calibrator:
    """از جفت‌های (شباهت، قضاوت LLM) آستانه‌ها را خودکار تنظیم می‌کند: بالاتر از t_high قطعاً تکراری، پایین‌تر از t_low قطعاً جدید."""
    DEFAULT = (0.80, 0.93)
    STEP = 0.02

    def __init__(self, data=None):
        self.b = {k: list(v) for k, v in (data or {}).items()}

    def _key(self, cos):
        return f"{math.floor(cos / self.STEP) * self.STEP:.2f}"

    def add(self, cos, dup):
        rec = self.b.setdefault(self._key(cos), [0, 0])
        rec[0 if dup else 1] += 1

    def thresholds(self):
        lo, hi = self.DEFAULT
        keys = sorted(self.b, key=float)
        # t_high: کوچک‌ترین ناحیه‌ی «بالا» که ≥۹۰٪ نمونه‌هایش «تکراری» باشد (حداقل ۱۰ نمونه)
        d = n = 0
        for k in reversed(keys):
            d += self.b[k][0]; n += sum(self.b[k])
            if n >= 10 and d / n >= 0.9:
                hi = float(k)
            elif n >= 10 and d / n < 0.8:
                break
        # t_low: بزرگ‌ترین ناحیه‌ی «پایین» که ≤۵٪ نمونه‌هایش «تکراری» باشد
        d = n = 0
        for k in keys:
            d += self.b[k][0]; n += sum(self.b[k])
            if n >= 10 and d / n <= 0.05:
                lo = float(k) + self.STEP
            elif n >= 10 and d / n > 0.15:
                break
        lo = min(max(lo, 0.6), 0.9)
        hi = min(max(hi, lo + 0.03), 0.99)
        return round(lo, 2), round(hi, 2)

    def total(self):
        return sum(sum(v) for v in self.b.values())


class Memory:
    """حافظه‌ی معنایی پیام‌های ارسال‌شده‌ی اخیر (فقط در RAM)."""

    def __init__(self):
        self.items = []   # {"v","text","t","sig"}

    def prune(self, now):
        self.items = [e for e in self.items if now - e["t"] <= WINDOW][-CAP:]

    def add(self, vec, text, sig, now=None):
        now = now or time.time()
        self.items.append({"v": vec, "text": text[:700], "t": now, "sig": sig})
        self.prune(now)

    def warm(self):
        return len(self.items) >= 30

    def top(self, vec, sig, now, compare, ctx, k=3):
        """نزدیک‌ترین‌ها به‌جز آن‌هایی که قفل مکان/عدد دارند. خروجی: [(cos, entry)]"""
        out = []
        for e in self.items:
            if now - e["t"] > WINDOW:
                continue
            c = compare(sig, {"w": e["sig"]["w"], "n": e["sig"]["n"], "p": e["sig"].get("p", [])}, ctx)
            if c["conflict"]:
                continue   # مکان یا عدد متفاوت = واقعه‌ی دیگر (veto قطعی)
            out.append((cosine(vec, e["v"]), e))
        out.sort(key=lambda x: -x[0])
        return out[:k]
