#!/usr/bin/env bash
# Build an architecture-independent .deb (Ubuntu 24.04+, Debian 13+).
#   scripts/build-deb.sh [output-dir]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(realpath -m "${1:-$ROOT/dist}")"
VERSION="$(sed -n "s/^  version: '\(.*\)',$/\1/p" "$ROOT/meson.build")"
[ -n "$VERSION" ] || { echo "cannot read version from meson.build" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
PKG="$WORK/pkg"

LOG="$WORK/build.log"
run() {
    "$@" >>"$LOG" 2>&1 || { cat "$LOG" >&2; echo "failed: $*" >&2; exit 1; }
}
run meson setup "$WORK/build" "$ROOT" --prefix=/usr --sysconfdir=/etc --buildtype=plain \
    -Dpython=/usr/bin/python3 -Dsystem_autostart=true -Dgnome_extension=true
run meson compile -C "$WORK/build"
DESTDIR="$PKG" run meson install -C "$WORK/build" --no-rebuild
# The launcher must use the distro's interpreter, where PyGObject lives.
SHEBANG="$(head -n1 "$PKG/usr/bin/blueglance")"
case "$SHEBANG" in
    '#!/usr/bin/python3'*) ;;
    *) echo "unexpected launcher shebang: $SHEBANG" >&2; exit 1 ;;
esac
sed -i '1s|.*|#!/usr/bin/python3|' "$PKG/usr/bin/blueglance"
find "$PKG" -name __pycache__ -prune -exec rm -rf {} +

mkdir -p "$PKG/DEBIAN" "$PKG/usr/share/doc/blueglance"
cp "$ROOT/README.md" "$PKG/usr/share/doc/blueglance/"
cat > "$PKG/usr/share/doc/blueglance/copyright" <<COPY
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: BlueGlance
Source: https://github.com/rafay-ah/blueglance

Files: *
Copyright: 2026 Abdul Rafay and contributors
License: GPL-3.0-or-later
 On Debian systems, the full text of the GNU General Public License
 version 3 can be found in /usr/share/common-licenses/GPL-3.
COPY

INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<CONTROL
Package: blueglance
Version: $VERSION
Architecture: all
Maintainer: Abdul Rafay <rafay-ah@users.noreply.github.com>
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.10), python3-gi (>= 3.42), python3-gi-cairo, python3-cairo, gir1.2-glib-2.0, gir1.2-gtk-4.0 (>= 4.12), gir1.2-adw-1 (>= 1.5), gir1.2-graphene-1.0, gir1.2-pango-1.0, librsvg2-common
Recommends: upower, bluez, pkexec | policykit-1
Suggests: gir1.2-gtk4layershell-1.0, gnome-shell-extension-appindicator
Section: utils
Priority: optional
Homepage: https://github.com/rafay-ah/blueglance
Description: battery levels of your Bluetooth devices at a glance
 BlueGlance shows the battery level of connected Bluetooth mice, keyboards,
 headphones, AirPods (left, right and case) and game controllers in a
 macOS-style desktop widget, in the top bar or system tray and in a
 GTK 4 app, and warns you before a battery runs out.
 .
 Includes the companion GNOME Shell extension that pins the widget to the
 GNOME desktop.
CONTROL
( cd "$PKG" && find etc -type f | sed 's#^#/#' ) > "$PKG/DEBIAN/conffiles"

mkdir -p "$OUT"
DEB="$OUT/blueglance_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$DEB" >/dev/null
echo "$DEB"
