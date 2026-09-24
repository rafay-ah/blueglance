"""Minimal EWMH helpers (via ctypes/libX11) for the X11 desktop widget.

GTK 4 dropped keep-below, sticky and move APIs, so the widget talks to the
window manager directly: ``_NET_WM_STATE`` (below, sticky, skip taskbar/pager),
``_NET_WM_DESKTOP`` (all workspaces) and plain ``XMoveWindow``. We open our own
Xlib connection; that's allowed for sending client messages about any window.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger(__name__)

CLIENT_MESSAGE = 33
CONFIGURE_NOTIFY = 22
SUBSTRUCTURE_NOTIFY_MASK = 1 << 19
SUBSTRUCTURE_REDIRECT_MASK = 1 << 20
STRUCTURE_NOTIFY_MASK = 1 << 17
PROP_MODE_REPLACE = 0
XA_ATOM = 4
XA_CARDINAL = 6

_NET_WM_STATE_REMOVE = 0
_NET_WM_STATE_ADD = 1
SOURCE_APPLICATION = 1


class _ClientMessageData(ctypes.Union):
    _fields_ = [("b", ctypes.c_char * 20), ("s", ctypes.c_short * 10), ("l", ctypes.c_long * 5)]


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", _ClientMessageData),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("type", ctypes.c_int), ("xclient", _XClientMessageEvent), ("pad", ctypes.c_long * 24)]


_lib = None


def _xlib():
    global _lib
    if _lib is None:
        name = ctypes.util.find_library("X11") or "libX11.so.6"
        lib = ctypes.CDLL(name)
        lib.XOpenDisplay.restype = ctypes.c_void_p
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        lib.XInternAtom.restype = ctypes.c_ulong
        lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.XDefaultRootWindow.restype = ctypes.c_ulong
        lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        lib.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long,
                                   ctypes.POINTER(_XEvent)]
        lib.XFlush.argtypes = [ctypes.c_void_p]
        lib.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
        lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                        ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                              ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                              ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        lib.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
        lib.XConnectionNumber.argtypes = [ctypes.c_void_p]
        lib.XPending.argtypes = [ctypes.c_void_p]
        lib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XEvent)]
        _lib = lib
    return _lib


class X11Window:
    """Pins one X11 window to the desktop layer."""

    def __init__(self, xid: int):
        self.xid = xid
        lib = _xlib()
        self._dpy = lib.XOpenDisplay(None)
        if not self._dpy:
            raise OSError("cannot open X display")
        self._root = lib.XDefaultRootWindow(self._dpy)

    def close(self) -> None:
        if self._dpy:
            _xlib().XCloseDisplay(self._dpy)
            self._dpy = None

    def atom(self, name: str) -> int:
        return _xlib().XInternAtom(self._dpy, name.encode(), 0)

    def connection_fd(self) -> int:
        return _xlib().XConnectionNumber(self._dpy)

    # -- before map --------------------------------------------------------
    def set_initial_state(self) -> None:
        """Set properties the WM reads when the window is mapped."""
        lib = _xlib()
        states = (ctypes.c_ulong * 4)(
            self.atom("_NET_WM_STATE_BELOW"),
            self.atom("_NET_WM_STATE_STICKY"),
            self.atom("_NET_WM_STATE_SKIP_TASKBAR"),
            self.atom("_NET_WM_STATE_SKIP_PAGER"),
        )
        lib.XChangeProperty(self._dpy, self.xid, self.atom("_NET_WM_STATE"), XA_ATOM, 32,
                            PROP_MODE_REPLACE, ctypes.cast(states, ctypes.c_void_p), 4)
        desktop = (ctypes.c_ulong * 1)(0xFFFFFFFF)
        lib.XChangeProperty(self._dpy, self.xid, self.atom("_NET_WM_DESKTOP"), XA_CARDINAL, 32,
                            PROP_MODE_REPLACE, ctypes.cast(desktop, ctypes.c_void_p), 1)
        wtype = (ctypes.c_ulong * 1)(self.atom("_NET_WM_WINDOW_TYPE_UTILITY"))
        lib.XChangeProperty(self._dpy, self.xid, self.atom("_NET_WM_WINDOW_TYPE"), XA_ATOM, 32,
                            PROP_MODE_REPLACE, ctypes.cast(wtype, ctypes.c_void_p), 1)
        lib.XFlush(self._dpy)

    # -- after map ---------------------------------------------------------
    def _client_message(self, message_type: str, *data: int) -> None:
        lib = _xlib()
        event = _XEvent()
        event.xclient.type = CLIENT_MESSAGE
        event.xclient.send_event = 1
        event.xclient.display = self._dpy
        event.xclient.window = self.xid
        event.xclient.message_type = self.atom(message_type)
        event.xclient.format = 32
        for index, value in enumerate(data[:5]):
            event.xclient.data.l[index] = value
        lib.XSendEvent(self._dpy, self._root, 0, SUBSTRUCTURE_REDIRECT_MASK | SUBSTRUCTURE_NOTIFY_MASK,
                       ctypes.byref(event))
        lib.XFlush(self._dpy)

    def pin(self) -> None:
        """(Re)apply below/sticky/skip-taskbar through the WM, for already mapped windows."""
        add = _NET_WM_STATE_ADD
        self._client_message("_NET_WM_STATE", add, self.atom("_NET_WM_STATE_BELOW"),
                             self.atom("_NET_WM_STATE_STICKY"), SOURCE_APPLICATION)
        self._client_message("_NET_WM_STATE", add, self.atom("_NET_WM_STATE_SKIP_TASKBAR"),
                             self.atom("_NET_WM_STATE_SKIP_PAGER"), SOURCE_APPLICATION)
        self._client_message("_NET_WM_DESKTOP", 0xFFFFFFFF, SOURCE_APPLICATION)

    def move(self, x: int, y: int) -> None:
        lib = _xlib()
        lib.XMoveWindow(self._dpy, self.xid, int(x), int(y))
        lib.XFlush(self._dpy)

    def position(self) -> tuple[int, int] | None:
        lib = _xlib()
        x, y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        ok = lib.XTranslateCoordinates(self._dpy, self.xid, self._root, 0, 0,
                                       ctypes.byref(x), ctypes.byref(y), ctypes.byref(child))
        return (x.value, y.value) if ok else None

    def watch_configure(self) -> None:
        _xlib().XSelectInput(self._dpy, self.xid, STRUCTURE_NOTIFY_MASK)
        _xlib().XFlush(self._dpy)

    def drain_configure_events(self) -> bool:
        """Consume pending events; True if the window moved/resized."""
        lib = _xlib()
        moved = False
        event = _XEvent()
        while lib.XPending(self._dpy):
            lib.XNextEvent(self._dpy, ctypes.byref(event))
            if event.type == CONFIGURE_NOTIFY:
                moved = True
        return moved
