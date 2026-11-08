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
BL = ["کانال نمونه | شعار نمونه برای آزمایش", "RadioxlTV", "outletone", "outlettwo", "outletthree"]
body = "🔴 فوری: ارتش اعلام کرد رزمایش جدید آغاز شد\nجزئیات بیشتر در ادامه منتشر می‌شود"

# ۱) پایین‌نویس متنی (بدون @ و بدون لینک)
msg = body + "\n\nکانال نمونه | شعار نمونه برای آزمایش\nRadioxlTV\noutletone\n\noutlettwo\noutletthree"
out = clean_message(msg, None, [], BL)
for w in ("کانال نمونه", "شعار نمونه", "radiox", "outletone", "outlettwo", "outletthree"):
    assert w not in out.lower(), (w, out)
assert out == body, repr(out)

# ۲) حتی بدون بلاک‌لیست: خط‌هایی که فقط آیدی لاتین هستند حذف شوند
out2 = clean_message(msg, None, [], [])
for w in ("radiox", "outletone", "outlettwo", "outletthree"):
    assert w not in out2.lower(), (w, out2)

# ۳) نام کانال با شعار، در حالتی که فقط عنوان کانال دیگری (scrub) داده شده
out3 = clean_message(body + "\n🆔 کانال نمونه | شعار نمونه برای آزمایش", None, ["کانال نمونه | شعار نمونه برای آزمایش"], [])
assert "نمونه" not in out3 and "شعار نمونه" not in out3 and out3 == body, repr(out3)

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
assert clean_message("خبر\nمنبع: outlettwo", None, [], []) == "خبر"
assert clean_message("خبر\nمنبع: وزارت دفاع", None, [], []) == "خبر\nمنبع: وزارت دفاع"

# ۶) پیام انگلیسی عادی خراب نشود
en = "Breaking\nIran announced new talks\nUpdate"
assert clean_message(en, None, [], []) == en
# کلمه‌ی عمومی نباید از متن خبر حذف شود (قطعه‌ی تک‌کلمه‌ای عنوان)
t = "اخبار ایران امروز اعلام شد"
assert clean_message(t, None, ["اخبار | ایران"], []) == t, clean_message(t, None, ["اخبار | ایران"], [])
print("USER EXAMPLES OK")

# ---------- برچسب‌های کوتاه مثل «| TCH» ----------
for tail in ("| TCH", "|TCH", "TCH", "• TCH", "کانال نمونه | TCH", "🔴 | TCH"):
    got = clean_message(body + "\n" + tail, None, [], ["TCH", "کانال نمونه"])
    assert got == body, (tail, got)
    if "نمونه" in tail:
        continue  # اسم فارسی ناشناس فقط با بلاک‌لیست قابل حذف است
    got2 = clean_message(body + "\n" + tail, None, [], [])  # حتی بدون بلاک‌لیست
    assert "tch" not in got2.lower() and body in got2 and "|" not in got2.split("\n")[-1], (tail, got2)
assert clean_message("متن خبر مهم | TCH", None, [], ["TCH"]) == "متن خبر مهم"
# آسیب به متن عادی نزند
assert clean_message("نشست OPEC برگزار شد", None, [], []) == "نشست OPEC برگزار شد"
assert clean_message("خبر\nBREAKING", None, [], []) == "خبر\nBREAKING"
assert clean_message("قیمت | ۱۰۰ دلار", None, [], []) == "قیمت | ۱۰۰ دلار"
print("TAG OK")

# ---------- افزودن/حذف منبع بدون دیدن Secret اصلی ----------
import importlib
os.environ["SOURCES"] = "https://t.me/good_one\n@typo_source_name\n-1001234567\nhttps://t.me/+AbCdEf123"
os.environ["SOURCES_EXTRA"] = "https://t.me/good_source_one\nhttps://t.me/Good_One"
os.environ["SOURCES_REMOVE"] = "typo_source_name"
importlib.reload(settings)
got = settings.get_sources()
assert got == ["https://t.me/good_one", "-1001234567", "https://t.me/+AbCdEf123", "https://t.me/good_source_one"], got
for k in ("SOURCES", "SOURCES_EXTRA", "SOURCES_REMOVE"): os.environ.pop(k)
importlib.reload(settings)
print("SOURCES OK")

