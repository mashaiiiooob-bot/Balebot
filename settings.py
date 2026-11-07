"""تنظیمات. مقادیر حساس فقط از متغیرهای محیطی (GitHub Secrets) خوانده می‌شوند."""
import os
import re

def _get(name, default):
    value = os.environ.get(name, "")
    return value.strip() if value and value.strip() else default

BALE_DESTINATION = _get("BALE_DESTINATION", "@bale_dr")
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
BLOCKLIST = [x.strip() for x in os.environ.get("BLOCKLIST", "").splitlines() if x.strip()]
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


def get_sources():
    """SOURCES + SOURCES_EXTRA، منهای SOURCES_REMOVE (بدون تکرار). لینک، یوزرنیم یا شناسه عددی."""
    removed = {_norm_source(x) for x in _parse(os.environ.get("SOURCES_REMOVE", ""))}
    seen, out = set(), []
    for item in _parse(os.environ.get("SOURCES", "")) + _parse(os.environ.get("SOURCES_EXTRA", "")):
        key = _norm_source(item)
        if key in removed or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
