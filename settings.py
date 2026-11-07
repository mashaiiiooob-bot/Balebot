"""تنظیمات. مقادیر حساس فقط از متغیرهای محیطی (GitHub Secrets) خوانده می‌شوند."""
import os
import re

def _get(name, default):
    value = os.environ.get(name, "")
    return value.strip() if value and value.strip() else default

BALE_DESTINATION = _get("BALE_DESTINATION", "@maklpo")
SIGNATURE = _get("SIGNATURE", "✍️  F16\n\n⚔️ @Bale_Dr")
STATE_FILE = _get("STATE_FILE", "state/state.json")

# مدت زنده ماندن هر اجرا و فاصله‌ی بررسی‌ها (ثانیه)
LOOP_SECONDS = int(_get("LOOP_SECONDS", "19800"))
POLL_INTERVAL = int(_get("POLL_INTERVAL", "20"))
# تعداد پیام اخیری که فقط در اولین اجرا (بدون checkpoint) ارسال می‌شود؛ 0 یعنی از اکنون به بعد
BACKFILL_LIMIT = int(_get("BACKFILL_LIMIT", "0"))
# اختیاری: شناسه‌ی چت خصوصی خودت در بله برای هشدار خطا، و آدرس healthchecks.io برای اطلاع از توقف
BALE_ADMIN_CHAT = _get("BALE_ADMIN_CHAT", "")
HEALTHCHECK_URL = _get("HEALTHCHECK_URL", "")
# عبارت‌هایی که باید از متن حذف شوند (هر مورد در یک خط): نام کانال‌ها/رسانه‌ها، شعارها، آیدی‌ها
BLOCKLIST = [x.strip() for x in (os.environ.get("BLOCKLIST", "") + "\n" + os.environ.get("BLOCKLIST_EXTRA", "")).splitlines() if x.strip()]
# پیامی که این واژه‌ها را (به‌صورت واژه‌ی کامل) داشته باشد ارسال نمی‌شود؛ هر مورد در یک خط
# واژه‌های ممنوعه‌ی ثابت (به فهرست Secretهای DROP_WORDS/DROP_WORDS_EXTRA اضافه می‌شوند؛ تکراری‌ها حذف می‌شوند)
_DEFAULT_DROP_WORDS = [
    "پهلوی", "بی بی", "بی‌بی", "اتاق", "کیر", "رایگان", "عجب", "بدو", "آرایش", "شاه",
    "نیوشا صارمی", "حضرت", "اعلی", "شاهزاده",
]
DROP_WORDS = list(dict.fromkeys(_DEFAULT_DROP_WORDS + [x.strip() for x in (os.environ.get("DROP_WORDS", "") + "\n" + os.environ.get("DROP_WORDS_EXTRA", "")).splitlines() if x.strip()]))
# حداقل سطح فحش که حذف شود: 1 = همه (ملایم تا رکیک)، 2 = متوسط و رکیک، 3 = فقط رکیک
PROFANITY_MIN_LEVEL = int(_get("PROFANITY_MIN_LEVEL", "1"))
MAX_FILE_MB = 45
MAX_FAILS = 5

