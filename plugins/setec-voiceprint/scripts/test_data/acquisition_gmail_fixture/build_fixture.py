#!/usr/bin/env python3
"""Permanent historical launcher for the acquisition_gmail_fixture builder."""
import sys
from pathlib import Path
_SCRIPT_DIR = Path(__file__).resolve().parents[2]
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from setec.test_data.acquisition_gmail_fixture import build_fixture as _mod
if __name__ == "__main__":
    sys.exit(_mod.main())
else:
    sys.modules[__name__] = _mod
