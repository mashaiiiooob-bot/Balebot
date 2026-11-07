import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import poll, settings
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
        if self.fail: raise RuntimeError("boom")
        self.sent.append(text)

def run(c, b, s): asyncio.run(poll.process_source(c, b, s, Ent(), tempfile.mkdtemp()))

d = tempfile.mkdtemp(); path = d+"/state.json"
msgs=[Msg(1,"خبر قدیمی"), Msg(2,"breaking news A")]
b=FakeBale(); s=State(path); c=FakeClient(msgs)
run(c,b,s); assert b.sent==[] and s.last_id(poll.key_for(Ent()))==2   # اولین اجرا: از اکنون
msgs += [Msg(3,"سلام"), Msg(4,"فوری: حادثه"), Msg(5,"تبلیغات خبر")]
run(c,b,s); assert len(b.sent)==1 and "فوری" in b.sent[0]; assert s.last_id(poll.key_for(Ent()))==5
msgs.append(Msg(6,"news update")); b.fail=True
run(c,b,s); assert s.last_id(poll.key_for(Ent()))==5            # شکست: checkpoint جلو نرفت
b.fail=False; s2=State(path); run(c,b,s2)
assert len(b.sent)==2 and s2.last_id(poll.key_for(Ent()))==6    # تلاش مجدد موفق
for _ in range(5): pass
b.fail=True; msgs.append(Msg(7,"news poison"))
for _ in range(settings.MAX_FAILS): run(c,b,s2)
assert s2.last_id(poll.key_for(Ent()))==7                       # پیام معیوب پس از ۵ بار رد می‌شود
print("ALL OK")
