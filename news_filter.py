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
