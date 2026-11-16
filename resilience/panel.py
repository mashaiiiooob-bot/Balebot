"""پنل مدیریت درون بله: دستورها فقط از مدیرهای مجاز (BALE_ADMIN_IDS) پذیرفته می‌شود؛ بقیه بی‌پاسخ نادیده گرفته می‌شوند.

نکته‌ی مستندات رسمی بله: آپدیت‌های ارسال‌نشده فقط برای «۲۰۰۰ پیام آخر و ۲۴ ساعت» نگه داشته می‌شوند؛
اگر پنل بیش از ۲۴ ساعت خاموش باشد، دستورهای آن بازه از دست می‌روند (offset ماندگار فقط از تکرار جلوگیری می‌کند).
فقط یک مصرف‌کننده‌ی getUpdates برای هر ربات مجاز است (وب‌هوک فعال، getUpdates را مسدود می‌کند).
"""
from resilience.health import format_status

HELP = ("دستورها:\n/status وضعیت\n/pause توقف ارسال\n/resume ادامه ارسال\n/dead فهرست ناموفق‌ها (بدون متن)\n"
        "/retry تلاش دوباره برای ناموفق‌ها\n/purge پاک‌سازی قدیمی‌ها")


class Panel:
    def __init__(self, outbox, bale, admins, breaker=None):
        self.outbox, self.bale, self.admins, self.breaker = outbox, bale, {str(a) for a in admins if str(a).strip()}, breaker

    def authorized(self, msg):
        """فقط چت خصوصیِ خودِ مدیر (شناسه‌ی فرستنده = شناسه‌ی چت = عضو فهرست مدیران)."""
        fid = str((msg.get("from") or {}).get("id"))
        return fid in self.admins and fid == str((msg.get("chat") or {}).get("id"))

    def handle(self, update):
        """→ (chat_id, reply) یا None."""
        msg = update.get("message") or {}
        text = (msg.get("text") or "").strip()
        if not text.startswith("/") or not self.authorized(msg):
            return None
        cmd = text.split()[0].split("@")[0].lower()
        chat = msg["chat"]["id"]
        bo = bool(self.breaker and self.breaker.is_open)
        if cmd in ("/start", "/help"):
            return chat, HELP
        if cmd == "/status":
            return chat, format_status(self.outbox, bo)
        if cmd == "/pause":
            self.outbox.kv_set("paused", "1")
            return chat, "⏸ ارسال متوقف شد. پیام‌ها در صف می‌مانند."
        if cmd == "/resume":
            self.outbox.kv_set("paused", "0")
            return chat, "▶️ ارسال ادامه یافت."
        if cmd == "/retry":
            return chat, f"🔁 {self.outbox.retry_dead()} پیام دوباره در صف قرار گرفت."
        if cmd == "/dead":
            d = self.outbox.dead_items()
            return chat, "ناموفقی نیست." if not d else "\n".join(f"#{x['id']} | تلاش {x['attempts']} | {x['last_error']}" for x in d)
        if cmd == "/purge":
            return chat, f"🧹 {self.outbox.purge()} مورد قدیمی پاک شد."
        return chat, "دستور ناشناخته. /help"

    def poll_once(self, timeout=25, limit=50):
        """یک دور getUpdates؛ offset ماندگار است تا پس از ری‌استارت دستور تکراری اجرا نشود."""
        offset = int(self.outbox.kv_get("panel_offset", "0"))
        params = {"timeout": timeout, "limit": limit}
        if offset:
            params["offset"] = offset
        updates = self.bale._post("getUpdates", params) or []
        handled = 0
        for u in updates:
            offset = max(offset, int(u.get("update_id", 0)) + 1)
            self.outbox.kv_set("panel_offset", offset)      # قبل از اجرا ثبت می‌شود: دستور خراب مدام تکرار نشود
            r = self.handle(u)
            if r:
                try:
                    self.bale.send_text(r[0], r[1])
                except Exception:                           # noqa: BLE001  پاسخ ناموفق نباید حلقه را بشکند
                    pass
                handled += 1
        return handled
