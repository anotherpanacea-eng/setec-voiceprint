#!/usr/bin/env python3
"""Regression: the tools/ CLIs print Unicode glyphs safely on a non-UTF-8 console.

The doc/capability gates print status glyphs (``✔`` / ``≥`` / ``→`` / ``⇒``). Under a
cp1252 default console (Windows, ``PYTHONUTF8`` unset) ``print()`` raised
``UnicodeEncodeError`` *after* the check ran — turning a pass into a nonzero exit +
traceback. CI is Linux/UTF-8 so it never saw the bug; these tests reproduce the
Windows condition on any platform with a cp1252-backed ``TextIOWrapper`` and pin the
whole class fixed by ``tools/_console.enable_utf8_stdio()``.
"""

from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from _console import enable_utf8_stdio  # type: ignore  # noqa: E402


def _cp1252_stream(*, errors: str = "strict") -> tuple[io.BytesIO, io.TextIOWrapper]:
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(
        raw,
        encoding="cp1252",
        errors=errors,
        newline="",
    )


def test_enable_utf8_stdio_reencodes_a_cp1252_stream(monkeypatch):
    raw_out, tw_out = _cp1252_stream(errors="surrogateescape")
    raw_err, tw_err = _cp1252_stream(errors="backslashreplace")
    monkeypatch.setattr(sys, "stdout", tw_out)
    monkeypatch.setattr(sys, "stderr", tw_err)
    enable_utf8_stdio()
    print("Docs are fresh. ✔")  # U+2714 is not encodable in cp1252 pre-reconfigure
    tw_out.flush()
    assert "✔".encode("utf-8") in raw_out.getvalue()
    assert tw_out.errors == "surrogateescape"
    assert tw_err.errors == "backslashreplace"
    tw_err.write("\udcff")
    tw_err.flush()
    assert b"\\udcff" in raw_err.getvalue()


def test_enable_utf8_stdio_is_safe_without_reconfigure(monkeypatch):
    # A plain StringIO has no .reconfigure, as with some test capture streams.
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    enable_utf8_stdio()


def test_enable_utf8_stdio_is_safe_when_reconfigure_is_unsupported(monkeypatch):
    class UnsupportedReconfigureStream(io.StringIO):
        def reconfigure(self, **_kwargs):
            raise OSError("synthetic unsupported reconfigure")

    monkeypatch.setattr(sys, "stdout", UnsupportedReconfigureStream())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    enable_utf8_stdio()


@pytest.mark.parametrize(
    "modname, argv",
    [
        ("gen_calibration_readiness", ["--stdout"]),  # ≥ block: prints regardless of state
        ("check_docs_freshness", []),                 # ✔ on the fresh path
        ("check_capabilities_drift", []),             # ✔ on the consistent path
        # assemble_changelog --stdout prints data-borne glyphs (fragments' ✔/≥/→),
        # not a source literal — the case neither audit predicate could catch.
        ("assemble_changelog", ["--stdout", "--version", "0.0.0", "--date", "2026-06-19"]),
    ],
)
def test_tool_human_output_survives_cp1252(monkeypatch, modname, argv):
    pytest.importorskip("yaml")
    mod = importlib.import_module(modname)
    _, tw_o = _cp1252_stream()
    _, tw_e = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", tw_o)
    monkeypatch.setattr(sys, "stderr", tw_e)
    try:
        mod.main(argv)  # the regression: this raised UnicodeEncodeError before the fix
    except SystemExit:
        pass
    tw_o.flush()
    tw_e.flush()


def test_argparse_help_with_glyph_docstring_survives_cp1252(monkeypatch):
    # gen_calibration_readiness's module docstring carries → / ⇒; --help prints it.
    pytest.importorskip("yaml")
    mod = importlib.import_module("gen_calibration_readiness")
    _, tw = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", tw)
    # A pre-fix run raises UnicodeEncodeError here instead of SystemExit, failing this.
    with pytest.raises(SystemExit):
        mod.main(["--help"])
