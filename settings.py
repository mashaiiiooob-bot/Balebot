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
    "חדשות", "דיווח", "הודיע", "בחירות", "ממשלה",
]

AD_KEYWORDS = [
    "تبلیغات", "تبلیغ", "اسپانسر", "برای خرید", "کد تخفیف",
    "ثبت سفارش", "همکاری تبلیغاتی", "ad:", "sponsored", "promo code",
    "discount code", "buy now", "برای تبلیغ",
]

def get_sources():
    """SOURCES: هر منبع در یک خط (یا جداشده با ویرگول). لینک، یوزرنیم یا شناسه عددی."""
    raw = os.environ.get("SOURCES", "")
    items = [x.strip() for x in re.split(r"\\n|[\s,;]+", raw) if x.strip()]
    return items