# ================= فیلتر واژه، تکراری، تبلیغ، پایین‌نویس‌ها =================
from news_filter import has_dropword, is_ad
import dedupe, importlib
DW = ["پهلوی", "بی‌بی", "bbc", "اتاق", "کیر", "رایگان", "عجب", "بدو", "آرایش", "شاه", "نیوشا صارمی", "حضرت اعلی"]
for bad in ["رضا پهلوی در پیامی گفت", "گزارش بی‌بی‌سی از حادثه", "به نقل از بی بی سی فارسی", "BBC reported",
            "کلاس آرایش رایگان", "نیوشا صارمی حاضر شد", "حضرت اعلی فرمودند", "اتاق فکر تشکیل شد", "شاه ایران", "بدو بدو"]:
    assert has_dropword(bad, DW), bad
for ok in ["شاهد عینی گزارش داد", "پادشاهی عربستان", "شاهرود زلزله", "بیبیسیایی", "اتاقک", "عجیب است"]:
    assert not has_dropword(ok, DW), ok

# تبلیغ
for ad in ["برای تبلیغات تماس بگیرید", "کد تخفیف ویژه", "عضو کانال ما شوید https://t.me/+AbCd",
           "خرید ارزان تماس: ۰۹۱۲۳۴۵۶۷۸۹", "فروش ویژه دایرکت بدید", "کانفیگ رایگان فیلترشکن", "پیج اینستاگرام ما را دنبال کنید"]:
    assert is_ad(ad), ad
for notad in ["سپاه از شلیک موشک خبر داد", "خرید ۲۰ فروند جنگنده توسط ارتش", "قیمت دلار امروز ۶۸ هزار تومان شد",
              "فروش نفت ایران افزایش یافت"]:
    assert not is_ad(notad), notad

# تکراری: املا/ساختار متفاوت
os.environ["TG_API_HASH"] = "x"
A = "سپاه پاسداران از شلیک موشک به مواضع مستقر در منطقه خبر داد"
B = "خبر فوری: شلیك موشك به مواضع مستقر در منطقه توسط سپاه پاسداران اعلام شد"
C = "پدافند هوایی تهران فعال شد؛ صدای انفجار شنیده شد"
sa, sb, sc = (dedupe.signature(x) for x in (A, B, C))
assert dedupe.is_duplicate(sb, [{"t": 1, **sa}]) and not dedupe.is_duplicate(sc, [{"t": 1, **sa}])
p1 = dedupe.signature("قیمت دلار امروز ۶۸ هزار تومان شد در بازار آزاد تهران")
p2 = dedupe.signature("قیمت دلار امروز ۷۰ هزار تومان شد در بازار آزاد تهران")
p3 = dedupe.signature("قیمت دلار امروز ٦٨ هزار تومان شد در بازار آزاد تهران")
assert not dedupe.is_duplicate(p2, [p1]) and dedupe.is_duplicate(p3, [p1])  # عدد متفاوت = خبر دیگر؛ ارقام عربی/فارسی یکی‌اند

# classify کامل + ماندگاری state
st = State(tempfile.mkdtemp() + "/s.json")
settings.DROP_WORDS[:] = DW
r, sig = poll.classify(A, A, st); assert r is None
st.add_seen(sig, time.time()); st.save()
assert poll.classify(B, B, st)[0] == "duplicate"
assert poll.classify("رضا پهلوی گفت خبر فوری", "رضا پهلوی گفت خبر فوری", st)[0] == "dropword"
assert poll.classify("خبر فوری تبلیغات", "خبر فوری تبلیغات", st)[0] == "ad"
st2 = State(st.path); assert poll.classify(B, B, st2)[0] == "duplicate"  # بعد از ریستارت هم یادش می‌ماند
assert "سپاه" not in open(st.path, encoding="utf-8").read()  # محتوا در state نیست

