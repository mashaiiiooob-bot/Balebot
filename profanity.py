"""فیلتر فحش فارسی (سطح ۱ ملایم ← سطح ۳ رکیک).

پیام حاوی هر کدام از این‌ها ارسال نمی‌شود. تطبیق:
- واژه‌ی کامل (با پسوندهای رایج مثل ی/م/ت/ها)، تا «کیرکوک» یا «خرم‌آباد» اشتباهاً حذف نشوند
- مقاوم در برابر کشیده‌نویسی (کیییر)، نقطه/خط/ستاره بین حروف، نیم‌فاصله، ی/ک عربی
- عبارت‌های استثنا (مثل «کیر استارمر») پیش از تطبیق کنار گذاشته می‌شوند
عمداً نیستند: واژه‌های رسمی/حقوقی (فاحشه، جلب)، و واژه‌های پرکاربرد غیرفحش در خبر
(کس، تخم، گاو، سگ، کوس، هرزه [علف هرزه]، کثافت [آلودگی]).
در عبارت‌ها «فاصله» یعنی «فاصله یا نیم‌فاصله یا هیچ».
"""
import re

LEVELS = {
    1: [  # توهین ملایم
        "احمق", "خر", "الاغ", "گاگول", "خنگ", "کودن", "ابله", "خرفت", "الدنگ", "اوسکول", "اسکول",
        "چاقال", "پفیوز", "مرتیکه", "عنتر", "نکبت", "کثافت کار", "عوضی", "لعنتی", "آشغال",
        "بی شعور", "بی تربیت", "بی ادب", "بی عرضه", "بی خاصیت", "بی وجدان", "بی شرف", "پست فطرت", "رذل",
        "لاشی", "گمشو", "گم شو", "خفه شو", "خفه شین", "خفه شید", "گور بابات", "گورتو گم کن",
    ],
    2: [  # فحش متوسط
        "گه", "گوه", "گه نخور", "گوه نخور", "گه خور", "گوه خور", "گوز", "تخمی", "تخم سگ",
        "پدر سگ", "پدر سوخته", "حرامزاده", "حروم زاده", "ولد زنا", "بی ناموس", "بی غیرت", "دیوث",
        "ریدم", "ریدی", "ریده", "ریدن", "ریدند", "ریدیم", "ریدید", "ریدمان", "ریدمت",
        "کس خل", "کصخل", "کس شر", "کصشر", "کس مغز", "خایه", "خایه مال", "جق", "جقی", "جقول",
        "پدرتو در میارم", "ننه سگ",
    ],
    3: [  # رکیک
        "کیر", "کیر خر", "کیر تو", "کیرتو", "کیرمو", "کون", "کونی", "کونده", "کون گشاد", "کون ده",
        "کونکش", "کوندهی", "جنده", "جندگی", "جنده بازی", "جاکش", "جاکشی", "قحبه", "قحبگی", "قهبه",
        "مادر جنده", "مادر قحبه", "ننه جنده", "ننه قحبه", "خواهر جنده", "خواهر قحبه",
        "کس کش", "کص کش", "کوس کش", "کس ننه", "کس ننت", "کس مادر", "کس خواهر", "کس ناموس", "کس مادرت",
        "کص", "کوص", "خار کسه", "خار کسده", "خار کسته", "خار کصه", "خار کصده",
        "گایید", "گاییدن", "گایی", "بگا", "بگام", "گاییدمت", "گاییدمش",
        # فینگلیش (فقط موارد بی‌ابهام)
        "koskesh", "kos kesh", "kosnanat", "kos nanat", "kos nane", "jende", "jendeh", "jakesh",
        "haroomzade", "haramzade", "madar jende", "madarjende", "pedar sag", "pedarsag", "pedar sookhte",
        "kir khar", "kirkhar", "kooni", "koonde", "ghahbe", "goh nakhor", "gooh nakhor",
    ],
}

# عبارت‌هایی که حاوی «ریشه‌ی فحش» اما بی‌ضرر هستند (نام افراد/مکان‌ها، اصطلاح‌ها)
EXEMPT = ["کیر استارمر", "کیراستارمر", "کیر هاردی", "کیرکوک", "کیر کوک", "گه گاه", "گه گاهی", "گه‌گاه"]

# پسوندهای اسمی/ضمیری؛ پسوندهای فعلی (ن، ند، ید...) عمداً نیستند (تخمی+ن = تخمین!)
_FULL_SUFFIX = ("ها", "های", "ی", "ه", "م", "ت", "ش", "مو", "تو", "شو", "مون", "تون", "شون", "ان", "ا", "مت", "مش")
_SHORT_SUFFIX = ("ها", "های")   # واژه‌های کوتاه/مبهم (مثل «خر» ← «خرم‌آباد») پسوند محدود دارند
_SHORT = {"گه", "گوه", "خر", "گوز", "کص", "کوص", "جق", "عنتر", "بگا"}
_SEP = r"[\u200c._\-*·]*"

_MAP = str.maketrans({"ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ۀ": "ه", "ة": "ه", "ہ": "ه", "ھ": "ه"})
_STRIP = re.compile("[\u064B-\u065F\u0670\u0640]")


def _norm(text: str) -> str:
    text = (text or "").translate(_MAP)
    text = _STRIP.sub("", text)
    return text.casefold()


def _pattern(word: str) -> str:
    parts, prev_letter = [], False
    for ch in _norm(word):
        if ch in " \u200c":
            parts.append(r"[\s\u200c]*")
            prev_letter = False
        else:
            if prev_letter:
                parts.append(_SEP)
            parts.append(re.escape(ch) + "+")
            prev_letter = True
    return "".join(parts)


_cache = {}


def _compile(min_level: int):
    if min_level in _cache:
        return _cache[min_level]
    full, short = [], []
    for level, words in LEVELS.items():
        if level < min_level:
            continue
        for w in words:
            (short if w in _SHORT else full).append(_pattern(w))

    def build(pats, suffixes):
        if not pats:
            return None
        pats = sorted(set(pats), key=len, reverse=True)
        suf = "|".join(sorted((re.escape(s) for s in suffixes), key=len, reverse=True))
        return re.compile(r"(?<![\w\u200c])(?:" + "|".join(pats) + r")(?:\u200c?(?:" + suf + r"))?(?![\w\u200c])")

    exempt = re.compile("|".join(_pattern(e) for e in EXEMPT))
    _cache[min_level] = (build(full, _FULL_SUFFIX), build(short, _SHORT_SUFFIX), exempt)
    return _cache[min_level]


def has_profanity(text: str, min_level: int = 1) -> bool:
    """True اگر متن فحشِ هم‌سطح یا رکیک‌تر از min_level داشته باشد (۱ = همه)."""
    value = _norm(text)
    if not value.strip():
        return False
    full, short, exempt = _compile(max(1, min(3, int(min_level))))
    value = exempt.sub(" ", value)
    return any(rx.search(value) for rx in (full, short) if rx is not None)
