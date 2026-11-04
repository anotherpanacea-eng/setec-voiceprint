#!/usr/bin/env python3
"""Permanent legacy alias for the diveye_signals library."""

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from setec.core import diveye_signals as _mod
from setec.core.diveye_signals import TASK_SURFACE

if __name__ != "__main__":
    sys.modules[__name__] = _mod
