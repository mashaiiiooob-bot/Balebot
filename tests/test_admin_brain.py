import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import requests
import admin_brain as AB
from dedupe import compare as dcompare, context as dctx, signature

D = AB.DIMS


class Clock:
    def __init__(self, t=1_800_000_000.0): self.t = t
    def __call__(self): return self.t


class R:
    def __init__(self, code=200, data=None): self.status_code = code; self._d = data if data is not None else {"ok": True}
    def json(self):
        if self._d is ValueError: raise ValueError("x")
        return self._d


class Sess:
    def __init__(self, *rs): self.rs, self.calls = list(rs), []
    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers, timeout)); r = self.rs.pop(0) if self.rs else R()
        if isinstance(r, Exception): raise r
        return r


def mk(sess=None, **kw):
    clk = Clock(); b = AB.Brain("https://x.supabase.co/functions/v1/balebot-admin", "K" * 40, session=sess or Sess(), clock=clk, **kw); return b, clk


VEC = [0.5] * D
NOW = 1_800_000_000.0
SIG = signature("انفجار در اصفهان ۲ کشته داشت")
CTX = ({}, 0)

# ---- from_env: پیش‌فرض خاموش؛ shadow/on فقط با URL و کلید؛ مشتق‌گیری URL از Edge Function قبلی
for k in ("ADMIN_BRAIN", "ADMIN_BRAIN_URL", "SUPABASE_INGEST_URL", "SUPABASE_INGEST_KEY"): os.environ.pop(k, None)
assert not AB.from_env().enabled
os.environ["SUPABASE_INGEST_URL"] = "https://p.supabase.co/functions/v1/balebot-ingest"; os.environ["SUPABASE_INGEST_KEY"] = "K" * 40
assert not AB.from_env().enabled                                    # ADMIN_BRAIN تنظیم نشده = off
os.environ["ADMIN_BRAIN"] = "bogus"; assert not AB.from_env().enabled
os.environ["ADMIN_BRAIN"] = "shadow"; b = AB.from_env(); assert b.enabled and b.mode == "shadow" and not b.live
assert b.url == "https://p.supabase.co/functions/v1/balebot-admin"
os.environ["ADMIN_BRAIN"] = "on"; assert AB.from_env().live
os.environ["ADMIN_BRAIN_URL"] = "https://custom/fn"; assert AB.from_env().url == "https://custom/fn"
del os.environ["SUPABASE_INGEST_KEY"]; assert not AB.from_env().enabled      # بدون کلید = خاموش
n = AB.NullBrain(); assert n.match() is None and n.remember() is None and n.flush_sync() is True and not n.live and not n.warm
for k in ("ADMIN_BRAIN", "ADMIN_BRAIN_URL", "SUPABASE_INGEST_URL"): os.environ.pop(k, None)

# ---- match: پارامترها، جریمه‌ی قدیمی، veto مکان، گرم‌شدن
rows = [
    {"id": 1, "age_s": 600, "text": "خبر جدید", "sig": {"w": SIG["w"], "n": SIG["n"], "p": SIG["p"]}, "sim": 0.95},
    {"id": 2, "age_s": 30 * 3600, "text": "خبر دیروز", "sig": {"w": SIG["w"], "n": SIG["n"], "p": SIG["p"]}, "sim": 0.96},
    {"id": 3, "age_s": 100, "text": "تبریز", "sig": signature("انفجار در تبریز ۲ کشته داشت"), "sim": 0.99},     # مکان متفاوت → veto
]
s = Sess(R(200, {"ok": True, "window_sent": 45, "rows": rows}))
b, _ = mk(s)
res = b.match(VEC, "m1", SIG, NOW, dcompare, CTX)
call = s.calls[0]
assert call[1]["kind"] == "match" and call[1]["model"] == "m1" and call[1]["hours"] == 72 and len(call[1]["vec"]) == D
assert call[2] == {"x-ingest-key": "K" * 40}
assert [e["id"] for _, e in res] == [1, 2]                           # ردیف ۳ به‌خاطر تضاد مکان حذف شد
assert abs(res[0][0] - 0.95) < 1e-9 and res[0][1]["old"] is False
assert abs(res[1][0] - 0.93) < 1e-9 and res[1][1]["old"] is True      # ۰.۹۶ − جریمه‌ی ۰.۰۳ برای پیام قدیمی
assert b.warm is True

