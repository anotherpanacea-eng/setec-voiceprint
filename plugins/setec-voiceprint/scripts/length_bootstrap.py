#!/usr/bin/env python3
"""Permanent schema-1.x launcher for length_bootstrap."""

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from setec.surfaces import length_bootstrap as _mod
from setec.surfaces.length_bootstrap import TASK_SURFACE

if __name__ != "__main__":
    sys.modules[__name__] = _mod
