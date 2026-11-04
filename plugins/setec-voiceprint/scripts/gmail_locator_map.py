#!/usr/bin/env python3
"""Permanent historical launcher for the Gmail metadata locator map."""
import sys
from pathlib import Path
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from setec.surfaces import gmail_locator_map as _mod
if __name__ == "__main__":
    sys.exit(_mod.main())
else:
    sys.modules[__name__] = _mod
