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

# ---------- تست‌های حریم خصوصی و پایداری ----------
import json, time
from cleaner import clean_text

names = ["News_Channel Fa", "newschannelfa"]
dirty = ("خبر فوری 🔴\nجزئیات در https://t.me/newschannelfa/123\n"
         "tg://resolve?domain=newschannelfa\nمنبع: @newschannelfa\n"
         "News Channel Fa\nNews_Channel Fa 🌐\nمتن اصلی خبر")
out = clean_text(dirty, names).lower()
for leak in ("t.me", "tg://", "newschannelfa", "news_channel", "news channel", "@"):
    assert leak not in out, f"leak: {leak!r} in {out!r}"
assert "متن اصلی خبر" in out and "خبر فوری" in out

# نام فایل اصلی و ساختار مسیر نباید به بله برسد
td = tempfile.mkdtemp(); orig = os.path.join(td, "@newschannelfa_video.MP4")
open(orig, "wb").write(b"x"); neutral = poll.sanitize_media(orig, "document")
assert os.path.basename(neutral) == "media.mp4" and not os.path.exists(orig)
bad = os.path.join(td, "f.@weird-ext!"); open(bad, "wb").write(b"x")
assert os.path.basename(poll.sanitize_media(bad, "document")) == "media"

# کلید checkpoint باید HMAC با راز باشد: بدون راز نتوان شناسه‌ی کانال را از state حدس زد
import hashlib
os.environ["TG_API_HASH"] = "secret-A"; kA = poll.key_for(Ent())
os.environ["TG_API_HASH"] = "secret-B"; kB = poll.key_for(Ent())
assert kA != kB and kA != hashlib.sha256(b"42").hexdigest()[:16]

# state قدیمی (نسخه‌ی ۱ با peers) دور ریخته شود و peers هرگز نوشته نشود
old = tempfile.mkdtemp() + "/state.json"
json.dump({"checkpoints": {"abc": 5}, "fails": {}, "peers": {"x": [1, 2]}}, open(old, "w"))
st = State(old); assert st.data["checkpoints"] == {} and st.dirty; st.save()
saved = json.load(open(old)); assert "peers" not in saved and saved["version"] == 2

# هشدار حداکثر یک بار در ۲۴ ساعت
st2 = State(tempfile.mkdtemp() + "/s.json"); now = time.time()
assert st2.alert_due("session", now); st2.mark_alert("session", now)
assert not st2.alert_due("session", now + 3600) and st2.alert_due("session", now + 90000)

# status.json نباید متن خطا (که ممکن است یوزرنیم منبع باشد) داشته باشد
src = open(os.path.join(os.path.dirname(__file__), "..", "poll.py"), encoding="utf-8").read()
assert "safe_err" not in src and "set_peer" not in src and "message_id=" not in src
print("PRIVACY OK")

# ---------- نمونه‌های واقعی کاربر: پایین‌نویس کانال‌ها ----------
from cleaner import clean_message
BL = ["پرشین ترند | متفاوت‌ترین کانال نظامی", "IranintlTV", "radiofarda", "bbcpersian", "Indypersian"]
body = "🔴 فوری: ارتش اعلام کرد رزمایش جدید آغاز شد\nجزئیات بیشتر در ادامه منتشر می‌شود"

# ۱) پایین‌نویس متنی (بدون @ و بدون لینک)
msg = body + "\n\nپرشین ترند | متفاوت‌ترین کانال نظامی\nIranintlTV\nradiofarda\n\nbbcpersian\nIndypersian"
out = clean_message(msg, None, [], BL)
for w in ("پرشین ترند", "متفاوت", "iranintl", "radiofarda", "bbcpersian", "indypersian"):
    assert w not in out.lower(), (w, out)
assert out == body, repr(out)

