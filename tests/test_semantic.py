import asyncio, math, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["TG_API_HASH"] = "x"
import poll, semantic
from dedupe import signature
from state import State

def vec(c):                                    # بردار با شباهتِ کسینوسیِ c به پایه [1,0,0,0]
    return [c, math.sqrt(max(0.0, 1 - c * c)), 0.0, 0.0]
BASE = vec(1.0)

class FakeEmb:
    def __init__(self, table=None, fail=False): self.provider, self.model, self.dims, self.probe = "fake", "m", 4, "ok"; self.table, self.fail, self.calls = table or {}, fail, 0
    def embed(self, texts):
        self.calls += 1
        return None if self.fail else [semantic.normalize(self.table.get(t, vec(0.0))) for t in texts]
class Judge:
    def __init__(self, verdict, ok=True): self.mode, self.verdict, self.ok, self.calls, self.stats = "on", verdict, ok, 0, {}
    def allow(self, s): return self.ok
    def judge_duplicate(self, new, olds, s): self.calls += 1; return self.verdict

def setup(table, warm=True, fail=False, cal=None):
    for k in poll.STATS: poll.STATS[k] = 0
    poll.SEM.update({"emb": FakeEmb(table, fail), "mem": semantic.Memory(), "cal": cal or semantic.Calibrator(), "mode": "on", "vec": None})
    if warm:
        for i in range(35):
            poll.SEM["mem"].add(semantic.normalize([0, 0, 1, i + 1]), f"filler {i}", signature(f"موضوع نامرتبط شماره {i} بدون ربط"))
def put(text, v=BASE):
    poll.SEM["mem"].add(semantic.normalize(v), text, signature(text))
def run(reason, text, judge=None):
    poll.AIC = judge
    st = State(tempfile.mkdtemp() + "/s.json")
    return asyncio.run(poll.apply_semantic(reason, signature(text), text, st))

OLD = "حمله پهپادی به اصفهان؛ دو نفر زخمی شدند"
NEW_SAME = "اصفهان هدف حمله‌ی پهپادی قرار گرفت؛ ۲ مصدوم"
NEW_DIFF = "حمله پهپادی به اصفهان؛ دو نفر زخمی شدند و ساختمان تخریب شد"

# ۱) شباهت بالا → تکراری (حتی اگر روش واژگانی نگفته بود)
setup({NEW_SAME: vec(0.96)}); put(OLD)
assert run(None, NEW_SAME) == ("duplicate", True) and poll.STATS["sem_caught"] == 1 and poll.STATS["sem_dup"] == 1
# ۲) شباهت پایین → جدید؛ تکراریِ واژگانیِ اشتباه نجات پیدا می‌کند
setup({"خبر کاملاً دیگر": vec(0.40)}); put(OLD)
assert run("duplicate", "خبر کاملاً دیگر") == (None, True) and poll.STATS["sem_rescued"] == 1
# ۳) قفل مکان: همتای پراشتباه (cos=۰٫۹۷) با مکان متفاوت کنار می‌رود → جدید
TABRIZ = "حمله پهپادی به تبریز؛ سه نفر زخمی شدند"
setup({TABRIZ: vec(0.97)}); put(OLD)
assert run("duplicate", TABRIZ) == (None, True) and poll.STATS["sem_rescued"] == 1
# ۴) بازه‌ی مبهم → LLM؛ نتیجه اعمال و برای کالیبراسیون ثبت می‌شود
setup({NEW_DIFF: vec(0.86)}); put(OLD); j = Judge(False)
assert run("duplicate", NEW_DIFF, j) == (None, True) and j.calls == 1 and poll.STATS["sem_llm"] == 1
assert poll.SEM["cal"].total() == 1
setup({NEW_SAME: vec(0.86)}); put(OLD); j = Judge(True)
assert run(None, NEW_SAME, j) == ("duplicate", True) and poll.STATS["sem_caught"] == 1
# ۵) مبهم و LLM در دسترس نیست / خطا → به روش واژگانی برمی‌گردد
setup({NEW_DIFF: vec(0.86)}); put(OLD)
assert run("duplicate", NEW_DIFF, Judge(False, ok=False)) == ("duplicate", False)
setup({NEW_DIFF: vec(0.86)}); put(OLD)
assert run("duplicate", NEW_DIFF, Judge(None)) == ("duplicate", False)
# ۶) حافظه‌ی سرد (<۳۰): به «تکراری»ِ واژگانی اعتماد می‌شود؛ ولی «تازه»ی واژگانی هم تازه می‌ماند
setup({NEW_SAME: vec(0.3)}, warm=False)
assert run("duplicate", NEW_SAME) == ("duplicate", False)
assert run(None, "متن دیگر") == (None, True)
# ۷) خطای Embedding → نظری نمی‌دهد (fail-open)
setup({}, fail=True); put(OLD)
assert run("duplicate", NEW_SAME) == ("duplicate", False) and poll.STATS["sem_errors"] == 1
# ۸) خاموش بودن
setup({NEW_SAME: vec(0.99)}); put(OLD); poll.SEM["mode"] = "off"
assert run(None, NEW_SAME) == (None, False)
# ۹) دلیل‌های غیرمرتبط دست نمی‌خورد
poll.SEM["mode"] = "on"; assert run("dropword", NEW_SAME) == ("dropword", False)

# ---- کالیبراسیون: از برچسب‌های LLM آستانه‌ها خودکار جابه‌جا می‌شوند
c = semantic.Calibrator(); assert c.thresholds() == (0.80, 0.93)
for _ in range(12): c.add(0.88, True)                 # در ۰٫۸۸ همیشه «تکراری»
for _ in range(12): c.add(0.74, False)                # در ۰٫۷۴ همیشه «جدید»
lo, hi = c.thresholds(); assert hi <= 0.90 and lo >= 0.76, (lo, hi)
c2 = semantic.Calibrator()
for _ in range(12): c2.add(0.90, False)               # در ۰٫۹۰ هم «جدید» می‌شود → آستانه‌ی بالا نباید پایین بیاید
for _ in range(12): c2.add(0.96, True)
assert c2.thresholds()[1] >= 0.94
c3 = semantic.Calibrator(); [c3.add(0.9, True) for _ in range(3)]; assert c3.thresholds() == (0.80, 0.93)   # نمونه‌ی کم: پیش‌فرض
# ---- حافظه: پنجره‌ی ۱۲ ساعته و سقف
m = semantic.Memory(); m.add(BASE, "a", signature("الف"), now=time.time() - 13 * 3600); m.add(BASE, "b", signature("ب")); assert [e["text"] for e in m.items] == ["b"]
# ---- Embedder: پاسخ ناقص → خطا (None)، نه کرش
e = semantic.Embedder(); e.provider = "cloudflare"
e._cf = lambda t: (_ for _ in ()).throw(RuntimeError("x")); assert e.embed(["a"]) is None
e._cf = lambda t: [[3.0, 4.0]] * len(t); out = e.embed(["a", "b"]); assert abs(out[0][0] - 0.6) < 1e-9 and len(out) == 2
poll.AIC = None; poll.SEM.update({"emb": None, "mode": "off", "vec": None})
print("SEMANTIC OK")
