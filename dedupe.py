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
NUMWORDS = {"دو": "2", "سه": "3", "چهار": "4", "پنج": "5", "شش": "6", "هفت": "7", "هشت": "8", "بیست": "20", "سی": "30",
            "چهل": "40", "پنجاه": "50", "شصت": "60", "هفتاد": "70", "هشتاد": "80", "نود": "90", "صد": "100", "هزار": "1000"}

# مکان‌ها «لنگر» رویداد هستند: دو پیام با مکان‌های متفاوت (اصفهان در برابر تبریز) دو خبر جدا هستند.
PLACES_RAW = """تهران اصفهان مشهد شیراز تبریز اهواز کرج قم کرمان ارومیه رشت زاهدان همدان یزد اردبیل بندرعباس اراک زنجان سنندج قزوین
خرم‌آباد گرگان ساری بجنورد بوشهر بیرجند ایلام شهرکرد سمنان یاسوج کاشان نجف‌آباد ورامین ملارد اسلامشهر ری شهریار دزفول آبادان خرمشهر
خراسان آذربایجان فارس خوزستان گیلان مازندران گلستان هرمزگان کردستان کرمانشاه لرستان مرکزی البرز چهارمحال بختیاری کهگیلویه بویراحمد
سیستان بلوچستان قشم کیش جاسک چابهار میناب
آمریکا اسرائیل روسیه چین عراق سوریه لبنان یمن ترکیه افغانستان پاکستان عربستان امارات قطر کویت بحرین عمان مصر آلمان فرانسه انگلیس
بریتانیا ژاپن هند اوکراین غزه رفح بیروت دمشق بغداد کابل ریاض دوحه مسکو پکن واشنگتن لندن پاریس برلین آنکارا باکو ارمنستان آذربایجان
تل‌آویو حیفا قدس کرانه اربیل بصره موصل حلب ادلب دبی ابوظبی کراچی لاهور اسلام‌آباد""".split()


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
        if word in NUMWORDS:
            word = NUMWORDS[word]              # «دو نفر» و «۲ نفر» یکی؛ «دو» در برابر «سه» = عدد متفاوت
        word = word if word.isdigit() else _stem(word)
        if word not in seen:
            seen.add(word)
            out.append(word)
    return out[:MAX_TOKENS]


PLACE_STEMS = None


def _place_stems():
    global PLACE_STEMS
    if PLACE_STEMS is None:
        PLACE_STEMS = {w for p in PLACES_RAW for w in tokens(p)}
    return PLACE_STEMS


def signature(text):
    salt = (os.environ.get("TG_API_HASH", "") + "|dedupe").encode()
    places = _place_stems()
    words, nums, plc = [], [], []
    for tok in tokens(text):
        h = hashlib.blake2b(salt + tok.encode(), digest_size=3).hexdigest()
        words.append(h)
        if tok.isdigit():
            nums.append(h)
        else:
            canon = tok if tok in places else (_stem(tok) if _stem(tok) in places else None)   # «اصفهانی» → همان «اصفهان»
            if canon:
                plc.append(hashlib.blake2b(salt + b"P:" + canon.encode(), digest_size=3).hexdigest())
    return {"w": words, "n": nums, "p": plc}


# ---------------------------------------------------------------- نسخه‌ی ۱ (برای مقایسه)
def is_duplicate_v1(sig, entries, now=None):
    import time as _t
    now = _t.time() if now is None else now
    a, an = set(sig["w"]), set(sig["n"])
    if not a:
        return False
    for e in entries:
        b, bn = set(e["w"]), set(e["n"])
        if an and bn and not (an <= bn or bn <= an):
            continue
        inter = len(a & b)
        small = min(len(a), len(b))
        if small >= 5 and inter / small >= 0.7 and inter / len(a | b) >= 0.35:
            return True
        if len(a) <= len(b) and inter >= 3 and inter / len(a) >= 0.85 and now - e.get("t", 0) <= 21600:
            return True
        if small < 5 and a == b:
            return True
    return False


