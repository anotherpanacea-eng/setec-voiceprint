"""Permanent compatibility alias for setec.core.passage_tokenizer_v1."""

import sys
from pathlib import Path

# runpy does not add the launcher's directory as direct execution does.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from setec.core import passage_tokenizer_v1 as _mod

if __name__ != "__main__":
    sys.modules[__name__] = _mod
