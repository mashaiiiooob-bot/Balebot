#!/usr/bin/env bash
# نصب کاملاً آفلاین از wheelhouse (هیچ تماسی با PyPI برقرار نمی‌شود: --no-index).
set -euo pipefail
WH="${1:-wheelhouse}"; VENV="${2:-.venv}"
[ -d "$WH" ] || { echo "پوشه‌ی $WH پیدا نشد؛ ابتدا scripts/make_wheelhouse.sh را (با اینترنت) اجرا کن." >&2; exit 1; }
( cd "$WH" && sha256sum -c --quiet SHA256SUMS )          # سالم بودن فایل‌ها
python3 -m venv "$VENV"
"$VENV/bin/pip" install --no-index --find-links "$WH" -r requirements.txt
echo "نصب آفلاین موفق: $("$VENV/bin/python" -c 'import telethon,requests;print("telethon",telethon.__version__,"| requests",requests.__version__)')"
