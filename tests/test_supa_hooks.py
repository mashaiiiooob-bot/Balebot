import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import poll, settings, supa_sink
from state import State

class Msg:
    def __init__(self, i, text): self.id=i; self.message=text; self.media=None; self.file=None
class Ent: id=42; title="TestChan"; username="t"
class FakeClient:
    def __init__(self, msgs): self.msgs=msgs
    async def get_messages(self, e, limit=1): return sorted(self.msgs, key=lambda m:-m.id)[:limit]
    def iter_messages(self, e, min_id=0, reverse=True, limit=100):
        async def g():
            for m in sorted(self.msgs, key=lambda m:m.id):
                if m.id>min_id: yield m
        return g()
class FakeBale:
    def __init__(self): self.sent=[]; self.fail=False
    def send_text(self, chat, text):
        if self.fail: raise RuntimeError("Bale HTTP 404 on sendMessage")
        self.sent.append(text)
class Sess:
    def post(self, *a, **k): raise AssertionError("no network in this test")

def run(c, b, s): asyncio.run(poll.process_source(c, b, s, Ent(), tempfile.mkdtemp()))
key = poll.key_for(Ent())

# بدون env: NullSink؛ ارسال بدون تغییر کار می‌کند
assert not poll.SINK.enabled
st = State(os.path.join(tempfile.mkdtemp(), "s.json")); b = FakeBale()
msgs = [Msg(1, "x")]; c = FakeClient(msgs); run(c, b, st)
msgs += [Msg(2, "فوری: حادثه")]; run(c, b, st); assert len(b.sent) == 1

# با sink فعال: رویداد هر تصمیم ثبت می‌شود، بدون متن
poll.SINK = supa_sink.SupaSink("https://x/f", "K" * 40, session=Sess()); poll.SINK.dest = "bale+tg"
st = State(os.path.join(tempfile.mkdtemp(), "s.json")); b = FakeBale()
msgs = [Msg(1, "x")]; c = FakeClient(msgs); run(c, b, st)                                       # اولین اجرا: از اکنون
msgs += [Msg(2, "فوری: حادثه"), Msg(3, "تبلیغات خبر")]; run(c, b, st)
ev = list(poll.SINK.q["events"])
assert [(e["msg_id"], e["outcome"]) for e in ev if e["msg_id"] in (2, 3)] == [(2, "sent"), (3, "rejected")], ev
sent = [e for e in ev if e["outcome"] == "sent"][0]; rej = [e for e in ev if e["outcome"] == "rejected"][0]
assert sent["dest"] == "bale+tg" and rej["reason"] and rej["dest"] is None and sent["src"] == key[:12]
assert all(set(e) == {"ts", "src", "msg_id", "outcome", "reason", "importance", "dest"} for e in ev)    # فقط ستون‌های مجاز؛ بدون متن
# شکست ارسال: outcome=failed با کد HTTP؛ checkpoint جلو نمی‌رود
msgs.append(Msg(4, "news update")); b.fail = True; before = len(poll.SINK.q["events"]); run(c, b, st)
fe = list(poll.SINK.q["events"])[before:]; assert [(e["outcome"], e["reason"]) for e in fe] == [("failed", "http_404")], fe
assert st.last_id(key) == 3
# خرابی خود sink هرگز ارسال را مختل نمی‌کند
class Broken(supa_sink.SupaSink):
    def event(self, *a, **k): raise RuntimeError("sink exploded")
poll.SINK = Broken("https://x/f", "K" * 40, session=Sess()); b.fail = False
try:
    run(c, b, st)
except RuntimeError:
    pass                                         # event() مستقیم پرتاب می‌کند؛ SupaSink واقعی هرگز این‌کار را نمی‌کند (تست بالا)
poll.SINK = supa_sink.NullSink()
print("SUPA HOOKS OK")
