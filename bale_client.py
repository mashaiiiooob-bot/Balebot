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


class BaleError(RuntimeError):
    """خطای API بله. هنوز RuntimeError است و متن پیام همان قبلی است؛ فقط جزئیات ساخت‌یافته (طبق مستندات رسمی:
    error_code و parameters.retry_after) هم دارد. متن/توضیح پاسخ عمداً در پیام نمی‌آید."""

    def __init__(self, message, status=None, error_code=None, retry_after=0):
        super().__init__(message)
        self.status, self.error_code, self.retry_after = status, error_code, retry_after


def _retry_after(params):
    try:
        return max(0, int((params or {}).get("retry_after", 0)))
    except (TypeError, ValueError, AttributeError):
        return 0


def _error_details(resp):
    """(retry_after, error_code) از بدنه‌ی JSON خطا؛ اگر بدنه JSON نبود (0, None)."""
    try:
        body = resp.json()
        return _retry_after(body.get("parameters")), body.get("error_code")
    except Exception:       # noqa: BLE001
        return 0, None


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
            resp = exc.response
            status = resp.status_code if resp is not None else "?"
            retry_after, code = _error_details(resp)
            raise BaleError(f"Bale HTTP {status} on {method}", status=status, error_code=code, retry_after=retry_after) from None
        except requests.RequestException as exc:
            raise BaleError(f"Bale network error on {method}: {type(exc).__name__}") from None
        except ValueError:
            raise BaleError(f"Bale invalid JSON on {method}") from None
        if not payload.get("ok"):
            retry_after = _retry_after(payload.get("parameters"))
            raise BaleError(f"Bale API rejected method: {method}", error_code=payload.get("error_code"), retry_after=retry_after)
        return payload.get("result")

    def send_text(self, chat_id, text):
        result = None
        for chunk in split_text(text):
            result = self._post("sendMessage", {"chat_id": chat_id, "text": chunk})
        return result

    def get_me(self):
        return self._post("getMe")

    def get_chat(self, chat_id):
        return self._post("getChat", {"chat_id": chat_id})

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
