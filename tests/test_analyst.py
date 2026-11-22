import asyncio, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["TG_API_HASH"] = "x"
import ai as ai_mod, poll, semantic, settings
from dedupe import signature
from state import State

def A(imp=4, banned=False, ad=False, news=True, same=0, adds=False):
    return {"importance": imp, "banned": banned, "ad": ad, "newsworthy": news, "same_event_as": same, "adds_new_info": adds, "banned_why": "", "summary": "s"}

class FakeAI:
    """نتیجه‌ی تحلیل اول و دوم (نظر دوم) را از جدول می‌دهد."""
    def __init__(self, first, second=None, second_ok=True):
        self.mode, self.gate, self.stats = "on", "on", {}
        self.first, self.second, self.second_ok, self.calls = first, second, second_ok, []
    def can_analyze(self, state, exclude=()): return (self.second_ok if exclude else True)
    def analyze(self, text, olds, terms, state, exclude=()):
        self.calls.append((len(olds), tuple(exclude)))
        return (self.second, "p2") if exclude else (self.first, "p1")

def reset():
    for k in poll.STATS: poll.STATS[k] = 0
    poll.SEM.update({"olds": [], "c": None, "cal": semantic.Calibrator()}); poll.GATE_IMP["v"] = None
    poll.ANALYST["on"] = True; settings.MIN_IMPORTANCE = 4; settings.DROP_WORDS[:] = ["x"]
def run(reason, ai, text="متن خبر", sig=None):
    poll.AIC = ai
    return asyncio.run(poll.apply_analyst(reason, sig or signature(text), text, State(tempfile.mkdtemp() + "/s.json")))
asyncio.sleep_orig = asyncio.sleep

# ۱) پیام سالم و مهم → ارسال؛ اهمیت برای سقف ساعتی ثبت می‌شود
reset(); assert run(None, FakeAI(A(imp=5))) == (None, True) and poll.GATE_IMP["v"] == 5 and poll.STATS["analyst_calls"] == 1
# ۲) ممنوع: با تأیید نظر دوم حذف؛ با مخالفت نظر دوم نه؛ بدون نظر دوم حذف (ممنوعه سخت‌گیر است)
reset(); assert run(None, FakeAI(A(banned=True), A(banned=True))) == ("ai_banned", True) and poll.STATS["analyst_veto_confirmed"] == 1
reset(); assert run(None, FakeAI(A(banned=True), A(banned=False))) == (None, True) and poll.STATS["analyst_veto_overruled"] == 1
reset(); assert run(None, FakeAI(A(banned=True), None, second_ok=False)) == ("ai_banned", True)
# ۳) تبلیغ
reset(); assert run(None, FakeAI(A(ad=True), A(ad=True))) == ("ai_ad", True)
reset(); assert run(None, FakeAI(A(ad=True), A(ad=False))) == (None, True)
# ۴) تکراری: فقط وقتی هر دو «همان واقعه و بدون اطلاعات تازه» بگویند؛ نظر دوم در دسترس نیست → ارسال (پیام تازه حذف نشود)
poll.SEM["olds"] = ["خبر قبلی"]
reset(); poll.SEM["olds"] = ["خبر قبلی"]; assert run(None, FakeAI(A(same=1), A(same=1))) == ("duplicate", True)
reset(); poll.SEM["olds"] = ["خبر قبلی"]; assert run(None, FakeAI(A(same=1), A(same=1, adds=True))) == (None, True)   # نظر دوم: اطلاعات تازه دارد
reset(); poll.SEM["olds"] = ["خبر قبلی"]; assert run(None, FakeAI(A(same=1), A(same=0))) == (None, True)
reset(); poll.SEM["olds"] = ["خبر قبلی"]; assert run(None, FakeAI(A(same=1), None, second_ok=False)) == (None, True)
reset(); poll.SEM["olds"] = ["خبر قبلی"]; assert run(None, FakeAI(A(same=1, adds=True))) == (None, True)              # اطلاعات تازه: تکراری نیست
# ۵) اهمیت کم: با تأیید دوم حذف؛ اختلاف → ارسال؛ بدون نظر دوم → حذف
reset(); assert run(None, FakeAI(A(imp=2), A(imp=3))) == ("low_importance", True)
reset(); assert run(None, FakeAI(A(imp=2), A(imp=4))) == (None, True)
reset(); assert run(None, FakeAI(A(imp=2), None, second_ok=False)) == ("low_importance", True)
reset(); assert run(None, FakeAI(A(imp=4))) == (None, True)                                                         # در آستانه: ارسال
# ۶) تکراریِ واژگانی که تحلیل‌گر رد می‌کند → نجات
reset(); poll.SEM["olds"] = ["خبر قبلی"]; assert run("duplicate", FakeAI(A(same=0))) == (None, True) and poll.STATS["analyst_rescued"] == 1
# ۷) تحلیل ناموفق → تصمیم قبلی دست‌نخورده و decided=False
reset(); assert run("duplicate", FakeAI(None)) == ("duplicate", False) and poll.STATS["analyst_errors"] == 1
# ۸) خاموش/دلیل‌های دیگر
reset(); poll.ANALYST["on"] = False; assert run(None, FakeAI(A(banned=True))) == (None, False)
reset(); assert run("dropword", FakeAI(A())) == ("dropword", False)
# ۹) اولویت‌ها: ممنوع > تبلیغ > تکراری > اهمیت
reset(); poll.SEM["olds"] = ["قبلی"]; assert poll.analysis_verdict(A(imp=1, banned=True, ad=True, same=1)) == "ai_banned"
assert poll.analysis_verdict(A(imp=1, ad=True, same=1)) == "ai_ad" and poll.analysis_verdict(A(imp=1, same=1)) == "duplicate"
# ۱۰) کالیبراسیون از قضاوت تحلیل‌گر
reset(); poll.SEM["olds"] = ["قبلی"]; poll.SEM["c"] = 0.86; run(None, FakeAI(A(same=1), A(same=1))); assert poll.SEM["cal"].total() == 1
# ۱۱) سرویس‌ها شلوغ‌اند: صبر و بعد fail-open
async def fast(x): return None
class Busy(FakeAI):
    def can_analyze(self, s, exclude=()): return False
