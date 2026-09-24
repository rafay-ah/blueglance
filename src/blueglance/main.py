"""Entry point used by the ``blueglance`` launcher and ``python -m blueglance``."""

from __future__ import annotations

import signal
import sys


def main(argv=None) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Graphene", "1.0")
    gi.require_version("Pango", "1.0")

    from . import session

    session.preload()

    from .application import run

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    return run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(main())