# سربرگ good_source_three و «🔴 | اخبار»
head = "@good_source_three\n\nعنوان سربرگ | شعار سربرگ نمونه👀⚡🔥\n\nمدارس شهر فردا تعطیل است\n\n🔴 | اخبار"
got = clean_message(head, None, ["good_source_three"], ["عنوان سربرگ | شعار سربرگ نمونه"])
assert got == "مدارس شهر فردا تعطیل است", repr(got)
assert clean_message("متن\n🔴 | اخبار", None, [], []) == "متن"
assert clean_message("قیمت | ۱۰۰ دلار", None, [], []) == "قیمت | ۱۰۰ دلار"

# حذف منبع با یوزرنیم/عنوان
os.environ["SOURCES"] = "https://t.me/SampleChannel\n@RadioxlTV\n-100123\nhttps://t.me/keep_me"
os.environ["SOURCES_EXTRA"] = "https://t.me/good_source_two\nhttps://t.me/good_source_three"
os.environ["SOURCES_REMOVE"] = "RadioxlTV\nSampleChannel\nرادیو اکس اینترنشنال\nsample"
importlib.reload(settings)
assert settings.get_sources() == ["-100123", "https://t.me/keep_me", "https://t.me/good_source_two", "https://t.me/good_source_three"], settings.get_sources()
for k in ("SOURCES", "SOURCES_EXTRA", "SOURCES_REMOVE"): os.environ.pop(k)
importlib.reload(settings)
print("NEW FILTERS OK")

# ---------- وحید + «اولین چنلی که خبر تعطیلی ...» ----------
os.environ["DROP_WORDS"] = "شاه"; os.environ["DROP_WORDS_EXTRA"] = "=وحید"
os.environ["BLOCKLIST"] = "TCH"; os.environ["BLOCKLIST_EXTRA"] = "جمله‌ی تبلیغی نمونه که بالای پایین‌نویس می‌آید"
importlib.reload(settings)
assert "=وحید" in settings.DROP_WORDS and settings.DROP_WORDS.count("شاه") == 1 and "جمله‌ی تبلیغی نمونه که بالای پایین‌نویس می‌آید" in settings.BLOCKLIST
assert has_dropword("وحید گفت مدارس تعطیل است", settings.DROP_WORDS) and not has_dropword("وحیدی گفت", settings.DROP_WORDS)
msg = "مدارس تهران فردا تعطیل است\n\nجمله‌ی تبلیغی نمونه که بالای پایین‌نویس می‌آید❤️👇"
assert clean_message(msg, None, [], settings.BLOCKLIST) == "مدارس تهران فردا تعطیل است", repr(clean_message(msg, None, [], settings.BLOCKLIST))
for k in ("DROP_WORDS", "DROP_WORDS_EXTRA", "BLOCKLIST", "BLOCKLIST_EXTRA"): os.environ.pop(k)
importlib.reload(settings)
print("EXTRA OK")

