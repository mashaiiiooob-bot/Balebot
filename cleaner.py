"""پاک‌سازی متن پیام: هر نشانه‌ای از منبع (نام، شعار، آیدی، لینک، خط‌های «منبع:») حذف می‌شود."""
import re

URL_RE = re.compile(r"tg://\S+|(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/\S*", re.I)
MENTION_RE = re.compile(r"(?<![\w.])@[A-Za-z]\w{3,}")
TG_URL_RE = re.compile(r"tg://|(?:^|//|\.)(?:t\.me|telegram\.me|telegram\.dog)/", re.I)

SEPARATORS = re.compile(r"[|•·—–\-:/،,؛;]+")
HANDLE_TOKEN = r"[A-Za-z][A-Za-z0-9_]{3,31}"
HANDLE_LINE = re.compile(rf"^[\W_]*{HANDLE_TOKEN}(?:[\W_]+{HANDLE_TOKEN})?[\W_]*$")
SAFE_WORDS = {"breaking", "news", "update", "urgent", "live", "alert", "video", "photo",
              "photos", "report", "exclusive", "warning", "latest", "today", "video"}
CREDIT_LABEL = r"(?:منبع|source|via|ref|credit)"
CREDIT_EMPTY = re.compile(rf"^[\W_]*{CREDIT_LABEL}\s*[:：\-–]?\s*[\W_]*$", re.I)
CREDIT_HANDLE = re.compile(rf"^[\W_]*{CREDIT_LABEL}\s*[:：\-–]\s*(?:{HANDLE_TOKEN}[\W_]*){{1,2}}$", re.I)
EDGE_SEP = re.compile(r"^[\s|•·—–\-:/،,؛;]+|[\s|•·—–\-:/،,؛;]+$")
SHORT_TAG = re.compile(r"^[A-Za-z0-9]{2,8}$")
PIPE_TAG_LINE = re.compile(r"^[\W_]*[|•·]\s*[A-Za-z0-9]{2,8}[\W_]*$")
PIPE_WORD_LINE = re.compile(r"^[^\w|]*\|\s*[\w\u200c]+(?:\s+[\w\u200c]+){0,2}\s*$")
CAPS_TAG_LINE = re.compile(r"^[\W_]*[A-Z0-9]{2,6}[\W_]*$")
RTL_RE = re.compile(r"[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]")

_LINK_TYPES = {"MessageEntityTextUrl", "MessageEntityUrl", "MessageEntityMention", "MessageEntityMentionName"}
_NORM = str.maketrans({"ي": "ی", "ك": "ک"})


def _norm(s):
    out = s.translate(_NORM).lower()
    return out if len(out) == len(s) else s.lower()


def build_terms(names=(), blocklist=()):
    """از نام‌ها و بلاک‌لیست، همه‌ی عبارت‌هایی که باید حذف شوند را می‌سازد (شعار پشت | یا - هم جدا حذف می‌شود)."""
    terms = set()
    for raw in list(names) + list(blocklist):
        raw = (raw or "").strip().lstrip("@")
        if len(raw) < 3:
            continue
        terms.add(raw)
        terms.add(raw.replace("_", " "))
        plain = re.sub(r"[^\w\s\u200c]", "", raw).strip()
        if len(plain) >= 3:
            terms.add(plain)
        parts = [p.strip() for p in SEPARATORS.split(raw)]
        if len(parts) > 1:
            for part in parts:
                # قطعه‌ی عمومی تک‌کلمه‌ای (مثل «اخبار») حذف نمی‌شود؛ فقط عبارت‌های ≥۲ کلمه یا بلند
                if len(part) >= 8 or (len(part) >= 5 and len(part.split()) >= 2):
                    terms.add(part)
    return sorted({_norm(t) for t in terms if len(t.strip()) >= 3}, key=len, reverse=True)


def _strip_terms(line, terms):
    if not terms:
        return line
    low = _norm(line)
    spans = []
    for term in terms:
        pat = r"(?<![\w\u200c])" + re.escape(term) + r"(?![\w\u200c])"
        spans.extend(m.span() for m in re.finditer(pat, low))
    if not spans:
        return line
    spans.sort()
    out, last = [], 0
    for a, b in spans:
        if a < last:
            a = last
        if b > a:
            out.append(line[last:a])
            last = b
    out.append(line[last:])
    return "".join(out)


