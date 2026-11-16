# راه‌اندازی رله‌ی تحویل (هر میزبانی با Python)

هیچ توکن یا رمزی در سورس نیست؛ همه از متغیرهای محیطی می‌آید.

## ۱) نصب وابستگی‌ها (آفلاین)
روی ماشینی که اینترنت دارد:
```
scripts/make_wheelhouse.sh wheelhouse
# برای میزبان مقصد با نسخه/پلتفرم دیگر:
PYTHON_VERSION=3.11 PLATFORM=manylinux2014_x86_64 scripts/make_wheelhouse.sh wheelhouse
```
پوشه‌ی `wheelhouse` را به میزبان منتقل کن و آنجا (بدون اینترنت):
```
scripts/install_offline.sh wheelhouse .venv
```
چک‌سام فایل‌ها پیش از نصب بررسی می‌شود.

## ۲) تنظیمات
| متغیر | توضیح |
|---|---|
| `BALE_BOT_TOKEN` | توکن ربات (الزامی) |
| `BALE_DESTINATION` | کانال/چت مقصد (الزامی) |
| `BALE_ADMIN_IDS` | شناسه‌های **عددی** مدیران با ویرگول؛ فقط چت خصوصی خودشان با ربات پنل را باز می‌کند |
| `OUTBOX_PATH` | مسیر پایگاه SQLite (پیش‌فرض `data/outbox.sqlite`) |
| `RELAY_RATE` | پیام در ثانیه (پیش‌فرض ۱) |

## ۳) اجرا
```
.venv/bin/python run_relay.py          # دائمی
.venv/bin/python run_relay.py --once   # یک دور (cron/تست)
```
نمونه‌ی systemd (`/etc/systemd/system/balebot-relay.service`):
```
[Service]
WorkingDirectory=/opt/balebot
EnvironmentFile=/etc/balebot.env        # حاوی متغیرهای بالا؛ chmod 600
ExecStart=/opt/balebot/.venv/bin/python run_relay.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```
پس از کرش/ری‌استارت، پیام‌های نیمه‌کاره خودکار بازیابی می‌شوند (`lease`).

## ۴) وصل کردن کلکتور (هنوز اعمال نشده؛ منتظر تأیید)
در `poll.py` به‌جای `BaleClient(...)` فقط وقتی `OUTBOX_PATH` تنظیم است:
```python
from resilience.outbox import Outbox
from resilience.bridge import OutboxBale
if os.environ.get("OUTBOX_PATH"):
    bale = OutboxBale(Outbox(os.environ["OUTBOX_PATH"]), real=bale)
```
و هر دقیقه یک بار `bale.heartbeat()` تا رله بتواند «سکوت کلکتور» را تشخیص بدهد.

## ۵) دستورهای پنل (در چت خصوصی با ربات)
`/status` `/pause` `/resume` `/dead` `/retry` `/purge` `/help`

## درباره‌ی دریافت آپدیت‌ها در پنل (طبق مستندات رسمی)
- پنل از `getUpdates` (long polling) استفاده می‌کند؛ نیازی به آدرس عمومی یا گواهی ندارد.
- اگر روزی وب‌هوک خواستی: فقط **HTTPS** و فقط پورت‌های **۴۴۳ یا ۸۸**؛ با وب‌هوک فعال، `getUpdates` کار نمی‌کند (`deleteWebhook` لازم است).
- خطاهای API شامل `error_code` و گاهی `parameters.retry_after` است؛ رله خودکار از آن استفاده می‌کند.

## محدودیت‌ها
- ضمانت تحویل «حداقل یک‌بار» است (برای Bot API کلید idempotency مستند نشده؛ `request_id` فقط در «سفیر» و برای ارسال به شماره تلفن است).
- یک ربات فقط یک مصرف‌کننده‌ی `getUpdates` می‌تواند داشته باشد و با وب‌هوک فعال، `getUpdates` کار نمی‌کند.
- دستورهای پنل بیش از ۲۴ ساعت خاموشی از دست می‌روند (سقف نگهداری بله).

- سقف نرخ ارسال بله در مستندات در دسترس من نیست؛ `RELAY_RATE` را محتاطانه (۱ در ثانیه) نگه دار و با خطای ۴۲۹ (`retry_after`) خودکار کند می‌شود.
