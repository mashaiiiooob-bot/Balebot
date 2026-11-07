"""بررسی دوره‌ای کانال‌های تلگرام و ارسال خبرها به بله (برای GitHub Actions)."""
import asyncio
import hashlib
import logging
import os
import re
import shutil
import sys
import tempfile
import time

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession

import settings
from bale_client import BaleClient
from news_filter import is_news
from state import State

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("balebot")


def source_label(entity):
    title = getattr(entity, "title", None)
    username = getattr(entity, "username", None)
    return title or (f"@{username}" if username else str(getattr(entity, "id", "Telegram")))


def key_for(entity):
    return hashlib.sha256(str(entity.id).encode()).hexdigest()[:16]


def build_caption(text, source):
    parts = []
    if text:
        parts.append(text.strip())
    parts.append(f"منبع: {source}")
    parts.append(settings.SIGNATURE)
    return "\n\n".join(parts)


def media_kind(message):
    mime = getattr(message.file, "mime_type", "") or ""
    if mime.startswith("image/"):
        return "photo"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


async def forward(bale, message, label, tmpdir):
    caption = build_caption(message.message or "", label)
    media_path = None
    try:
        size = getattr(message.file, "size", 0) or 0
        if message.media and size <= settings.MAX_FILE_MB * 1024 * 1024:
            media_path = await message.download_media(file=tmpdir)
        if media_path:
            await asyncio.to_thread(
                bale.send_media, settings.BALE_DESTINATION, media_path, caption, media_kind(message)
            )
        else:
            await asyncio.to_thread(bale.send_text, settings.BALE_DESTINATION, caption)
    finally:
        if media_path and os.path.exists(media_path):
            try:
                os.remove(media_path)
            except OSError:
                pass


async def fetch_new(client, entity, last_id):
    if last_id:
        return [m async for m in client.iter_messages(entity, min_id=last_id, reverse=True, limit=100)]
    return None


async def process_source(client, bale, state, entity, tmpdir):
    key = key_for(entity)
    label = source_label(entity)
    last = state.last_id(key)

    if last:
        messages = await fetch_new(client, entity, last)
    elif settings.BACKFILL_LIMIT > 0:
        messages = list(reversed(await client.get_messages(entity, limit=settings.BACKFILL_LIMIT)))
    else:
        latest = await client.get_messages(entity, limit=1)
        if latest:
            state.set_last_id(key, latest[0].id)
            log.info("First run for %s: starting from now", label)
        return

    for msg in messages:
        if msg.id <= last:
            continue
        if is_news(msg.message or ""):
            try:
                await forward(bale, msg, label, tmpdir)
                log.info("Forwarded %s message_id=%s", label, msg.id)
            except FloodWaitError:
                raise
            except Exception as exc:
                state.add_fail(key, msg.id)
                fails = state.fail_count(key, msg.id)
                log.error("Forward failed %s id=%s (%d/%d): %s", label, msg.id, fails,
                          settings.MAX_FAILS, type(exc).__name__)
                state.save()
                if fails < settings.MAX_FAILS:
                    return  # checkpoint جلو نمی‌رود؛ دور بعد دوباره تلاش می‌شود
        state.clear_fail(key, msg.id)
        state.set_last_id(key, msg.id)
        state.save()


async def resolve_sources(client):
    try:
        await client.get_dialogs()  # کش کردن موجودیت‌ها؛ از resolve مکرر یوزرنیم جلوگیری می‌کند
    except Exception as exc:
        log.warning("get_dialogs failed: %s", type(exc).__name__)
    entities = []
    for index, source in enumerate(settings.get_sources(), 1):
        try:
            target = int(source) if re.fullmatch(r"-?\d+", source) else source
            entity = await client.get_entity(target)
            entities.append(entity)
            log.info("Source ready: %s", source_label(entity))
        except Exception as exc:
            # عمداً خود منبع (ممکن است لینک خصوصی باشد) چاپ نمی‌شود؛ لاگ عمومی است.
            log.error("Could not resolve source #%d: %s", index, type(exc).__name__)
    return entities


async def run():
    required = ["TG_API_ID", "TG_API_HASH", "TG_SESSION", "BALE_BOT_TOKEN", "SOURCES"]
    missing = [n for n in required if not os.environ.get(n, "").strip()]
    if missing:
        log.error("Missing secrets: %s", ", ".join(missing))
        return 2

    client = TelegramClient(
        StringSession(os.environ["TG_SESSION"].strip()),
        int(os.environ["TG_API_ID"]),
        os.environ["TG_API_HASH"].strip(),
    )
    bale = BaleClient(os.environ["BALE_BOT_TOKEN"].strip())
    state = State(settings.STATE_FILE)
    tmpdir = tempfile.mkdtemp(prefix="balebot_")
    deadline = time.monotonic() + settings.LOOP_SECONDS

    await client.connect()
    try:
        if not await client.is_user_authorized():
            log.error("Telegram session is not authorized. Regenerate TG_SESSION.")
            return 3
        entities = await resolve_sources(client)
        if not entities:
            log.error("No source could be resolved.")
            return 4

        while True:
            for entity in entities:
                try:
                    await process_source(client, bale, state, entity, tmpdir)
                except FloodWaitError as exc:
                    log.warning("Telegram flood wait: %ss", exc.seconds)
                    if exc.seconds > max(0, deadline - time.monotonic()):
                        state.save()
                        return 0
                    await asyncio.sleep(exc.seconds + 1)
                except Exception as exc:
                    log.error("Source %s failed: %s", source_label(entity), type(exc).__name__)
            state.save()
            if time.monotonic() + settings.POLL_INTERVAL >= deadline:
                break
            await asyncio.sleep(settings.POLL_INTERVAL)
        return 0
    finally:
        state.save()
        await client.disconnect()
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
