#!/usr/bin/env python3
"""Permanent schema-1.x nested launcher for pan_replay."""

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from setec.calibration import pan_replay as _mod
from setec.calibration.pan_replay import TASK_SURFACE

if __name__ == "__main__":
    sys.exit(_mod.main())
else:
    sys.modules[__name__] = _mod
