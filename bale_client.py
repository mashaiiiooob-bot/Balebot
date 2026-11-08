import requests

TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


def split_text(text: str, limit: int = TEXT_LIMIT):
    """متن بلند را روی مرز خط/فاصله به بخش‌های مجاز تقسیم می‌کند."""
    text = text or ""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


class BaleClient:
    def __init__(self, token: str):
        self.base = f"https://tapi.bale.ai/bot{token}/"
        self.session = requests.Session()

    def _post(self, method, data=None, files=None):
        try:
            response = self.session.post(
                self.base + method, data=data, files=files, timeout=45
            )
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            # پیام خطای requests شامل URL (و توکن) است؛ آن را چاپ نمی‌کنیم.
            status = exc.response.status_code if exc.response is not None else "?"
            raise RuntimeError(f"Bale HTTP {status} on {method}") from None
        except requests.RequestException as exc:
            raise RuntimeError(f"Bale network error on {method}: {type(exc).__name__}") from None
        except ValueError:
            raise RuntimeError(f"Bale invalid JSON on {method}") from None
        if not payload.get("ok"):
            raise RuntimeError(f"Bale API rejected method: {method}")
        return payload.get("result")

    def send_text(self, chat_id, text):
        result = None
        for chunk in split_text(text):
            result = self._post("sendMessage", {"chat_id": chat_id, "text": chunk})
        return result

    def delete_message(self, chat_id, message_id):
        return self._post("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def send_media(self, chat_id, path, caption, media_type):
        method = {
            "photo": "sendPhoto",
            "video": "sendVideo",
            "document": "sendDocument",
            "audio": "sendAudio",
        }.get(media_type, "sendDocument")
        field = {
            "photo": "photo",
            "video": "video",
            "audio": "audio",
        }.get(media_type, "document")

        caption = caption or ""
        too_long = len(caption) > CAPTION_LIMIT
        data = {"chat_id": chat_id}
        if not too_long and caption:
            data["caption"] = caption
        with open(path, "rb") as handle:
            result = self._post(method, data=data, files={field: handle})
        if too_long:
            # کپشن بیش از حد مجاز است؛ متن کامل جداگانه ارسال می‌شود.
            self.send_text(chat_id, caption)
        return result
