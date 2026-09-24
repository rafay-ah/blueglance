#!/usr/bin/env bash
# Zip the GNOME Shell extension (installable with `gnome-extensions install`).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(realpath -m "${1:-$ROOT/dist}")"
UUID="blueglance@rafay-ah.github.io"
mkdir -p "$OUT"
rm -f "$OUT/$UUID.shell-extension.zip"
(cd "$ROOT/gnome-extension/$UUID" && zip -qr -X "$OUT/$UUID.shell-extension.zip" . -x '*.orig' -x 'schemas/gschemas.compiled')
echo "$OUT/$UUID.shell-extension.zip"
