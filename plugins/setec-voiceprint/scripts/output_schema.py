#!/usr/bin/env python3
"""Compatibility launcher for setec.contract.output_schema."""

import sys

from setec.contract import output_schema as _mod

if __name__ != "__main__":
    sys.modules[__name__] = _mod
