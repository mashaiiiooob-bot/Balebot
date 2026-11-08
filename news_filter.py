import re
from settings import NEWS_KEYWORDS, AD_KEYWORDS

def normalize(text: str) -> str:
    text = (text or "").casefold()
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def is_news(text: str) -> bool:
    value = normalize(text)
    if not value:
        return False
    if any(normalize(word) in value for word in AD_KEYWORDS if word.strip()):
        return False
    return any(normalize(word) in value for word in NEWS_KEYWORDS if word.strip())


# ---------------- تبلیغ (سخت‌گیرانه) ----------------
_DIG = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
STRONG_AD = [
    "تبلیغ", "اسپانسر", "حمایت مالی", "کد تخفیف", "کد معرف", "ثبت سفارش", "سفارش دهید", "سفارش بدهید",
    "برای خرید", "همین حالا بخرید", "buy now", "sponsored", "promo code", "discount code", "#ad",
    "عضو شوید", "عضو شو", "عضو کانال", "عضویت در", "جوین شوید", "جوین شو", "لینک دعوت", "کانال ما",
    "ما را دنبال", "دنبال کنید", "فالو کنید", "فیلترشکن", "vpn", "کانفیگ", "کسب درآمد", "درآمد دلاری",
    "سیگنال رایگان", "فروش ویژه", "تخفیف ویژه", "حراج", "پیج اینستاگرام", "دایرکت", "تماس بگیرید",
    "شماره تماس", "واتساپ", "واتس اپ", "whatsapp", "پشتیبانی", "ادمین", "کپی رایت",
]
WEAK_AD = ["خرید", "فروش", "تخفیف", "ارزان", "قیمت مناسب", "سفارش", "مشاوره", "آموزش", "دوره"]
_PHONE = re.compile(r"(?<!\d)(?:\+?98|0)?9\d{9}(?!\d)|(?<!\d)0\d{2,3}[\s-]?\d{7,8}(?!\d)")
_INVITE = re.compile(r"t\.me/\+|t\.me/joinchat|telegram\.me/joinchat", re.I)
_HANDLE = re.compile(r"(?<![\w.])@[A-Za-z]\w{3,}")


_ZERO = dict.fromkeys(map(ord, "\u200b\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060\ufeff\u00ad"), None)
_LET = str.maketrans({"ي": "ی", "ى": "ی", "ی": "ی", "ې": "ی", "ۍ": "ی", "ئ": "ی", "ك": "ک", "ڪ": "ک", "ة": "ه", "ۀ": "ه",
                      "ہ": "ه", "ھ": "ه", "ۃ": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ؤ": "و", "\u200c": " "})
_MARKS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED\u0640]")
SUFFIX = r"(?:ها|های|یی|ی|ان|ات|ای|ت|ش|شان|مان|تان|ه)?"


def canon(text, join=False):
    """نرمال‌سازی محکم: اشکال نمایشی/عربی حروف، حرکت‌ها، کشیده، حروف نامرئی، نیم‌فاصله، ارقام.
    join=True: نیم‌فاصله حذف می‌شود (برای گرفتن «ج‌مهوری» که عمداً وسط کلمه نیم‌فاصله دارد)."""
    import unicodedata
    t = unicodedata.normalize("NFKC", text or "").translate(_ZERO)
    if join:
        t = t.replace("\u200c", "")
    t = _MARKS.sub("", t).casefold().translate(_LET).translate(_DIG)
    return re.sub(r"\s+", " ", t).strip()


_an = canon


def is_ad(text):
    raw = (text or "").translate(_DIG)
    if _INVITE.search(raw):
        return True
    v = _an(text)
    if any(_an(w) in v for w in STRONG_AD):
        return True
    cta = bool(_PHONE.search(raw)) or bool(_HANDLE.search(raw))
    return cta and any(_an(w) in v for w in WEAK_AD)


def has_dropword(text, words):
    """واژه‌ی کامل، با تحمل پسوندهای رایج فارسی (ها، ی، ان، ...). «=واژه» یعنی فقط دقیقاً همان واژه."""
    values = (" " + canon(text) + " ", " " + canon(text, join=True) + " ")
    for w in words:
        exact = w.startswith("=")
        nw = canon(w.lstrip("="))
        if not nw:
            continue
        tail = "" if exact or len(nw) <= 2 else SUFFIX
        pat = r"(?<!\w)" + re.escape(nw) + tail + r"(?!\w)"
        if any(re.search(pat, v) for v in values):
            return True
    return False
