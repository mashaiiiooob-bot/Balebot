"""بررسی دوره‌ای کانال‌های تلگرام و ارسال خبرها به بله (برای GitHub Actions)."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import requests
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.sessions import StringSession
from telethon import utils
from telethon.tl.functions.messages import CheckChatInviteRequest, GetPeerDialogsRequest
from telethon.tl.types import Channel, ChatInviteAlready, InputDialogPeer, InputPeerChannel

import settings
from bale_client import BaleClient
from cleaner import clean_text
from news_filter import is_news
from state import State

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("balebot")


INVITE_RE = re.compile(r"(?:t\.me|telegram\.me)/(?:joinchat/|\+)([\w-]+)", re.I)


def source_label(entity):
    title = getattr(entity, "title", None)
    username = getattr(entity, "username", None)
    return title or (f"@{username}" if username else str(getattr(entity, "id", "Telegram")))


def key_for(entity):
    # HMAC با راز خصوصی: از روی state عمومی نمی‌توان فهمید یک کانال جزو منابع هست یا نه
    secret = (os.environ.get("TG_API_HASH", "") + "|balebot-key").encode()
    return hmac.new(secret, str(entity.id).encode(), hashlib.sha256).hexdigest()[:16]


def build_caption(text):
    parts = []
    if text:
        parts.append(text.strip())
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


def sanitize_media(path, kind):
    """نام اصلی فایل را خنثی می‌کند و (در صورت وجود ffmpeg) متادیتای ویدیو/صدا را پاک می‌کند."""
    ext = os.path.splitext(path)[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", ext):
        ext = ""
    neutral = os.path.join(os.path.dirname(path), "media" + ext)
    os.replace(path, neutral)
    if kind in ("video", "audio") and shutil.which("ffmpeg"):
        out = os.path.join(os.path.dirname(path), "clean" + ext)
        try:
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", neutral,
                 "-map", "0", "-map_metadata", "-1", "-c", "copy", out],
                timeout=120, capture_output=True,
            )
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
                os.replace(out, neutral)
        except Exception:
            pass
        finally:
            if os.path.exists(out):
                os.remove(out)
    return neutral


async def forward(bale, message, text, tmpdir):
    caption = build_caption(text)
    media_path = None
    try:
        size = getattr(message.file, "size", 0) or 0
        if message.media and size <= settings.MAX_FILE_MB * 1024 * 1024:
            media_path = await message.download_media(file=tmpdir)
            if media_path:
                media_path = await asyncio.to_thread(sanitize_media, media_path, media_kind(message))
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


async def alert(bale, state, code, text):
    """هشدار خطا به چت خصوصی مدیر در بله (حداکثر یک بار در ۲۴ ساعت برای هر نوع خطا)."""
    chat = settings.BALE_ADMIN_CHAT
    now = time.time()
    if not chat or not state.alert_due(code, now):
        return
    try:
        await asyncio.to_thread(bale.send_text, chat, "⚠️ Balebot: " + text)
        state.mark_alert(code, now)
        state.save()
    except Exception:
        log.warning("Admin alert could not be sent")


def ping_healthcheck():
    try:
        requests.get(settings.HEALTHCHECK_URL, timeout=10)
    except Exception:
        pass  # آدرس هرگز چاپ نمی‌شود


async def fetch_new(client, entity, last_id):
    if last_id:
        return [m async for m in client.iter_messages(entity, min_id=last_id, reverse=True, limit=100)]
    return None


async def process_source(client, bale, state, entity, tmpdir):
    key = key_for(entity)
    tag = key[:6]  # نام کانال در لاگ عمومی چاپ نمی‌شود
    names = [getattr(entity, "title", None), getattr(entity, "username", None)]
    last = state.last_id(key)

    if last:
        messages = await fetch_new(client, entity, last)
    elif settings.BACKFILL_LIMIT > 0:
        messages = list(reversed(await client.get_messages(entity, limit=settings.BACKFILL_LIMIT)))
    else:
        latest = await client.get_messages(entity, limit=1)
        if latest:
            state.set_last_id(key, latest[0].id)
            log.info("First run for source %s: starting from now", tag)
        return

    for msg in messages:
        if msg.id <= last:
            continue
        text = clean_text(msg.message or "", names)
        if is_news(text):
            try:
                await forward(bale, msg, text, tmpdir)
                log.info("Forwarded source=%s", tag)
            except FloodWaitError:
                raise
            except Exception as exc:
                state.add_fail(key, msg.id)
                fails = state.fail_count(key, msg.id)
                log.error("Forward failed source=%s (%d/%d): %s", tag, fails,
                          settings.MAX_FAILS, type(exc).__name__)
                state.save()
                if fails < settings.MAX_FAILS:
                    return  # checkpoint جلو نمی‌رود؛ دور بعد دوباره تلاش می‌شود
                await alert(bale, state, "skipped",
                            f"a message was skipped after {settings.MAX_FAILS} failed sends (source {tag}).")
        state.clear_fail(key, msg.id)
        state.set_last_id(key, msg.id)
        state.save()


def write_status(info):
    """وضعیت غیرحساس برای عیب‌یابی (نوع خطاها) در کنار state ذخیره می‌شود."""
    try:
        path = os.path.join(os.path.dirname(settings.STATE_FILE) or ".", "status.json")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False, indent=1, sort_keys=True)
    except OSError:
        pass


async def resolve_sources(client, state):
    sources = settings.get_sources()
    status = {"dialogs": "ok", "count": len(sources), "sources": {}}
    failed = []
    try:
        await client.get_dialogs()  # کش کردن موجودیت‌ها (کانال‌های عضو‌شده بدون درخواست resolve پیدا می‌شوند)
    except Exception as exc:
        status["dialogs"] = type(exc).__name__
        log.warning("get_dialogs failed: %s", type(exc).__name__)
    entities = []
    for index, source in enumerate(sources, 1):
        try:
            invite = INVITE_RE.search(source)
            if invite:
                res = await client(CheckChatInviteRequest(invite.group(1)))
                if not isinstance(res, ChatInviteAlready):
                    raise ValueError("not a member")
                entity = res.chat
            else:
                target = int(source) if re.fullmatch(r"-?\d+", source) else source
                entity = await client.get_entity(target)
                await asyncio.sleep(0.5)  # فاصله بین resolveها (جلوگیری از FloodWait)
            entities.append(entity)
            status["sources"][str(index)] = "ok"
            log.info("Source #%d ready", index)
        except FloodWaitError as exc:
            failed.append(index)
            status["sources"][str(index)] = f"FloodWait {exc.seconds}s"
            log.warning("Source #%d flood wait %ss; retry next run", index, exc.seconds)
        except Exception as exc:
            failed.append(index)
            # فقط نوع خطا ثبت می‌شود، نه متن آن (متن خطا ممکن است یوزرنیم منبع را داشته باشد)
            status["sources"][str(index)] = type(exc).__name__
            log.error("Could not resolve source #%d: %s", index, type(exc).__name__)
    state.save()
    write_status(status)
    return entities, failed


async def changed_entities(client, state, entities):
    """با یک درخواست، کانال‌هایی را که پیام جدید دارند پیدا می‌کند؛ در صورت خطا همه را برمی‌گرداند."""
    try:
        tops = {}
        for i in range(0, len(entities), 90):
            chunk = entities[i:i + 90]
            peers = [InputDialogPeer(await client.get_input_entity(e)) for e in chunk]
            res = await client(GetPeerDialogsRequest(peers=peers))
            for d in res.dialogs:
                tops[utils.get_peer_id(d.peer)] = d.top_message
        out = []
        for e in entities:
            top = tops.get(utils.get_peer_id(e))
            last = state.last_id(key_for(e))
            if top is None or not last or top > last:
                out.append(e)
        return out
    except Exception as exc:
        log.warning("Peer-dialog precheck failed (%s); polling all", type(exc).__name__)
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
            await alert(bale, state, "session", "the Telegram session is invalid. Regenerate TG_SESSION (python generate_session.py) and update the secret.")
            return 3
        entities, failed = await resolve_sources(client, state)
        if not entities:
            log.error("No source could be resolved.")
            await alert(bale, state, "nosources", "no source could be resolved. Check SOURCES and the Telegram membership.")
            return 4
        if failed:
            await alert(bale, state, "badsources",
                        f"{len(failed)} source(s) could not be resolved (positions in SOURCES: {', '.join(map(str, failed))}).")

        # رویداد تلگرام: به‌محض رسیدن پیام جدید حلقه بیدار می‌شود (تأخیر حدود ۱ ثانیه)
        wake = asyncio.Event()

        @client.on(events.NewMessage(chats=entities))
        async def _on_new_message(event):
            wake.set()

        last_ping = 0.0
        wake.set()  # دور اول: بررسی فوری برای جبران پیام‌های زمان خاموشی
        while True:
            wake.clear()
            for entity in await changed_entities(client, state, entities):
                try:
                    await process_source(client, bale, state, entity, tmpdir)
                except FloodWaitError as exc:
                    log.warning("Telegram flood wait: %ss", exc.seconds)
                    if exc.seconds > max(0, deadline - time.monotonic()):
                        state.save()
                        return 0
                    await asyncio.sleep(exc.seconds + 1)
                except Exception as exc:
                    log.error("Source %s failed: %s", key_for(entity)[:6], type(exc).__name__)
            state.save()
            if settings.HEALTHCHECK_URL and time.monotonic() - last_ping >= 240:
                last_ping = time.monotonic()
                await asyncio.to_thread(ping_healthcheck)
            if time.monotonic() + settings.POLL_INTERVAL >= deadline:
                break
            # تا رویداد جدید بیاید منتظر می‌ماند؛ POLL_INTERVAL فقط پشتیبان است
            try:
                await asyncio.wait_for(wake.wait(), timeout=settings.POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
        return 0
    finally:
        state.save()
        await client.disconnect()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_bale():
    """یک پیام آزمایشی به مقصد بله می‌فرستد و نتیجه را در status.json می‌نویسد."""
    token = os.environ.get("BALE_BOT_TOKEN", "").strip()
    result = "ok"
    try:
        BaleClient(token).send_text(settings.BALE_DESTINATION, "✅ تست اتصال Balebot — این پیام آزمایشی است.")
    except Exception as exc:
        result = f"{type(exc).__name__}: {exc}"[:160]
    write_status({"bale_test": result})
    log.info("Bale test: %s", "ok" if result == "ok" else "failed")
    return 0 if result == "ok" else 5


if __name__ == "__main__":
    if "--test-bale" in sys.argv:
        sys.exit(test_bale())
    sys.exit(asyncio.run(run()))
