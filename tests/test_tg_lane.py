import asyncio, json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import requests
from state import State
import telegram_out as T
from telegram_out import TelegramBot, TelegramLane, FanoutBale, TgError

class Clock:
    def __init__(self, t=1_000_000.0): self.t = t
    def __call__(self): return self.t

class FakeBot:
    def __init__(self): self.calls = []; self.fail = []      # fail: صف خطاهایی که پشت‌سرهم پرتاب می‌شوند
    def _maybe(self):
        if self.fail:
            e = self.fail.pop(0)
            if e: raise e
    def send_text(self, chat, text): self._maybe(); self.calls.append(("text", chat, text))
    def send_media(self, chat, path, caption, kind): self._maybe(); self.calls.append(("media", chat, open(path, "rb").read(), caption, kind))

def mkstate():
    return State(os.path.join(tempfile.mkdtemp(), "state.json"))

async def drain(lane, clk, seconds=0, max_loops=400):
    """lane.run() را اجرا می‌کند تا صف خالی شود؛ زمان و sleep ساختگی‌اند."""
    sleeps = []
    async def fake_sleep(t): sleeps.append(round(t, 2)); clk.t += t; await asyncio.sleep(0)
    lane._sleep = fake_sleep; lane._stop = False
    task = asyncio.create_task(lane.run())
    for _ in range(max_loops):
        await asyncio.sleep(0.005)
        if not lane._dq: break
    await asyncio.sleep(0.05); lane.stop(); await asyncio.wait_for(task, 5)
    return sleeps

def run(c): return asyncio.run(c)

# ---------- ارسال متن، ضدتکرار، ماندگاری ----------
st = mkstate(); clk = Clock(); bot = FakeBot(); lane = TelegramLane(bot, "@dest", st, clock=clk, min_interval=3.5)
assert lane.submit_text("خبر ۱") and not lane.submit_text("خبر ۱")                     # در صف تکراری نیست
assert lane.submit_text("خبر ۲")
sl = run(drain(lane, clk)); assert [c[2] for c in bot.calls] == ["خبر ۱", "خبر ۲"] and all(c[1] == "@dest" for c in bot.calls)
assert 3.5 in sl                                                                       # فاصله‌ی بین دو ارسال (سقف ~۲۰ در دقیقه)
assert lane.counters["sent"] == 2 and st.dirty and len(st.data["tg_seen"]) == 2
assert not lane.submit_text("خبر ۱")                                                  # بعد از ارسال هم تکراری نیست
# ری‌استارت: state ذخیره و دوباره خوانده می‌شود → باز هم تکراری نیست
st.save(); st2 = State(st.path); lane2 = TelegramLane(FakeBot(), "@dest", st2, clock=clk)
assert len(st2.data["tg_seen"]) == 2 and not lane2.submit_text("خبر ۲") and lane2.submit_text("خبر ۳")
assert "خبر" not in json.dumps(st2.data["tg_seen"], ensure_ascii=False)                # متن پیام در state نیست
print("lane basic OK")

# ---------- خطا: backoff، retry_after، بدون از دست رفتن ----------
st = mkstate(); clk = Clock(); bot = FakeBot(); lane = TelegramLane(bot, "@d", st, clock=clk, min_interval=0)
bot.fail = [TgError("x", status=429, retry_after=40), TgError("x", status=403), None]
lane.submit_text("A"); sl = run(drain(lane, clk))
assert [c[2] for c in bot.calls] == ["A"] and lane.counters["failed_attempts"] == 2 and lane.counters["sent"] == 1
assert max(sl) >= 10 and 30 in sl                                                      # انتظار (≤30 تکه‌تکه) برای retry_after=40 و backoff
assert lane.last_error is None and lane.last_ok                                        # پس از موفقیت خطا پاک می‌شود
# خرابی دائمی: بعد از max_age حذف می‌شود و چیزی دیگر را نمی‌بندد
st = mkstate(); clk = Clock(); bot = FakeBot(); lane = TelegramLane(bot, "@d", st, clock=clk, min_interval=0, max_age_s=600)
bot.fail = [TgError("x", status=403)] * 50
lane.submit_text("پیام خراب"); run(drain(lane, clk, max_loops=800))
assert lane.counters["dropped_age"] == 1 and lane.status()["queued"] == 0 and bot.calls == []
# سقف صف: قدیمی‌ترین‌ها حذف می‌شوند
st = mkstate(); lane = TelegramLane(FakeBot(), "@d", st, cap=3)
for i in range(5): lane.submit_text(f"m{i}")
assert lane.status()["queued"] == 3 and lane.counters["dropped_overflow"] == 2 and [x["text"] for x in lane._dq] == ["m2", "m3", "m4"]
print("lane errors OK")

