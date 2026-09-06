"""Shared console helper for the ``tools/`` CLIs.

The doc/capability gates print Unicode status glyphs (``✔``, ``≥``, ``→`` …) to
stdout/stderr. Under a non-UTF-8 default console — notably Windows ``cp1252`` when
``PYTHONUTF8`` is unset — ``print()`` raises ``UnicodeEncodeError`` *after* the check
has already run, turning a success into a nonzero exit + traceback. CI runs on Linux
(UTF-8), so this never fires there; it only bites a maintainer running the gate
locally on Windows ("run the real CI command first").
"""

from __future__ import annotations

import sys


def enable_utf8_stdio() -> None:
    """Best-effort UTF-8 stdout/stderr for console-safe status glyphs.

    Call once at the top of a tool's ``main()`` (before any output, incl. argparse
    ``--help``). Preserve each stream's existing error policy — especially
    stderr's normal ``backslashreplace`` safety net — while changing only the
    encoding. Unsupported capture streams and detached/closed stdio are left
    unchanged.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            errors = stream.errors  # type: ignore[union-attr]
            stream.reconfigure(  # type: ignore[union-attr]
                encoding="utf-8",
                errors=errors,
            )
        except (AttributeError, OSError, TypeError, ValueError):
            pass