def _flags(text, entities):
    """بر اساس entityهای تلگرام (offset بر حسب UTF-16): کدام کاراکترها لینک/منشن‌اند و کدام باید حذف شوند."""
    n = len(text)
    remove, linked, tg = [False] * n, [False] * n, [False] * n
    if not entities:
        return remove, linked, tg
    starts, pos = [], 0
    for ch in text:
        starts.append(pos)
        pos += 2 if ord(ch) > 0xFFFF else 1
    for e in entities:
        kind = type(e).__name__
        if kind not in _LINK_TYPES:
            continue
        a, b = e.offset, e.offset + e.length
        idx = [i for i, u in enumerate(starts) if a <= u < b]
        if not idx:
            continue
        span_text = text[idx[0]:idx[-1] + 1]
        is_tg = kind in ("MessageEntityMention", "MessageEntityMentionName")
        if kind == "MessageEntityTextUrl":
            is_tg = bool(TG_URL_RE.search(getattr(e, "url", "") or ""))
        elif kind == "MessageEntityUrl":
            is_tg = bool(TG_URL_RE.search(span_text))
        for i in idx:
            linked[i] = True
            if is_tg:
                tg[i] = True
                remove[i] = True
    return remove, linked, tg


def clean_message(text, entities=None, names=(), blocklist=()):
    """متن + entityها → متن پاک‌شده. خطی که کاملاً لینک/منشن تلگرامی یا لینک کوتاه است (پایین‌نویس کانال) حذف می‌شود."""
    text = text or ""
    remove, linked, tg = _flags(text, entities)
    lines, pos = [], 0
    for raw in text.split("\n"):
        idx = range(pos, pos + len(raw))
        pos += len(raw) + 1
        if any(linked[i] for i in idx):
            residue = "".join(ch for i, ch in zip(idx, raw) if not linked[i])
            entirely_linked = not re.search(r"\w", residue)
            if entirely_linked and (any(tg[i] for i in idx) or len(raw.strip()) <= 40):
                continue
        lines.append("".join(ch for i, ch in zip(idx, raw) if not remove[i]))
    return clean_text("\n".join(lines), names, blocklist)


def clean_text(text, names=(), blocklist=()):
    terms = build_terms(names, blocklist)
    rtl_message = bool(RTL_RE.search(text or ""))
    lines = []
    for line in (text or "").splitlines():
        new = URL_RE.sub("", line)
        new = MENTION_RE.sub("", new)
        new = _strip_terms(new, terms)
        new = re.sub(r"[ \t]{2,}", " ", new).strip()
        if new != line.strip():
            new = EDGE_SEP.sub("", new)  # جداکننده‌ی یتیم (مثل «|») بعد از حذف نام
            if not re.search(r"\w", new):
                continue  # باقی‌مانده فقط نماد/ایموجی است
            if SHORT_TAG.match(new):
                continue  # فقط یک برچسب کوتاه لاتین (مثل TCH) مانده است
            if CREDIT_EMPTY.match(new):
                continue  # «منبع:» که نامش حذف شد
        if CREDIT_HANDLE.match(new):
            continue  # «منبع: someHandle»
        if PIPE_WORD_LINE.match(new):
            continue  # خطی مثل «🔴 | اخبار» (برچسب پایین‌نویس)
        if PIPE_TAG_LINE.match(new):
            continue  # خطی مثل «| TCH»
        if rtl_message and CAPS_TAG_LINE.match(new) and new.strip(" |•·-—–").lower() not in SAFE_WORDS:
            continue  # خطی که فقط یک مخفف بزرگ لاتین است
        if rtl_message and HANDLE_LINE.match(new):
            words = re.findall(HANDLE_TOKEN, new)
            if not any(w.lower() in SAFE_WORDS for w in words):
                continue  # خطی که فقط آیدی لاتین است (مثل RadioxlTV)
        lines.append(new)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
