"""What kind of desktop session are we running in?

Must not import GTK at module level: :func:`preload` has to run before GTK
(and therefore libwayland-client) is loaded.
"""

from __future__ import annotations

import ctypes
import logging
import os
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

LAYER_SHELL_LIBS = ("libgtk4-layer-shell.so.0", "libgtk4-layer-shell.so")
_layer_shell_loaded = False


@dataclass(frozen=True)
class SessionInfo:
    desktop: str  # lower-cased XDG_CURRENT_DESKTOP, e.g. "ubuntu:gnome"
    wayland: bool
    x11: bool
    flatpak: bool

    @property
    def gnome(self) -> bool:
        return any(name in self.desktop for name in ("gnome", "ubuntu", "unity", "pop"))

    @property
    def kde(self) -> bool:
        return "kde" in self.desktop or "plasma" in self.desktop


@lru_cache(maxsize=1)
def info() -> SessionInfo:
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("XDG_SESSION_DESKTOP") or "").lower()
    session_type = os.environ.get("XDG_SESSION_TYPE", "").lower()
    gdk_backend = os.environ.get("GDK_BACKEND", "").lower()
    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) and not gdk_backend.startswith("x11")
    if session_type == "x11" and not os.environ.get("WAYLAND_DISPLAY"):
        wayland = False
    x11 = not wayland and bool(os.environ.get("DISPLAY"))
    return SessionInfo(
        desktop=desktop,
        wayland=wayland,
        x11=x11,
        flatpak=os.path.exists("/.flatpak-info"),
    )


def preload() -> None:
    """Load gtk4-layer-shell before GTK so it can hook libwayland-client.

    Only attempted on non-GNOME Wayland sessions (Mutter has no layer-shell);
    silently skipped when the library isn't installed.
    """
    global _layer_shell_loaded
    session = info()
    if not session.wayland or session.gnome or os.environ.get("BLUEGLANCE_NO_LAYER_SHELL"):
        return
    for name in LAYER_SHELL_LIBS:
        try:
            ctypes.CDLL(name)
        except OSError:
            continue
        _layer_shell_loaded = True
        log.debug("Loaded %s", name)
        return


def layer_shell_loaded() -> bool:
    return _layer_shell_loaded