# ---------- تیتر کوتاه داخل خبر مفصل ----------
now = time.time()
long_msg = "تعطیلی مدارس شهر تهران فردا شنبه به دلیل آلودگی هوا اعلام شد و کلاس‌ها غیرحضوری برگزار می‌شود"
headline = "تعطیلی مدارس تهران فردا"   # ۳ واژه‌ی اصلی، همه در خبر مفصل هست
sl, sh = dedupe.signature(long_msg), dedupe.signature(headline)
assert dedupe.is_duplicate(sh, [{"t": now, **sl}], now)            # تیتر بعد از خبر مفصل: تکراری
assert not dedupe.is_duplicate(sl, [{"t": now, **sh}], now)        # خبر مفصل بعد از تیتر: اطلاعات بیشتر، ارسال شود
assert not dedupe.is_duplicate(sh, [{"t": now - 30000, **sl}], now)  # بیش از ۶ ساعت قبل: خبر دیگری است
other = dedupe.signature("تعطیلی مدارس شهر مشهد به دلیل بارش برف اعلام شد")
assert not dedupe.is_duplicate(other, [{"t": now, **sl}], now)     # شهر دیگر = خبر دیگر
two = dedupe.signature("تعطیلی مدارس")                               # ۲ واژه: فقط برابر دقیق
assert not dedupe.is_duplicate(two, [{"t": now, **sl}], now)
print("DEDUPE2 OK")

# ---------- حذف منبع با حروف هم‌شکل و نوشتار فارسی متفاوت ----------
os.environ["SOURCES"] = "@RadioxITV\nhttps://t.me/RadioxlTV\nt.me/RADIOX1TV\nhttps://t.me/keep_me"
os.environ["SOURCES_REMOVE"] = "RadioxITV"
importlib.reload(settings)
assert settings.get_sources() == ["https://t.me/keep_me"], settings.get_sources()
assert settings.confuse("رادیو اکس اينترنشنال\u200c") == settings.confuse("رادیو اکس اینترنشنال")
for k in ("SOURCES", "SOURCES_REMOVE"): os.environ.pop(k)
importlib.reload(settings)
print("REMOVE2 OK")

# ---------- نهایی: واژه‌های ممنوعه کاربر + رادیو اکس اینترنشنال ----------
FINAL = ["پهلوی","بی بی","بی‌بی","اتاق","کیر","رایگان","عجب","بدو","آرایش","شاه","نیوشا صارمی","حضرت","اعلی","شاهزاده",
         "وحید","یاشار","رادیو اکس اینترنشنال","radio x international","radioxltv","radioxitv","radiox"]
settings.DROP_WORDS[:] = FINAL
st3 = State(tempfile.mkdtemp() + "/s3.json")
for bad in ["شاهزاده رضا پهلوی", "به گزارش رادیو اکس اینترنشنال حمله شد", "خبر فوری\n@RadioxlTV", "خبر فوری | RadioxITV",
            "Radio X International reported attack", "حضرت علی", "مقام اعلی", "خبر فوری از بی‌بی‌سی", "اتاق فکر"]:
    assert poll.classify(bad, clean_message(bad, None, [], ["RadioxlTV", "RadioxITV"]), st3)[0] == "dropword", bad
assert poll.classify("خبر فوری از سپاه پاسداران", "خبر فوری از سپاه پاسداران", st3)[0] is None
print("FINAL OK")

# ================= لایه‌ی هوش مصنوعی (با نسخه‌ی ساختگی) =================
import asyncio as _aio
class FakeAI:
    def __init__(self, mode, verdict): self.mode, self.verdict, self.calls = mode, verdict, 0
    def allow(self, state): return True
    def judge_duplicate(self, new, olds, state=None): self.calls += 1; return self.verdict
def _reset():
    poll.RECENT.clear()
    for k in poll.STATS: poll.STATS[k] = 0
old_text = "تعطیلی مدارس شهر تهران فردا شنبه به دلیل آلودگی هوا اعلام شد و کلاس‌ها غیرحضوری برگزار می‌شود"
mid_text = "مدارس تهران فردا شنبه به دلیل آلودگی هوا تعطیل شد"   # مرزی: هم‌موضوع ولی نه قطعاً تکراری
def _prime():
    _reset(); s = dedupe.signature(old_text)
    poll.RECENT.append({"w": set(s["w"]), "n": set(s["n"]), "text": old_text, "t": time.time()})
    return dedupe.signature(mid_text)
