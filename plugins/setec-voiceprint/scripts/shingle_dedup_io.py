#!/usr/bin/env python3
"""Permanent legacy alias for the shingle_dedup_io library."""

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from setec.core import shingle_dedup_io as _mod

if __name__ != "__main__":
    sys.modules[__name__] = _mod
