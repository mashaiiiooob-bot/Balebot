#!/usr/bin/env bash
# دریافت همه‌ی وابستگی‌ها (و وابستگی‌های آن‌ها) در یک پوشه‌ی محلی تا نصب بدون اینترنت ممکن شود.
# استفاده:  scripts/make_wheelhouse.sh [پوشه]            (برای همین سیستم/نسخه‌ی Python)
#           PYTHON_VERSION=3.11 PLATFORM=manylinux2014_x86_64 scripts/make_wheelhouse.sh   (برای میزبان مقصد)
set -euo pipefail
OUT="${1:-wheelhouse}"
mkdir -p "$OUT"
ARGS=(-r requirements.txt -d "$OUT")
[ -n "${PYTHON_VERSION:-}" ] && ARGS+=(--python-version "$PYTHON_VERSION" --only-binary=:all:)
[ -n "${PLATFORM:-}" ] && ARGS+=(--platform "$PLATFORM" --only-binary=:all:)
python3 -m pip download "${ARGS[@]}"
# ابزار ساخت برای بسته‌های بدون wheel (مثلاً pyaes) تا نصب آفلاین گیر نکند
python3 -m pip download setuptools wheel -d "$OUT" >/dev/null
( cd "$OUT" && sha256sum -- * | grep -v SHA256SUMS > SHA256SUMS.tmp && mv SHA256SUMS.tmp SHA256SUMS )
echo "wheelhouse: $(ls "$OUT" | grep -vc SHA256SUMS) فایل در $OUT"
