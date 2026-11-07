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


def _an(text):
    v = normalize((text or "").translate(_DIG)).replace("\u200c", " ")
    return re.sub(r"\s+", " ", v)


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
    value = " " + _an(text) + " "
    for w in words:
        nw = _an(w)
        if nw and re.search(r"(?<!\w)" + re.escape(nw) + r"(?!\w)", value):
            return True
    return False
