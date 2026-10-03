#!/usr/bin/env python3
"""Compatibility launcher for setec.core.cross_doc_consistency_schema."""

import sys
from pathlib import Path

# Detached runpy does not supply scripts/ on sys.path.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from setec.core import cross_doc_consistency_schema as _mod

if __name__ != "__main__":
    sys.modules[__name__] = _mod
