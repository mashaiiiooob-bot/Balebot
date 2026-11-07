import re

URL_RE = re.compile(r"tg://\S+|(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/\S*", re.I)
MENTION_RE = re.compile(r"(?<![\w.])@[A-Za-z]\w{3,}")


def clean_text(text, names=()):
    """لینک‌های تلگرام، آیدی‌ها (@...) و نام کانال منبع را از متن حذف می‌کند."""
    variants = set()
    for name in names:
        name = (name or "").strip()
        if len(name) >= 3:
            variants.add(name)
            variants.add(name.replace("_", " "))
            plain = re.sub(r"[^\w\s]", "", name).strip()
            if len(plain) >= 3:
                variants.add(plain)
    variants = sorted(variants, key=len, reverse=True)
    lines = []
    for line in (text or "").splitlines():
        new = URL_RE.sub("", line)
        new = MENTION_RE.sub("", new)
        for name in variants:
            new = re.sub(re.escape(name), "", new, flags=re.I)
        new = re.sub(r"[ \t]{2,}", " ", new).strip()
        if new != line.strip() and not re.search(r"\w", new):
            continue  # خطی که فقط باقی‌مانده‌ی نماد/ایموجی پس از حذف است
        lines.append(new)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
