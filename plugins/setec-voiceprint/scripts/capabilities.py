#!/usr/bin/env python3
"""Compatibility launcher for setec.contract.capabilities."""

import sys

from setec.contract import capabilities as _mod

if __name__ == "__main__":
    sys.exit(_mod.main())
else:
    sys.modules[__name__] = _mod
