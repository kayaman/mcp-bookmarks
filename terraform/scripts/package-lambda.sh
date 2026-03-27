#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PKG="$ROOT/lambda/.lambda_pkg"
OUT="$ROOT/terraform/.build/lambda_processor.zip"
rm -rf "$PKG" "$(dirname "$OUT")"
mkdir -p "$PKG" "$(dirname "$OUT")"
python3 -m pip install -q -r "$ROOT/lambda/requirements.txt" -t "$PKG"
cp "$ROOT/lambda/handler.py" "$PKG"
cd "$PKG"
zip -qr "$OUT" .
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