# ---- match: ابعاد نامعتبر = بدون تماس شبکه؛ خطا = None و circuit breaker
s = Sess(); b, clk = mk(s)
assert b.match([0.1] * 10, "m", SIG, NOW, dcompare, CTX) == [] and b.match(VEC, None, SIG, NOW, dcompare, CTX) == [] and not s.calls
s = Sess(requests.ConnectionError("https://secret-url"), R(200, {"ok": True, "window_sent": 0, "rows": []}))
b, clk = mk(s)
assert b.match(VEC, "m", SIG, NOW, dcompare, CTX) is None and b.last_error == "ConnectionError" and "secret" not in b.last_error
assert b.match(VEC, "m", SIG, NOW, dcompare, CTX) is None and len(s.calls) == 1      # در بازه‌ی توقف تماس تازه‌ای نیست
clk.t += 30; assert b.match(VEC, "m", SIG, NOW, dcompare, CTX) == [] and b.last_error is None   # بعد از backoff برمی‌گردد
b, clk = mk(Sess(R(401, {"ok": False}))); assert b.match(VEC, "m", SIG, NOW, dcompare, CTX) is None
clk.t += 300; assert b._paused()                                      # ۴۰۱ = ۱۰ دقیقه توقف
b, _ = mk(Sess(R(200, {"ok": False}))); assert b.match(VEC, "m", SIG, NOW, dcompare, CTX) is None
b, _ = mk(Sess(R(200, ValueError))); assert b.match(VEC, "m", SIG, NOW, dcompare, CTX) is None

# ---- remember + flush
s = Sess(); b, clk = mk(s, batch=2)
b.remember("src1", 10, "متن خبر", {"w": ["a"]}, [0.123456789] * D, "m1", "sent")
b.remember("src1", 11, "   ", {}, None, None, "sent")                        # متن خالی نادیده گرفته می‌شود
b.remember("src1", 12, "ردشده", {"w": ["a"]}, None, None, "rejected", "duplicate", dup_of=7, score=0.97)
b.remember("src1", 13, "x", {}, None, None, "weird")                         # outcome نامعتبر
b.remember("src1", 14, "سه", {}, [1.0] * 5, "m1", "sent")                    # بردار با ابعاد غلط: بدون بردار ذخیره می‌شود
assert len(b.q) == 3
assert b.flush_sync() is True and not b.q
sent = [c[1]["rows"] for c in s.calls]
assert [len(x) for x in sent] == [2, 1] and all(c[1]["kind"] == "remember" for c in s.calls)
assert sent[0][0]["vec"][0] == 0.12346 and sent[0][0]["model"] == "m1"          # گردکردن برای کم‌کردن حجم
assert "vec" not in sent[0][1] and sent[0][1]["dup_of"] == 7 and sent[0][1]["score"] == 0.97
assert "vec" not in sent[1][0]
# خطای ۵xx: صف حفظ می‌شود؛ ۴۰۰: دسته‌ی معیوب دور ریخته می‌شود
s = Sess(R(500, {"ok": False})); b, clk = mk(s)
b.remember("s", 1, "الف", {}, None, None, "sent"); assert b.flush_sync() is False and len(b.q) == 1
clk.t += 60; s.rs = [R(400, {"ok": False})]; assert b.flush_sync() is True and not b.q and b.c["dropped_bad"] == 1
# سرریز صف
b, _ = mk(cap=3)
for i in range(5): b.remember("s", i, f"t{i}", {}, None, None, "sent")
assert len(b.q) == 3 and b.c["dropped_overflow"] == 2

# ---- merge: ادغام بدون متن تکراری، نزولی، سقف k
m = AB.merge([(0.9, {"text": "الف"}), (0.8, {"text": "ب"})], [(0.95, {"text": "الف"}), (0.85, {"text": "ج"}), (0.5, {"text": "د"})])
assert [(c, e["text"]) for c, e in m] == [(0.95, "الف"), (0.85, "ج"), (0.8, "ب")]

# ---- filter_pairs: تضاد مکان حذف می‌شود؛ بدون امضا به بازبین نشان داده می‌شود
sg = lambda t: signature(t)
pairs = [{"a_sig": sg("انفجار در اصفهان ۲ کشته داشت"), "b_sig": sg("انفجار در تبریز ۲ کشته داشت"), "sim": .95, "a_text": "", "b_text": ""},
         {"a_sig": sg("انفجار در اصفهان ۲ کشته داشت"), "b_sig": sg("انفجار شدید در اصفهان ۲ کشته داشت"), "sim": .95, "a_text": "", "b_text": ""},
         {"a_sig": {}, "b_sig": {}, "sim": .9, "a_text": "", "b_text": ""}]
assert len(AB.filter_pairs(pairs, dcompare)) == 2
rep = AB.render_report({"stats": {"total": 10, "sent": 4, "dup": 3, "no_emb": 1}}, [{"sim": .9, "a_text": "الف" * 3, "b_text": "ب" * 3}], 48, .86)
assert "ادمین هوشمند" in rep and "الفالفالف" in rep

# ================= یکپارچگی با poll.apply_semantic =================
import poll
from state import State
from semantic import Memory, Calibrator