stx = State(tempfile.mkdtemp() + "/s.json")

# shadow: فقط ثبت می‌کند، تصمیم را عوض نمی‌کند
sig = _prime(); poll.AIC = FakeAI("shadow", False)
assert _aio.run(poll.apply_ai("duplicate", sig, mid_text, stx)) == "duplicate"
assert poll.STATS["sh_dup_ai_new"] == 1 and poll.STATS["ai_rescued"] == 0

# on: قانون می‌گوید تکراری، هوش مصنوعی می‌گوید خبر تازه → نجات
sig = _prime(); poll.AIC = FakeAI("on", False)
assert _aio.run(poll.apply_ai("duplicate", sig, mid_text, stx)) is None and poll.STATS["ai_rescued"] == 1
# on: قانون می‌گوید تازه، هوش مصنوعی می‌گوید تکراری → رد
sig = _prime(); poll.AIC = FakeAI("on", True)
assert _aio.run(poll.apply_ai(None, sig, mid_text, stx)) == "duplicate" and poll.STATS["ai_caught"] == 1
# خطا/نامعلوم → همان تصمیم قانون (fail-open)
sig = _prime(); poll.AIC = FakeAI("on", None)
assert _aio.run(poll.apply_ai("duplicate", sig, mid_text, stx)) == "duplicate" and poll.STATS["ai_errors"] == 1
# قطعاً تکراری یا آشکارا متفاوت: اصلاً صدا زده نمی‌شود
_reset(); s0 = dedupe.signature(old_text)
poll.RECENT.append({"w": set(s0["w"]), "n": set(s0["n"]), "text": old_text, "t": time.time()})
fa = FakeAI("on", False); poll.AIC = fa
assert _aio.run(poll.apply_ai("duplicate", s0, old_text, stx)) == "duplicate" and fa.calls == 0
far = dedupe.signature("قیمت دلار امروز در بازار آزاد افزایش یافت")
assert _aio.run(poll.apply_ai(None, far, "x", stx)) is None and fa.calls == 0
# هوش مصنوعی هرگز قوانین سخت را دور نمی‌زند: واژه‌ی ممنوعه/تبلیغ قبل از آن رد می‌شود
settings.DROP_WORDS[:] = ["رژیم"]
assert poll.classify("رژیم", "رژیم صهیونیستی", stx)[0] == "dropword"
assert _aio.run(poll.apply_ai("dropword", sig, "x", stx)) == "dropword"
# چندسرویسی: failover، vote، بودجه، سازگاری با نبودن کلید
import ai as ai_mod
class FP(ai_mod.Provider):
    name = "fake"
    def __init__(self, name, results, limit=100):
        self.name = name; self.key = "k"; self.limit = limit; self.model = "m"; self.probe = "ok"; self.ok = True
        self._last = 0.0; self._blocked_until = 0.0; self.min_interval = 0; self.results = list(results); self.calls = 0
        self.okc = self.errc = 0; self.lat = __import__("collections").deque(maxlen=300)
    def _run(self, text): return self._generate(text)
    def _generate(self, text):
        self.calls += 1; r = self.results.pop(0)
        return (200, r) if isinstance(r, dict) else (r, None)
DUP = {"same_event": True, "adds_new_info": False}; NEW = {"same_event": True, "adds_new_info": True}
def mk(strategy, *provs):
    for k in ("GEMINI_API_KEY", "GROQ_API_KEY"): os.environ.pop(k, None)
    os.environ["GEMINI_API_KEY"] = "x"; a = ai_mod.AI(); a.providers = list(provs); a.strategy = strategy; a.mode = "on"; return a