# ---------- رسانه ----------
st = mkstate(); clk = Clock(); bot = FakeBot(); lane = TelegramLane(bot, "@d", st, clock=clk, min_interval=0)
d = tempfile.mkdtemp(); f = os.path.join(d, "a.jpg"); open(f, "wb").write(b"IMG")
assert lane.submit_media(f, "cap", "photo") and not lane.submit_media(f, "cap", "photo")
os.remove(f)                                                                           # فایل اصلی پاک شد؛ کپی لِین کافی است
run(drain(lane, clk)); assert bot.calls == [("media", "@d", b"IMG", "cap", "photo")]
assert os.listdir(lane._tmp) == []                                                     # کپی موقت بعد از ارسال پاک می‌شود
print("lane media OK")

# ---------- TelegramBot: درخواست‌ها، تقسیم متن، خطاها (بدون نشت توکن) ----------
class R:
    def __init__(self, code, body): self.status_code = code; self._b = body
    def json(self):
        if self._b is ValueError: raise ValueError("x")
        return self._b
class S:
    def __init__(self, *rs): self.rs = list(rs); self.calls = []
    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append((url, data, files is not None))
        r = self.rs.pop(0)
        if isinstance(r, Exception): raise r
        return r
ok = R(200, {"ok": True, "result": {"message_id": 1}})
s = S(ok, ok, ok); b = TelegramBot("TOK123", session=s); b.send_text("@c", ("خط\n" * 2500))
assert len(s.calls) == 2 and s.calls[0][0] == "https://api.telegram.org/botTOK123/sendMessage"
assert s.calls[0][1]["disable_web_page_preview"] == "true" and "parse_mode" not in s.calls[0][1]
f = os.path.join(d, "v.mp4"); open(f, "wb").write(b"V")
s = S(ok, ok); b = TelegramBot("TOK123", session=s); b.send_media("@c", f, "x" * 1500, "video")
assert s.calls[0][0].endswith("/sendVideo") and "caption" not in s.calls[0][1] and s.calls[1][0].endswith("/sendMessage")   # کپشن بلند: جدا
s = S(ok); TelegramBot("TOK123", session=s).send_media("@c", f, "cap", "weird"); assert s.calls[0][0].endswith("/sendDocument") and s.calls[0][1]["caption"] == "cap"
for resp, status, ra in [(R(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 17}}), 429, 17),
                         (R(403, {"ok": False, "error_code": 403, "description": "Forbidden: bot is not a member"}), 403, 0),
                         (R(502, ValueError), 502, 0), (R(400, {"ok": False, "parameters": {"retry_after": "bad"}}), 400, 0)]:
    try: TelegramBot("TOK123", session=S(resp)).send_text("@c", "x"); assert False
    except TgError as e: assert (e.status, e.retry_after) == (status, ra) and "TOK123" not in str(e) and "Forbidden" not in str(e), str(e)
try: TelegramBot("TOK123", session=S(requests.ConnectionError("https://api.telegram.org/botTOK123/x"))).send_text("@c", "x"); assert False
except TgError as e: assert "TOK123" not in str(e) and "ConnectionError" in str(e)
print("bot OK")

