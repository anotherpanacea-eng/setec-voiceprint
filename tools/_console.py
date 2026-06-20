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
    """Force stdout/stderr to UTF-8 so Unicode glyphs are safe on any console.

    Call once at the top of a tool's ``main()`` (before any output, incl. argparse
    ``--help``). No-op where the streams are already UTF-8 (e.g. Linux/CI). Guarded
    so it can never itself raise: a plain ``io.StringIO`` test harness lacks
    ``reconfigure`` (``AttributeError``); a detached/closed stream raises
    ``ValueError``. No ``errors=`` — every glyph in use encodes losslessly in UTF-8,
    and a fallback would only mask a genuinely-unencodable future glyph.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