# ---------------------------------------------------------------- نسخه‌ی ۲: وزن IDF + لنگرها
TAU = 3.0           # idf ≥ این = «واژه‌ی اختصاصی» (حدود کمتر از ~۵٪ پیام‌های اخیر)
MIN_DOCS = 50       # با کمتر از این تعداد پیام اخیر، وزن‌دهی IDF بی‌اعتبار است (همه‌ی واژه‌ها هم‌وزن)
MISMATCH = 0.4      # اگر وزنِ جزئیاتِ «فقط در یک طرف» (از هر دو طرف) بیش از این نسبت از وزنِ بخش مشترک باشد و هر دو طرف چنین جزئیاتی داشته باشند: دو خبر جدا


def context(entries):
    """DF واژه‌ها روی پیام‌های اخیر؛ واژه‌ی رایجِ همین ساعت‌ها (مثل «حمله» روز جنگ) وزن کم می‌گیرد."""
    df = {}
    for e in entries:
        for h in set(e["w"]):
            df[h] = df.get(h, 0) + 1
    return df, len(entries)


def _idf(h, df, n):
    import math
    return 1.0 if n < MIN_DOCS else math.log((n + 1) / (df.get(h, 0) + 1))


def compare(sig, e, ctx):
    """مقایسه‌ی دو پیام. خروجی: دیکشنری معیارها (همه‌ی نسبت‌ها وزن‌دار با IDF)."""
    df, n = ctx
    a, b = set(sig["w"]), set(e["w"])
    an, bn = set(sig["n"]), set(e["n"])
    ap, bp = set(sig.get("p", [])), set(e.get("p", []))
    conflict = bool((an and bn and not (an <= bn or bn <= an)) or (ap and bp and not (ap & bp)))
    w = lambda s: sum(_idf(h, df, n) for h in s)
    inter = a & b
    wa, wb, wi = w(a), w(b), w(inter)
    specific = n >= MIN_DOCS
    xa, xb = a - b, b - a                       # جزئیاتِ فقط-در-یک-طرف
    wu = wa + wb - wi
    return {
        "conflict": conflict, "n_inter": len(inter), "small": min(len(a), len(b)), "equal": a == b, "len_a": len(a), "len_b": len(b),
        "wo_min": wi / min(wa, wb) if min(wa, wb) else 0.0, "wo_a": wi / wa if wa else 0.0,
        "wj": wi / (wa + wb - wi) if (wa + wb - wi) else 0.0,
        "distinct_inter": sum(1 for h in inter if _idf(h, df, n) >= TAU) if specific else len(inter),
        "mismatch": bool(specific and xa and xb and wi and (w(xa) + w(xb)) / wi >= MISMATCH),
        "specific": specific,
    }


def is_duplicate(sig, entries, now=None):
    """پیام تازه تکراری است اگر با یکی از پیام‌های اخیر «همان واقعه» باشد:
    - مکان یا عدد متفاوت، یا جزئیات اختصاصیِ متفاوت در هر دو طرف → هرگز تکراری نیست؛
    - شباهت وزن‌دار بالا و دست‌کم ۲ واژه‌ی اختصاصیِ مشترک (نه فقط واژه‌های رایج)؛
    - یا تیتر/خلاصه‌ی پیامی که تا ۶ ساعت پیش فرستاده‌ایم."""
    import time as _t
    now = _t.time() if now is None else now
    if not sig["w"]:
        return False
    ctx = context(entries)
    need = 2 if ctx[1] >= MIN_DOCS else 0
    for e in entries:
        c = compare(sig, e, ctx)
        if c["conflict"] or c["mismatch"]:
            continue
        if c["small"] >= 5 and ((c["wo_min"] >= 0.9 and c["wj"] >= 0.6)                       # پوشش تقریباً کامل
                                or (c["wo_min"] >= 0.75 and c["wj"] >= 0.45 and c["distinct_inter"] >= need)):
            return True
        if c["len_a"] <= c["len_b"] and c["n_inter"] >= 3 and c["wo_a"] >= 0.85 and c["distinct_inter"] >= max(need, 1) \
                and now - e.get("t", 0) <= 21600:
            return True
        if c["small"] < 5 and c["equal"]:
            return True
    return False