# ---------- FanoutBale ----------
class Real:
    def __init__(self): self.sent = []; self.fail = None
    def send_text(self, c, t):
        if self.fail: raise self.fail
        self.sent.append((c, t))
    def send_media(self, c, p, cap, k):
        if self.fail: raise self.fail
        self.sent.append((c, open(p, "rb").read(), cap, k))
    def get_me(self): return {"id": 1}
st = mkstate(); clk = Clock(); tg = FakeBot(); lane = TelegramLane(tg, "@tg", st, clock=clk, min_interval=0); real = Real()
fb = FanoutBale(real, lane, "@maklpo", signature="— SIG", tg_signature="— TGSIG")
fb.send_text("@maklpo", "خبر\n\n— SIG"); fb.send_text("999", "⚠️ هشدار مدیر")                          # چت مدیر به تلگرام نمی‌رود
assert real.sent == [("@maklpo", "خبر\n\n— SIG"), ("999", "⚠️ هشدار مدیر")] and lane.status()["queued"] == 1
assert fb.get_me() == {"id": 1}                                                                  # بقیه‌ی متدها به کلاینت واقعی
# قطع بله: خطا همان‌طور که هست بالا می‌آید، ولی تلگرام یک‌بار می‌گیرد؛ تلاش مجدد بله تلگرام را تکرار نمی‌کند
real.fail = RuntimeError("Bale HTTP 404 on sendMessage")
try: fb.send_text("@maklpo", "خبر دوم\n\n— SIG"); assert False
except RuntimeError: pass
assert lane.status()["queued"] == 2
for _ in range(3):
    try: fb.send_text("@maklpo", "خبر دوم\n\n— SIG")
    except RuntimeError: pass
assert lane.status()["queued"] == 2 and lane.counters["skipped_duplicate"] == 3               # بدون تکرار
real.fail = None; fb.send_text("@maklpo", "خبر دوم\n\n— SIG"); assert real.sent[-1][1].startswith("خبر دوم")
run(drain(lane, clk)); assert [c[2] for c in tg.calls] == ["خبر\n\n— TGSIG", "خبر دوم\n\n— TGSIG"]   # امضای تلگرام جایگزین شد
f = os.path.join(d, "p.png"); open(f, "wb").write(b"PNG"); fb.send_media("@maklpo", f, "cap\n\n— SIG", "photo")
run(drain(lane, clk)); assert tg.calls[-1] == ("media", "@tg", b"PNG", "cap\n\n— TGSIG", "photo") and real.sent[-1][1:] == (b"PNG", "cap\n\n— SIG", "photo")
print("fanout OK")

# ---------- جداسازی: خرابی تلگرام هرگز بله را نمی‌شکند ----------
class Boom:
    counters = __import__("collections").Counter()
    def submit_text(self, t): raise RuntimeError("lane exploded")
    def submit_media(self, p, c, k): raise OSError("disk gone")
real = Real(); fb = FanoutBale(real, Boom(), "@maklpo")
fb.send_text("@maklpo", "x"); fb.send_media("@maklpo", f, "c", "photo")
assert [r[0] for r in real.sent] == ["@maklpo", "@maklpo"] and Boom.counters["submit_errors"] == 2   # بله ارسال شد
# حلقه‌ی بسته: submit خطا نمی‌دهد
lane = TelegramLane(FakeBot(), "@d", mkstate()); asyncio.run(drain(lane, Clock())); assert lane.submit_text("پس از بسته شدن حلقه")
print("isolation OK")

# ---------- from_env ----------
os.environ.pop("TG_BOT_TOKEN", None); os.environ.pop("TG_DESTINATION", None)
assert TelegramLane.from_env(mkstate()) is None                                              # بدون تنظیمات خاموش است
os.environ["TG_BOT_TOKEN"] = "T"; assert TelegramLane.from_env(mkstate()) is None
os.environ["TG_DESTINATION"] = "@x"; os.environ["TG_MAX_AGE_HOURS"] = "2"; l = TelegramLane.from_env(mkstate()); assert l.chat == "@x" and l.max_age_s == 7200
print("ALL TG LANE OK")
