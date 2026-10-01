#!/usr/bin/env python3
"""Compatibility launcher for setec.contract.claim_license."""

import sys

from setec.contract import claim_license as _mod

if __name__ != "__main__":
    sys.modules[__name__] = _mod