s_ = State(tempfile.mkdtemp() + "/b.json")
# failover: اولی خطا (500) می‌دهد → دومی جواب می‌دهد
a, b = FP("a", [500]), FP("b", [DUP]); ai = mk("failover", a, b)
assert ai.judge_duplicate("n", ["o"], s_) is True and a.calls == 1 and b.calls == 1 and ai.stats["a_error"] == 1
# failover: اولی سالم است → دومی صدا زده نمی‌شود
a, b = FP("a", [NEW]), FP("b", [DUP]); ai = mk("failover", a, b)
assert ai.judge_duplicate("n", ["o"], s_) is False and b.calls == 0
# vote: هر دو «تکراری» → تکراری
a, b = FP("a", [DUP]), FP("b", [DUP]); ai = mk("vote", a, b)
assert ai.judge_duplicate("n", ["o"], s_) is True
# vote: اختلاف نظر → تکراری حساب نمی‌شود (پیام ارسال می‌شود)
a, b = FP("a", [DUP]), FP("b", [NEW]); ai = mk("vote", a, b)
assert ai.judge_duplicate("n", ["o"], s_) is False and ai.stats["disagree"] == 1
# همه شکست خوردند → None (تصمیم قانون‌ها)
a, b = FP("a", [429]), FP("b", [500]); ai = mk("failover", a, b)
assert ai.judge_duplicate("n", ["o"], s_) is None
assert a._blocked_until > time.monotonic()  # 429 → ده دقیقه استراحت
# خروجی مخرب/نامعتبر → None
a = FP("a", [{"same_event": "yes", "adds_new_info": 1}]); ai = mk("failover", a)
assert ai.judge_duplicate("n", ["o"], s_) is None
# بودجه‌ی روزانه‌ی هر سرویس جدا
a = FP("budget_p", [DUP, DUP, DUP], limit=2); ai = mk("failover", a)
assert ai.judge_duplicate("n", ["o"], s_) is True and ai.judge_duplicate("n", ["o"], s_) is True
assert ai.judge_duplicate("n", ["o"], s_) is None and not ai.allow(s_)
# بدون هیچ کلیدی: خاموش
for k in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "MISTRAL_API_KEY", "OPENROUTER_API_KEY"): os.environ.pop(k, None)
assert ai_mod.AI().mode == "off"
os.environ["GROQ_API_KEY"] = "y"; assert [p.name for p in ai_mod.AI().providers] == ["groq"]
os.environ.pop("GROQ_API_KEY"); os.environ.pop("AI_MODE", None); poll.AIC = None; _reset()
print("AI LAYER OK")

# ================= تطبیق محکم + دروازه‌ی نهایی =================
W2 = ["جمهوری", "پهلوی", "شاه", "دی", "رژیم", "=وحید"]
for hit in ["جمهوری اسلامی", "جمهورى اسلامى", "جَمهوری", "ﺟﻤﻬﻮﺭﯼ", "جمهوری‌ها", "جمهوریت", "#جمهوری", "«جمهوری»",
            "جمهوري", "ج‌مهوری", "پهلوی‌ها", "شاهی", "شاهان", "۱۸ دی", "رژیم‌های", "وحید گفت"]:
    assert has_dropword(hit, W2), hit
for miss in ["شاهد عینی", "دیروز", "مدیر", "پادشاه", "شاهرود", "وحیدی گفت"]:   # «=وحید» دقیق است
    assert not has_dropword(miss, W2), miss
# دروازه‌ی نهایی: shadow ثبت می‌کند، on رد می‌کند، خطا = ارسال، و هرگز چیزی را آزاد نمی‌کند
class GateAI:
    def __init__(self, gate, res): self.mode, self.gate, self.res, self.stats = "on", gate, res, {}
    def allow(self, s): return True
    def judge_gate(self, text, terms, s): return self.res