# وضعیت سراسریِ poll را ذخیره می‌کنیم و در پایان برمی‌گردانیم تا تست‌های دیگر (در همان پروسه‌ی pytest) آلوده نشوند
_SAVED = (poll.BRAIN, dict(poll.ANALYST), poll.AIC, dict(poll.SEM), dict(poll.STATS), dict(poll.GATE_IMP))


class FakeEmb:
    provider, model = "cloudflare", "m1"
    def embed(self, texts): return [VEC for _ in texts]


class FakeBrain:
    enabled = True
    def __init__(self, mode, result): self.mode, self.result, self.warm, self.calls = mode, result, True, 0
    @property
    def live(self): return self.mode == "on"
    def match(self, *a): self.calls += 1; return self.result


def setup(brain, analyst=False):
    poll.BRAIN = brain; poll.ANALYST["on"] = analyst; poll.AIC = None
    poll.SEM.update({"mode": "on", "emb": FakeEmb(), "mem": Memory(), "cal": Calibrator()})
    for k in ("sem_dup", "sem_new", "brain_errors", "brain_shadow_would_dup", "sem_calls"): poll.STATS[k] = 0
    return State(tempfile.mkdtemp() + "/s.json")


def entry(old=False, sim_text="انفجار در اصفهان ۲ کشته داشت"): return {"text": sim_text, "t": NOW, "sig": {}, "id": 99, "db": True, "old": old}


run = lambda st: asyncio.run(poll.apply_semantic("duplicate", SIG, "انفجار در اصفهان ۲ کشته داشت", st))

# حالت on: همتای تازه‌ی فقط-در-دیتابیس (RAM بعد از ریستارت خالی است) با شباهت ≥ t_high → تکراری
st = setup(FakeBrain("on", [(0.97, entry())]))
assert run(st) == ("duplicate", True) and poll.SEM["top"] == (0.97, 99) and poll.STATS["sem_dup"] == 1
# حالت on: همتای قدیمی (>۱۲ ساعت) حتی با شباهت بالا خودکار حذف نمی‌شود (تحلیل‌گر خاموش → تصمیم با روش واژگانی)
st = setup(FakeBrain("on", [(0.99, entry(old=True))]))
r = run(st); assert r == ("duplicate", False) and poll.STATS["sem_dup"] == 0 and poll.SEM["olds"]    # به‌عنوان راهنما به تحلیل‌گر می‌رسد
# حالت shadow: تصمیم دقیقاً مثل قبل (بدون همتا و حافظه‌ی سرد = اعتماد به روش واژگانی)، فقط شمارنده بالا می‌رود
st = setup(FakeBrain("shadow", [(0.97, entry())]))
assert run(st) == ("duplicate", False) and poll.STATS["brain_shadow_would_dup"] == 1 and poll.STATS["sem_dup"] == 0
# خطا/توقف دیتابیس (None): fail-open به رفتار فعلی
st = setup(FakeBrain("on", None))
assert run(st) == ("duplicate", False) and poll.STATS["brain_errors"] == 1
# ADMIN_BRAIN خاموش (NullBrain): هیچ تغییری
st = setup(AB.NullBrain()); assert run(st) == ("duplicate", False) and poll.STATS["brain_errors"] == 0
# on و همتای تازه: reason=None (واژگانی نگفته تکراری) ولی دیتابیس همتا دارد → گرفته می‌شود
st = setup(FakeBrain("on", [(0.97, entry())])); assert asyncio.run(poll.apply_semantic(None, SIG, "متن", st)) == ("duplicate", True)

# brain_remember: دلیل‌های زودرس ثبت نمی‌شوند؛ dup_of از همتای برتر می‌آید
class Rec(AB.NullBrain):
    enabled = True
    def __init__(self): self.rows = []
    def remember(self, *a, **k): self.rows.append((a, k))
class M: id = 5
poll.BRAIN = rec = Rec(); poll.SEM.update({"vec": VEC, "top": (0.97, 99), "emb": FakeEmb()}); poll.GATE_IMP["v"] = 3
poll.brain_remember("key" * 5, M(), "متن", SIG, "rejected", "ad"); assert not rec.rows
poll.brain_remember("key" * 5, M(), "متن", SIG, "rejected", "duplicate")
a, k = rec.rows[0]; assert a[1] == 5 and a[6] == "rejected" and k["dup_of"] == 99 and k["score"] == 0.97 and k["importance"] == 3 and a[4] == VEC and a[5] == "m1"
poll.brain_remember("key" * 5, M(), "متن", SIG, "sent", None); assert rec.rows[1][1]["dup_of"] is None
poll.BRAIN, poll.AIC = _SAVED[0], _SAVED[2]
for _d, _v in ((poll.ANALYST, _SAVED[1]), (poll.SEM, _SAVED[3]), (poll.STATS, _SAVED[4]), (poll.GATE_IMP, _SAVED[5])):
    _d.clear(); _d.update(_v)
print("admin_brain tests ok")
