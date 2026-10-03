#!/usr/bin/env python3
"""Permanent legacy alias for the passage_remediation_projection library."""

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from setec.core import passage_remediation_projection as _mod

if __name__ != "__main__":
    sys.modules[__name__] = _mod
