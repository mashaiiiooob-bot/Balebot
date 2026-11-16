#!/usr/bin/env python3
"""رله‌ی تحویل: صف ماندگار → بله، پنل مدیریت بله، پایش سلامت. مستقل از میزبان (Python + دیسک کافی است).

متغیرهای محیطی (هیچ‌کدام در سورس نیست):
  BALE_BOT_TOKEN   توکن ربات (الزامی)         BALE_DESTINATION  مقصد (الزامی)
  BALE_ADMIN_IDS   شناسه‌های عددی مدیران، با ویرگول (برای پنل و هشدار)
  OUTBOX_PATH      مسیر SQLite (پیش‌فرض data/outbox.sqlite)
  RELAY_RATE       پیام در ثانیه (پیش‌فرض 1)
اجرا: python run_relay.py            (دائمی)      |      python run_relay.py --once   (یک دور؛ برای cron/تست)
"""
import logging
import os
import signal
import sys
import threading
import time

from resilience.deliver import Breaker, Deliverer
from resilience.health import Alerter, evaluate
from resilience.outbox import Outbox
from resilience.panel import Panel

log = logging.getLogger("relay")


class Relay:
    def __init__(self, outbox, bale, destination, admins, rate=1.0, clock=time.time, sleep=time.sleep):
        self.outbox, self.bale, self.admins, self.clock = outbox, bale, [a for a in admins if a], clock
        self.breaker = Breaker(clock=clock)
        self.deliverer = Deliverer(outbox, bale, destination, self.breaker, rate_per_s=rate, sleep=sleep, clock=clock)
        self.panel = Panel(outbox, bale, self.admins, self.breaker)
        self.alerter = Alerter(outbox, clock=clock)

    def check_health(self):
        """هشدارهای سررسیدشده را به مدیرها می‌فرستد (با cooldown)."""
        issues = evaluate(self.outbox, self.clock(), breaker_open=self.breaker.is_open)
        sent = 0
        for code, msg in self.alerter.due(issues):
            for admin in self.admins:
                try:
                    self.bale.send_text(admin, "⚠️ " + msg)
                    sent += 1
                except Exception:       # noqa: BLE001  اگر بله قطع است، هشدار هم نمی‌رسد؛ در دور بعد دوباره تلاش می‌شود
                    break
            else:
                self.alerter.mark(code)
        return sent

    def step(self):
        res = self.deliverer.run_once()
        return res


def main(argv=None):
    argv = argv or sys.argv[1:]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    from bale_client import BaleClient
    token, dest = os.environ.get("BALE_BOT_TOKEN", "").strip(), os.environ.get("BALE_DESTINATION", "").strip()
    if not token or not dest:
        log.error("BALE_BOT_TOKEN و BALE_DESTINATION الزامی هستند.")
        return 2
    admins = [x.strip() for x in os.environ.get("BALE_ADMIN_IDS", "").split(",") if x.strip()]
    outbox = Outbox(os.environ.get("OUTBOX_PATH", "data/outbox.sqlite"))
    relay = Relay(outbox, BaleClient(token), dest, admins, rate=float(os.environ.get("RELAY_RATE", "1")))
    if "--once" in argv:
        log.info("once: %s", relay.step())
        relay.check_health()
        if admins:
            relay.panel.poll_once(timeout=0)
        return 0

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    def panel_loop():
        backoff = 1
        while not stop.is_set():
            try:
                relay.panel.poll_once(timeout=25)
                backoff = 1
            except Exception as exc:    # noqa: BLE001
                log.warning("panel poll failed: %s", type(exc).__name__)
                stop.wait(min(60, backoff)); backoff *= 2

    if admins:
        threading.Thread(target=panel_loop, daemon=True).start()
    last_health = last_purge = 0.0
    log.info("relay started; queue=%s", outbox.stats())
    while not stop.is_set():                    # پس از ری‌استارت، موارد inflight بعد از lease خودکار بازیابی می‌شوند
        relay.step()
        now = time.time()
        if now - last_health > 60:
            relay.check_health(); last_health = now
        if now - last_purge > 3600:
            outbox.purge(); last_purge = now
        stop.wait(2)
    log.info("relay stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