real = asyncio.sleep; asyncio.sleep = fast
reset(); assert run(None, Busy(A())) == (None, False) and poll.STATS["ai_skipped"] == 1
asyncio.sleep = real
# ۱۲) ممیزی: ردشده‌های قوانین که هوش مصنوعی می‌فرستاد شمرده می‌شود
reset(); settings.AUDIT_PER_DAY = 5
poll.AIC = FakeAI(A(imp=5)); st = State(tempfile.mkdtemp() + "/s.json")
asyncio.run(poll.audit_task("متن", "not_news", st)); assert poll.STATS["audit_not_news_n"] == 1 and poll.STATS["audit_not_news_good"] == 1
poll.AIC = FakeAI(A(banned=True)); asyncio.run(poll.audit_task("متن", "dropword", st)); assert poll.STATS["audit_dropword_n"] == 1 and "audit_dropword_good" not in poll.STATS
assert not poll.audit_wanted("duplicate") and not poll.audit_wanted("sent")
# ۱۳) اعتبارسنجی خروجی مدل: هر فیلد نامعتبر = کل پاسخ نامعتبر
ok = {"summary": "x", "importance": 3, "banned": False, "banned_why": "", "ad": False, "newsworthy": True, "same_event_as": 1, "adds_new_info": False}
assert ai_mod._analysis(ok, 2)["same_event_as"] == 1
for bad in ({**ok, "importance": 9}, {**ok, "importance": True}, {**ok, "banned": "no"}, {**ok, "same_event_as": 5}, {**ok, "same_event_as": -1},
            {**ok, "adds_new_info": 1}, {k: v for k, v in ok.items() if k != "ad"}, "text", None):
    assert ai_mod._analysis(bad, 2) is None, bad
poll.AIC = None; poll.ANALYST["on"] = False; poll.SEM["olds"] = []
print("ANALYST OK")
