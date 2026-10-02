#!/usr/bin/env python3
"""Compatibility launcher for setec.core.stylometry_distance."""

import sys
from pathlib import Path

# runpy does not add the launcher's directory as direct execution does.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from setec.core import stylometry_distance as _mod

if __name__ != "__main__":
    sys.modules[__name__] = _mod
