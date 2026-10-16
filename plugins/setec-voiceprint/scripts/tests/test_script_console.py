"""Windows console encoding behavior for four existing plugin CLIs."""

from __future__ import annotations

import io
import os
from pathlib import Path
import sys


from setec.core.script_console import enable_utf8_stdio


SCRIPTS = Path(__file__).resolve().parents[1]
HELP_CASES = (
    ("calibration/pan_replay.py", "Δ"),
    ("adversarial_robustness_card.py", "Δ"),
    ("bigram_diff.py", "α"),
    ("cosine_explanation.py", "→"),
)


def cp1252_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    env["PYTHONUTF8"] = "0"
    return env


def test_text_wrappers_keep_stream_identity_and_each_error_policy(monkeypatch):
    out_bytes, err_bytes = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(out_bytes, encoding="cp1252", errors="strict")
    err = io.TextIOWrapper(err_bytes, encoding="cp1252", errors="backslashreplace")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", out)
        patch.setattr(sys, "stderr", err)
        enable_utf8_stdio()
        enable_utf8_stdio()
        assert sys.stdout is out and sys.stderr is err
        assert (out.encoding, out.errors) == ("utf-8", "strict")
        assert (err.encoding, err.errors) == ("utf-8", "backslashreplace")
        out.write("Δ")
        err.write("→")
        out.flush()
        err.flush()
    assert out_bytes.getvalue() == "Δ".encode("utf-8")
    assert err_bytes.getvalue() == "→".encode("utf-8")
    out.close()
    err.close()


def test_unsupported_none_and_closed_streams_are_best_effort(monkeypatch):
    class Unsupported:
        errors = "strict"

        def reconfigure(self, **_kwargs):
            raise OSError("unsupported")

    closed = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    closed.close()
    good = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="replace")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", None)
        patch.setattr(sys, "stderr", good)
        enable_utf8_stdio()
        assert good.encoding == "utf-8"
        patch.setattr(sys, "stdout", Unsupported())
        patch.setattr(sys, "stderr", closed)
        enable_utf8_stdio()
    good.close()


