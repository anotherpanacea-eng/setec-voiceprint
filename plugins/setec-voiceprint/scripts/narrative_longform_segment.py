#!/usr/bin/env python3
"""Compatibility launcher for setec.core.narrative_longform_segment."""

import sys
from pathlib import Path

# Detached runpy does not supply scripts/ on sys.path.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from setec.core import narrative_longform_segment as _mod

if __name__ != "__main__":
    sys.modules[__name__] = _mod
