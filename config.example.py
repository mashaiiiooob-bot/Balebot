# این فایل را به config.py کپی کن و مقادیر واقعی را فقط روی گوشی وارد کن.
API_ID = 123456
API_HASH = "PUT_YOUR_TELEGRAM_API_HASH_HERE"
BALE_BOT_TOKEN = "PUT_NEW_BALE_BOT_TOKEN_HERE"

# کانال مقصد آزمایشی
BALE_DESTINATION = "@maklpo"

# لینک‌ها یا شناسه عددی کانال‌ها. لینک دعوت خصوصی ممکن است نیاز به شناسه عددی داشته باشد.
SOURCES = [
    "https://t.me/+RRwXo-Z5RgQ2YzM0",
    "https://t.me/khabarfuri",
    "https://t.me/Nlinews",
    "https://t.me/GenVNews",
    "https://t.me/iran_charshanbsoriii",
    "https://t.me/withyashar",
]

SIGNATURE = "⚔️ @Bale_Dr"
DATABASE_PATH = "balebot.sqlite3"
DOWNLOAD_DIR = "downloads"

# فیلتر ساده و قابل تنظیم؛ برای کاهش خطا، واژه‌های تبلیغاتی را با احتیاط تنظیم کن.
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

# در شروع، چند پیام اخیر هر کانال بررسی می‌شود تا پیام‌های زمان قطعی جا نمانند.
BACKFILL_LIMIT = 30
POLL_RETRY_SECONDS = 10
