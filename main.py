import asyncio
import logging
import os
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError

import config
from bale_client import BaleClient
from news_filter import is_news
from storage import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("balebot")
MAX_ATTEMPTS = 3

client = TelegramClient("balebot_user", config.API_ID, config.API_HASH)
bale = BaleClient(config.BALE_BOT_TOKEN)
store = Store(config.DATABASE_PATH)
Path(config.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

def source_label(entity):
    title = getattr(entity, "title", None)
    username = getattr(entity, "username", None)
    return title or (f"@{username}" if username else str(getattr(entity, "id", "Telegram")))

def build_caption(text, source):
    parts = []
    if text:
        parts.append(text.strip())
    parts.append(f"منبع: {source}")
    parts.append(config.SIGNATURE)
    return "\n\n".join(parts)

async def send_message(message, entity):
    source_id = str(entity.id)
    if store.was_sent(source_id, message.id):
        return

    text = message.message or ""
    if not is_news(text):
        return

    caption = build_caption(text, source_label(entity))
    media_path = None
    try:
        if message.media:
            media_path = await message.download_media(file=config.DOWNLOAD_DIR)
        if media_path:
            mime = getattr(message.file, "mime_type", "") or ""
            if mime.startswith("image/"):
                media_type = "photo"
            elif mime.startswith("video/"):
                media_type = "video"
            elif mime.startswith("audio/"):
                media_type = "audio"
            else:
                media_type = "document"
            await asyncio.to_thread(
                bale.send_media, config.BALE_DESTINATION, media_path, caption, media_type
            )
        else:
            await asyncio.to_thread(
                bale.send_text, config.BALE_DESTINATION, caption
            )
        store.mark_sent(source_id, message.id)
        store.set_last_id(source_id, message.id)
        log.info("Forwarded source=%s message_id=%s", source_id, message.id)
    finally:
        if media_path and os.path.exists(media_path):
            try:
                os.remove(media_path)
            except OSError:
                pass

async def resolve_sources():
    resolved = []
    for source in config.SOURCES:
        try:
            entity = await client.get_entity(source)
            resolved.append(entity)
            log.info("Source ready: %s", source_label(entity))
        except Exception:
            log.exception("Could not resolve source: %s", source)
    return resolved

async def backfill(entity):
    source_id = str(entity.id)
    last_id = store.last_id(source_id)
    try:
        # اگر checkpoint وجود دارد، پیام‌های جدیدتر از آن واکشی می‌شوند.
        if last_id:
            messages = []
            async for msg in client.iter_messages(entity, min_id=last_id, reverse=True):
                messages.append(msg)
        else:
            messages = []
            async for msg in client.iter_messages(entity, limit=config.BACKFILL_LIMIT, reverse=True):
                messages.append(msg)

        for msg in messages:
            ok = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    await send_message(msg, entity)
                    ok = True
                    break
                except FloodWaitError as exc:
                    log.warning("Telegram flood wait: %s seconds", exc.seconds)
                    await asyncio.sleep(exc.seconds + 1)
                except Exception:
                    log.exception("Backfill message %s failed (attempt %d/%d)",
                                  msg.id, attempt, MAX_ATTEMPTS)
                    await asyncio.sleep(config.POLL_RETRY_SECONDS)
            if not ok:
                # checkpoint جلو نمی‌رود تا پیام در اجرای بعدی دوباره تلاش شود.
                log.error("Giving up for now on source=%s at message %s", source_id, msg.id)
                break
            store.set_last_id(source_id, msg.id)
    except Exception:
        log.exception("Backfill failed for %s", source_label(entity))

async def main():
    await client.start()
    me = await client.get_me()
    log.info("Telegram connected as %s", getattr(me, "username", None) or me.id)
    sources = await resolve_sources()
    if not sources:
        log.error("No source could be resolved. Check SOURCES in config.py.")
        return

    for entity in sources:
        await backfill(entity)

    @client.on(events.NewMessage(chats=sources))
    async def handler(event):
        try:
            await send_message(event.message, event.chat)
        except FloodWaitError as exc:
            log.warning("Telegram flood wait: %s seconds", exc.seconds)
            await asyncio.sleep(exc.seconds + 1)
        except Exception:
            # عمداً URL درخواست Bale یا توکن در لاگ چاپ نمی‌شود.
            log.exception("Message processing failed")

    log.info("Monitoring %d source(s). Press Ctrl+C to stop.", len(sources))
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        store.close()
