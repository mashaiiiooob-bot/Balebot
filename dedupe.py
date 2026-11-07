"""تشخیص پیام تکراری (حتی با املا/ساختار متفاوت): مقایسه‌ی مجموعه‌ی واژه‌های هم‌ریشه، نه متن دقیق."""
import hashlib
import os
import re

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NORM = str.maketrans({"ي": "ی", "ك": "ک", "\u200c": " "})
_DIAC = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_NOISE = re.compile(r"https?://\S+|(?:t\.me|telegram\.me)/\S+|@\w+|#\w+", re.I)
STOP = set("""و در به از که این را با برای است شد شده می هم کرد یک تا بر آن او ما ها های بود باشد خود اما یا نیز هر
همه اند کنند کند شود دارد دارند گفت گفته افزود اعلام داد داده کرده کنم کنیم کنید پس هیچ بین روی طی نیست
the a an of to in and is are was were for on at by with from that this it as""".split())
SUFFIXES = ("ها", "های", "ان", "ات", "ین", "ای", "ی")
MAX_TOKENS = 40


def _stem(word):
    if len(word) > 4:
        for suffix in SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                return word[: -len(suffix)]
    return word


def tokens(text):
    text = _NOISE.sub(" ", text or "").lower().translate(_DIGITS).translate(_NORM)
    text = _DIAC.sub("", text)
    out, seen = [], set()
    for word in re.findall(r"\w+", text):
        if word in STOP or (len(word) < 2 and not word.isdigit()):
            continue
        word = word if word.isdigit() else _stem(word)
        if word not in seen:
            seen.add(word)
            out.append(word)
    return out[:MAX_TOKENS]


def signature(text):
    salt = (os.environ.get("TG_API_HASH", "") + "|dedupe").encode()
    words, nums = [], []
    for tok in tokens(text):
        h = hashlib.blake2b(salt + tok.encode(), digest_size=3).hexdigest()
        words.append(h)
        if tok.isdigit():
            nums.append(h)
    return {"w": words, "n": nums}


def is_duplicate(sig, entries):
    a, an = set(sig["w"]), set(sig["n"])
    if not a:
        return False
    for e in entries:
        b, bn = set(e["w"]), set(e["n"])
        if an and bn and not (an <= bn or bn <= an):
            continue  # عددهای متفاوت (مثلاً قیمت دیگر) = خبر دیگر
        inter = len(a & b)
        small = min(len(a), len(b))
        if small < 5:
            if a == b:
                return True
            continue
        if inter / small >= 0.7 and inter / len(a | b) >= 0.4:
            return True
    return False