# ۲) حتی بدون بلاک‌لیست: خط‌هایی که فقط آیدی لاتین هستند حذف شوند
out2 = clean_message(msg, None, [], [])
for w in ("iranintl", "radiofarda", "bbcpersian", "indypersian"):
    assert w not in out2.lower(), (w, out2)

# ۳) نام کانال با شعار، در حالتی که فقط عنوان کانال دیگری (scrub) داده شده
out3 = clean_message(body + "\n🆔 پرشین ترند | متفاوت‌ترین کانال نظامی", None, ["پرشین ترند | متفاوت‌ترین کانال نظامی"], [])
assert "پرشین" not in out3 and "متفاوت" not in out3 and out3 == body, repr(out3)

# ۴) لینک‌های پنهان (entity): خط کاملاً لینکِ تلگرامی حذف شود، متن اصلی بماند
class TU:  # MessageEntityTextUrl
    def __init__(s, o, l, u): s.offset, s.length, s.url = o, l, u
TU.__name__ = "MessageEntityTextUrl"
text = "خبر مهم امروز\nکانال ما را ببینید"
ents = [TU(text.index("کانال"), len("کانال ما را ببینید"), "https://t.me/somechannel")]
out4 = clean_message(text, ents, [], [])
assert out4 == "خبر مهم امروز", repr(out4)
# لینک معمولی داخل جمله دست نخورد
text5 = "گزارش کامل در سایت منتشر شد و همه‌چیز عادی است"
e5 = [TU(0, 5, "https://example.com/a")]
assert clean_message(text5, e5, [], []) == text5
# offset با ایموجی (UTF-16) درست محاسبه شود
text6 = "😀🔴 خبر\nمنبع ما"
e6 = [TU(len("😀🔴 خبر\n".encode("utf-16-le")) // 2, len("منبع ما"), "https://telegram.me/x")]
assert clean_message(text6, e6, [], []) == "😀🔴 خبر", repr(clean_message(text6, e6, [], []))

# ۵) خط «منبع: آیدی» حذف شود؛ منبع خبری فارسی عادی باقی بماند
assert clean_message("خبر\nمنبع: bbcpersian", None, [], []) == "خبر"
assert clean_message("خبر\nمنبع: وزارت دفاع", None, [], []) == "خبر\nمنبع: وزارت دفاع"

# ۶) پیام انگلیسی عادی خراب نشود
en = "Breaking\nIran announced new talks\nUpdate"
assert clean_message(en, None, [], []) == en
# کلمه‌ی عمومی نباید از متن خبر حذف شود (قطعه‌ی تک‌کلمه‌ای عنوان)
t = "اخبار ایران امروز اعلام شد"
assert clean_message(t, None, ["اخبار | ایران"], []) == t, clean_message(t, None, ["اخبار | ایران"], [])
print("USER EXAMPLES OK")

# ---------- برچسب‌های کوتاه مثل «| TCH» ----------
for tail in ("| TCH", "|TCH", "TCH", "• TCH", "پرشین ترند | TCH", "🔴 | TCH"):
    got = clean_message(body + "\n" + tail, None, [], ["TCH", "پرشین ترند"])
    assert got == body, (tail, got)
    if "پرشین" in tail:
        continue  # اسم فارسی ناشناس فقط با بلاک‌لیست قابل حذف است
    got2 = clean_message(body + "\n" + tail, None, [], [])  # حتی بدون بلاک‌لیست
    assert "tch" not in got2.lower() and body in got2 and "|" not in got2.split("\n")[-1], (tail, got2)
assert clean_message("متن خبر مهم | TCH", None, [], ["TCH"]) == "متن خبر مهم"
# آسیب به متن عادی نزند
assert clean_message("نشست OPEC برگزار شد", None, [], []) == "نشست OPEC برگزار شد"
assert clean_message("خبر\nBREAKING", None, [], []) == "خبر\nBREAKING"
assert clean_message("قیمت | ۱۰۰ دلار", None, [], []) == "قیمت | ۱۰۰ دلار"
print("TAG OK")
