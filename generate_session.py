"""یک بار روی گوشی/کامپیوتر خودت اجرا کن تا TG_SESSION برای GitHub Secrets ساخته شود.
خروجی معادل ورود کامل به حساب تلگرام توست؛ فقط در Secrets بگذار و هیچ‌جا منتشر نکن."""
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID: ").strip())
api_hash = input("API HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\n=== TG_SESSION (کپی کن و در GitHub Secrets بگذار) ===")
    print(client.session.save())
