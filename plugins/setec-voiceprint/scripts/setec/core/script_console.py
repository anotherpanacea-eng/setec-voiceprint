"""Best-effort UTF-8 console streams for plugin script entry points."""

from __future__ import annotations

import sys


def enable_utf8_stdio() -> None:
    """Configure current stdout/stderr as UTF-8 without changing error policies.

    Call explicitly at the start of a CLI main, before argparse can print help.
    Unsupported capture, detached, or closed streams are left untouched.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            errors = stream.errors  # type: ignore[union-attr]
            stream.reconfigure(encoding="utf-8", errors=errors)  # type: ignore[union-attr]
        except (AttributeError, OSError, TypeError, ValueError):
            pass
