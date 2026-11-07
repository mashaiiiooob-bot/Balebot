import os, sys
os.environ.pop("DROP_WORDS", None); os.environ.pop("DROP_WORDS_EXTRA", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import settings
from news_filter import has_dropword

WANT = ["پهلوی", "بی بی", "بی‌بی", "اتاق", "کیر", "رایگان", "عجب", "بدو", "آرایش", "شاه",
        "نیوشا صارمی", "حضرت", "اعلی", "شاهزاده"]
for w in WANT:
    assert w in settings.DROP_WORDS, w
    assert has_dropword(f"خبر فوری: {w} در بیانیه‌ای گفت", settings.DROP_WORDS), w
assert len(settings.DROP_WORDS) == len(set(settings.DROP_WORDS))
for t in ["شاهزاده رضا پهلوی", "گزارش بی‌بی‌سی", "به نقل از بی بی سی فارسی", "نیوشا  صارمی حاضر شد", "حضرت علی", "مقام اعلی", "اتاق فکر"]:
    assert has_dropword(t, settings.DROP_WORDS), t
# تطبیق واژه‌ی کامل: شهر/واژه‌های مشابه سالم می‌مانند
for t in ["شاهرود و شاهین‌شهر و شاهدان", "خبر فوری از سپاه پاسداران", "قیمت دلار و سکه امروز", "بدون تغییر و اتاقک", "عجیب و آرایشگاه", "کیرکوک"]:
    assert not has_dropword(t, settings.DROP_WORDS), t
# با Secret هم ادغام و بی‌تکرار می‌شود
os.environ["DROP_WORDS"] = "شاه\nکلمه‌ی تازه"
import importlib; importlib.reload(settings)
assert settings.DROP_WORDS.count("شاه") == 1 and "کلمه‌ی تازه" in settings.DROP_WORDS and "شاهزاده" in settings.DROP_WORDS
print("DEFAULT DROPWORDS OK", len(settings.DROP_WORDS))