_reset()
poll.AIC = GateAI("shadow", (True, False)); assert _aio.run(poll.apply_gate("x", stx)) is None and poll.STATS["sh_gate_banned"] == 1
poll.AIC = GateAI("on", (True, False));  assert _aio.run(poll.apply_gate("x", stx)) == "ai_banned"
poll.AIC = GateAI("on", (False, True));  assert _aio.run(poll.apply_gate("x", stx)) == "ai_ad"
poll.AIC = GateAI("on", (False, False)); assert _aio.run(poll.apply_gate("x", stx)) is None
poll.AIC = GateAI("on", None);           assert _aio.run(poll.apply_gate("x", stx)) is None and poll.STATS["gate_errors"] == 1
poll.AIC = GateAI("off", (True, True));  assert _aio.run(poll.apply_gate("x", stx)) is None
# مدیر چندسرویسی: دروازه با failover
ga, gb = FP("ga", [500]), FP("gb", [{"banned": True, "ad": False}]); aig = mk("failover", ga, gb)
assert aig.judge_gate("n", ["t"], s_) == (True, False)
ga, gb = FP("ga2", [{"banned": "yes"}]), FP("gb2", [{"banned": False, "ad": False}]); aig = mk("failover", ga, gb)
assert aig.judge_gate("n", ["t"], s_) == (False, False)  # پاسخ نامعتبرِ اولی نادیده، دومی جواب می‌دهد
for k in ("GEMINI_API_KEY",): os.environ.pop(k, None)
os.environ["DEEPSEEK_API_KEY"] = "z"; assert [p.name for p in ai_mod.AI().providers] == ["deepseek"]; os.environ.pop("DEEPSEEK_API_KEY")
poll.AIC = None; _reset()
print("GATE OK")

# Cloudflare: فقط با توکن فعال می‌شود و بعد از Gemini/Groq می‌آید
for k in list(os.environ):
    if k.endswith("_API_KEY") or k == "CLOUDFLARE_API_TOKEN": os.environ.pop(k)
os.environ["CLOUDFLARE_API_TOKEN"] = "t"; os.environ["GROQ_API_KEY"] = "g"; os.environ["GEMINI_API_KEY"] = "m"
assert [p.name for p in ai_mod.AI().providers] == ["gemini", "groq", "cloudflare"]
for k in ("CLOUDFLARE_API_TOKEN", "GROQ_API_KEY", "GEMINI_API_KEY"): os.environ.pop(k)
print("CLOUDFLARE ORDER OK")

# ================= مقایسه‌ی عوامل =================
poll.BENCH.clear(); poll.BENCH_PAIR.clear()
poll.bench_update({"gemini": (True, False), "groq": (False, False), "cf": None}, positive=True)    # حتماً ممنوع
poll.bench_update({"gemini": (False, False), "groq": (True, False)}, positive=False)               # سالم
poll.bench_update({"gemini": (False, True), "groq": (False, True)}, positive=True)
rep = poll.compare_report()
assert rep["vs_rules"]["gemini"]["tp"] == 2 and rep["vs_rules"]["gemini"]["tn"] == 1 and rep["vs_rules"]["gemini"]["recall"] == 1.0
assert rep["vs_rules"]["groq"]["fn"] == 1 and rep["vs_rules"]["groq"]["fp"] == 1 and rep["vs_rules"]["groq"]["tp"] == 1
assert rep["vs_rules"]["cf"]["err"] == 1 and rep["agreement"]["gemini|groq"] == {"agree": 1, "of": 3, "rate": 0.33}
# عملکرد سرویس: تأخیر و خطا
class PP(ai_mod.Provider):
    name = "pp"
    def __init__(self): super().__init__("k"); self.ok = True; self.res = [(200, DUP), (500, None), (200, DUP)]
    def _generate(self, text): return self.res.pop(0)
pf = PP()
for _ in range(3): pf._run("x")
aiq = mk("failover", pf); perf = aiq.perf()["pp"]
assert perf["calls"] == 3 and perf["err"] == 1 and perf["err_rate"] == 0.333 and perf["p50_ms"] is not None
poll.BENCH.clear(); poll.BENCH_PAIR.clear(); poll.AIC = None
print("COMPARE OK")
