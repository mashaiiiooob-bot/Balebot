import re

URL_RE = re.compile(r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/\S*", re.I)
MENTION_RE = re.compile(r"(?<![\w.])@[A-Za-z]\w{3,}")


def clean_text(text, names=()):
    """لینک‌های تلگرام، آیدی‌ها (@...) و نام کانال منبع را از متن حذف می‌کند."""
    lines = []
    for line in (text or "").splitlines():
        new = URL_RE.sub("", line)
        new = MENTION_RE.sub("", new)
        for name in names:
            if name and len(name.strip()) >= 3:
                new = re.sub(re.escape(name.strip()), "", new, flags=re.I)
        new = re.sub(r"[ \t]{2,}", " ", new).strip()
        if new != line.strip() and not re.search(r"\w", new):
            continue  # خطی که فقط باقی‌مانده‌ی نماد/ایموجی پس از حذف است
        lines.append(new)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
