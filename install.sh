#!/usr/bin/env bash
# Install BlueGlance from a source checkout or release tarball — any distro.
#
#   ./install.sh                 install for the current user (~/.local)
#   sudo ./install.sh --system   install for everyone (/usr/local)
#   ./install.sh --uninstall     remove a user install (add --system for /usr/local)
#
# Needs Python 3.10+, PyGObject, GTK 4.12+ and libadwaita 1.5+ from your distro.
set -euo pipefail

APP_ID="io.github.rafay_ah.BlueGlance"
UUID="blueglance@rafay-ah.github.io"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE=user
ACTION=install
for arg in "$@"; do
    case "$arg" in
        --system) MODE=system ;;
        --uninstall) ACTION=uninstall ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ "$MODE" = system ]; then
    PREFIX="${PREFIX:-/usr/local}"
    EXT_DIR="$PREFIX/share/gnome-shell/extensions"
    AUTOSTART_DIR="/etc/xdg/autostart"
else
    PREFIX="${PREFIX:-$HOME/.local}"
    EXT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions"
    AUTOSTART_DIR=""
fi
BIN="$PREFIX/bin/blueglance"
PKGDATA="$PREFIX/share/blueglance"
LIBEXEC="$PREFIX/libexec/blueglance"
SHARE="$PREFIX/share"

say() { printf '\033[1m%s\033[0m\n' "$*"; }

if [ "$ACTION" = uninstall ]; then
    rm -rf "${PKGDATA:?}" "${LIBEXEC:?}" "${EXT_DIR:?}/$UUID"
    rm -f "$BIN" "$SHARE/applications/$APP_ID.desktop" "$SHARE/metainfo/$APP_ID.metainfo.xml" \
          "$SHARE/icons/hicolor/scalable/apps/$APP_ID.svg" \
          "$SHARE/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg" \
          "$SHARE"/icons/hicolor/scalable/actions/blueglance-*-symbolic.svg \
          "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/$APP_ID.desktop"
    [ -n "$AUTOSTART_DIR" ] && rm -f "$AUTOSTART_DIR/$APP_ID.desktop"
    say "BlueGlance removed. (Settings are kept in ~/.config/blueglance.)"
    exit 0
fi

# ---- dependency check -------------------------------------------------------
PYTHON_CANDIDATES="${PYTHON:-} /usr/bin/python3 python3"
PYTHON=""
for candidate in $PYTHON_CANDIDATES; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" - <<'EOF' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12)
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5)
EOF
    then
        PYTHON="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    say "Missing dependencies: PyGObject, GTK 4.12+ and libadwaita 1.5+."
    echo "Install them with your package manager, for example:"
    echo "  Ubuntu/Debian: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 librsvg2-common"
    echo "  Fedora:        sudo dnf install python3-gobject gtk4 libadwaita"
    echo "  Arch:          sudo pacman -S python-gobject python-cairo gtk4 libadwaita"
    echo "  openSUSE:      sudo zypper install python3-gobject-Gdk typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1"
    exit 1
fi

# ---- files ------------------------------------------------------------------
say "Installing BlueGlance to $PREFIX"
rm -rf "$PKGDATA/blueglance"
mkdir -p "$PKGDATA" "$LIBEXEC" "$PREFIX/bin" "$SHARE/applications" "$SHARE/metainfo" \
         "$SHARE/icons/hicolor/scalable/apps" "$SHARE/icons/hicolor/symbolic/apps"
cp -r "$ROOT/src/blueglance" "$PKGDATA/"
find "$PKGDATA" -name __pycache__ -prune -exec rm -rf {} +

cat > "$BIN" <<EOF
#!$PYTHON
import os
import sys

sys.path.insert(1, "$PKGDATA")
os.environ.setdefault("BLUEGLANCE_LIBEXECDIR", "$LIBEXEC")

if __name__ == "__main__":
    from blueglance.main import main

    sys.exit(main())
EOF
chmod 755 "$BIN"
install -m 755 "$ROOT/data/blueglance-enable-bluez-battery" "$LIBEXEC/"
sed "s#^Exec=blueglance#Exec=$BIN#" "$ROOT/data/$APP_ID.desktop" > "$SHARE/applications/$APP_ID.desktop"
install -m 644 "$ROOT/data/$APP_ID.metainfo.xml" "$SHARE/metainfo/"
install -m 644 "$ROOT/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$SHARE/icons/hicolor/scalable/apps/"
install -m 644 "$ROOT/data/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg" "$SHARE/icons/hicolor/symbolic/apps/"
mkdir -p "$SHARE/icons/hicolor/scalable/actions"
install -m 644 "$ROOT"/src/blueglance/icons/hicolor/scalable/actions/blueglance-*.svg "$SHARE/icons/hicolor/scalable/actions/"
if [ -n "$AUTOSTART_DIR" ]; then
    mkdir -p "$AUTOSTART_DIR"
    sed "s#@BINDIR@#$PREFIX/bin#" "$ROOT/data/autostart.desktop.in" > "$AUTOSTART_DIR/$APP_ID.desktop"
fi

# GNOME Shell companion extension (pinned desktop widget + top bar menu)
mkdir -p "$EXT_DIR"
rm -rf "${EXT_DIR:?}/$UUID"
cp -r "$ROOT/gnome-extension/$UUID" "$EXT_DIR/"

if command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -q -t -f "$SHARE/icons/hicolor" 2>/dev/null || true
fi
if command -v update-desktop-database >/dev/null; then
    update-desktop-database -q "$SHARE/applications" 2>/dev/null || true
fi

say "Done!"
echo "Start it from your app menu, or run: $BIN"
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "(Tip: add $PREFIX/bin to your PATH.)" ;; esac
if [ "${XDG_CURRENT_DESKTOP:-}" != "${XDG_CURRENT_DESKTOP#*GNOME}" ]; then
    echo "On GNOME, log out and back in once so the desktop widget extension can load."
fi
