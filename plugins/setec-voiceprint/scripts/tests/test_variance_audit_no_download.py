"""Regression: importing variance_audit must not fetch NLTK data.

It used to call ``nltk.download("punkt")`` at import whenever the data was
missing, so every ``stylometry_core`` import on a fresh host reached the
network. The import runs in a fresh interpreter because the bug lived at
module import time.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("nltk")

SCRIPTS = Path(__file__).resolve().parents[1]

PROBE = """
import sys
import nltk
import nltk.downloader

def refuse(*args, **kwargs):
    raise SystemExit("network download attempted: %r" % (args,))

nltk.download = nltk.downloader.download = refuse
nltk.data.path[:] = []  # no Punkt data visible
sys.path.insert(0, sys.argv[1])
import variance_audit
print(variance_audit.split_sentences("Dr. Smith left. He slept."))
"""


def test_import_without_punkt_data_downloads_nothing_and_uses_regex():
    proc = subprocess.run(
        [sys.executable, "-c", PROBE, str(SCRIPTS)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "['Dr.', 'Smith left.', 'He slept.']"