NEWS_KEYWORDS = [
    "خبر", "گزارش", "اعلام کرد", "اعلام شد", "فوری", "آخرین", "تحولات",
    "حادثه", "زلزله", "سیل", "آتش", "انفجار", "حمله", "درگیری", "ارتش",
    "دولت", "پارلمان", "انتخابات", "رئیس جمهور", "وزیر", "سازمان ملل",
    "خبرگزاری", "breaking", "news", "report", "announced", "update",
    "latest", "election", "government", "president", "minister",
    "parliament", "military", "attack", "conflict", "earthquake",
    "flood", "fire", "explosion", "israel", "iran", "ukraine",
    # گسترش فیلتر: اقتصاد، سیاست، امنیت، حوادث، هواشناسی، بین‌الملل
    "دلار", "قیمت", "سکه", "طلا", "نفت", "تورم", "بورس", "بانک مرکزی", "مذاکره", "مذاکرات",
    "توافق", "تحریم", "سپاه", "موشک", "پهپاد", "پدافند", "جنگ", "آتش‌بس", "آتش بس",
    "بازداشت", "دستگیر", "کشته", "زخمی", "شهید", "اعدام", "دادگاه", "قوه قضائیه", "مجلس",
    "رهبر", "فرمانده", "سخنگو", "بیانیه", "سفیر", "هشدار", "هواشناسی", "بارش", "طوفان",
    "تجمع", "اعتراض", "تظاهرات", "اعتصاب", "مرز", "نیروهای", "تصاویر", "ویدیو", "ویدئو",
    "تهران", "واشنگتن", "اسرائیل", "آمریکا", "روسیه", "چین", "لبنان", "غزه", "سوریه", "عراق",
    "strike", "killed", "sanctions", "talks", "protest", "missile", "drone", "ceasefire",
    "dollar", "police", "court", "washington", "tehran", "russia", "china", "gaza", "lebanon",
    "חדשות", "דיווח", "הודיע", "בחירות", "ממשלה",
]

AD_KEYWORDS = [
    "تبلیغات", "تبلیغ", "اسپانسر", "برای خرید", "کد تخفیف",
    "ثبت سفارش", "همکاری تبلیغاتی", "#ad", "sponsored", "promo code",
    "discount code", "buy now", "برای تبلیغ",
]

def _parse(raw):
    return [x.strip() for x in re.split(r"\\n|[\s,;]+", raw or "") if x.strip()]


def _norm_source(item):
    """لینک/@یوزرنیم/یوزرنیم را به یک شکل یکسان (حروف کوچک) تبدیل می‌کند تا تکراری‌ها و حذفی‌ها تشخیص داده شوند."""
    m = re.search(r"(?:t\.me|telegram\.me)/(?!\+|joinchat)([A-Za-z0-9_]{4,})", item, re.I)
    if m:
        return m.group(1).lower()
    return item.lstrip("@").lower()


def confuse(text):
    """نرمال‌سازی برای تطبیق حذف: حروف هم‌شکل (I بزرگ، l کوچک، 1، |) یکی حساب می‌شوند؛ ی/ک عربی و نیم‌فاصله یکسان می‌شوند."""
    t = (text or "").lower().replace("ي", "ی").replace("ك", "ک").replace("\u200c", " ")
    t = t.translate({ord("i"): "l", ord("1"): "l", ord("|"): "l", ord("0"): "o"})
    return re.sub(r"\s+", " ", t).strip()


def get_remove_terms():
    """عبارت‌های SOURCES_REMOVE (نرمال‌شده)؛ برای حذف منبع بر اساس یوزرنیم یا عنوان بعد از resolve هم استفاده می‌شود."""
    return [x.strip().lstrip("@").lower() for x in os.environ.get("SOURCES_REMOVE", "").replace(",", "\n").splitlines() if x.strip()]


def get_add_terms():
    """منبع‌هایی که صریحاً با SOURCES_ADD اضافه شده‌اند (نرمال‌شده)؛ SOURCES_REMOVE روی آن‌ها اثر ندارد."""
    return {confuse(_norm_source(x)) for x in _parse(os.environ.get("SOURCES_ADD", ""))}


def get_sources():
    """SOURCES + SOURCES_EXTRA، منهای SOURCES_REMOVE، به‌علاوه‌ی SOURCES_ADD (همیشه اضافه می‌شود)؛ بدون تکرار."""
    removed = {confuse(_norm_source(x)) for x in get_remove_terms()}
    seen, out = set(), []
    for item in _parse(os.environ.get("SOURCES", "")) + _parse(os.environ.get("SOURCES_EXTRA", "")):
        key = _norm_source(item)
        if confuse(key) in removed or key in seen:
            continue
        seen.add(key)
        out.append(item)
    for item in _parse(os.environ.get("SOURCES_ADD", "")):
        key = _norm_source(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
