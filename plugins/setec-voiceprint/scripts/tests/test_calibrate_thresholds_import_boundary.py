"""Observable import boundary and direct-alias compatibility for calibration."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
CALIBRATION = SCRIPTS / "calibration"
SCRIPT = CALIBRATION / "calibrate_thresholds.py"
HEAVY_FAMILIES = ("spacy", "sentence_transformers", "sklearn", "torch", "transformers")


def _child_probe(body: str) -> tuple[subprocess.CompletedProcess[str], set[str]]:
    prelude = """
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
before = set(sys.modules)
"""
    epilogue = """
delta = set(sys.modules) - before
print('IMPORT_DELTA=' + json.dumps(sorted(delta)), file=sys.stderr)
"""
    result = subprocess.run(
        [sys.executable, "-c", prelude + body + epilogue, str(CALIBRATION), str(SCRIPT)],
        cwd=SCRIPTS,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    marker = next(
        (line for line in result.stderr.splitlines() if line.startswith("IMPORT_DELTA=")),
        None,
    )
    assert marker is not None, result.stderr
    return result, set(json.loads(marker.partition("=")[2]))


def _introduced_heavy_families(delta: set[str]) -> set[str]:
    return {
        family for family in HEAVY_FAMILIES
        if any(name == family or name.startswith(family + ".") for name in delta)
    }


def test_plain_library_import_does_not_initialize_scoring_dependencies():
    result, delta = _child_probe("import calibrate_thresholds\n")
    assert result.returncode == 0, result.stderr
    assert _introduced_heavy_families(delta) == set()


def test_real_help_exits_before_scoring_dependencies():
    result, delta = _child_probe("""
import runpy
sys.argv = [sys.argv[2], '--help']
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except SystemExit as exc:
    assert exc.code == 0, exc.code
else:
    raise AssertionError('argparse help did not exit')
""")
    assert result.returncode == 0, result.stderr
    for flag in ("--manifest", "--signal", "--comparator-class", "--judge", "--generator"):
        assert flag in result.stdout
    assert _introduced_heavy_families(delta) == set()


def test_direct_and_from_import_aliases_are_genuine_and_patchable():
    if str(CALIBRATION) not in sys.path:
        sys.path.insert(0, str(CALIBRATION))
    import calibrate_thresholds as ct
    import validation_harness as vh
    import variance_audit as va
    from calibrate_thresholds import (
        COMPRESSION_HEURISTICS,
        DEFAULT_NEGATIVE_STATUSES,
        DEFAULT_POSITIVE_STATUSES,
        _entry_uses,
        collect_signal_records,
        load_manifest_entries,
        score_smoothing_entry,
    )

    aliases = {
        "DEFAULT_NEGATIVE_STATUSES": DEFAULT_NEGATIVE_STATUSES,
        "DEFAULT_POSITIVE_STATUSES": DEFAULT_POSITIVE_STATUSES,
        "_entry_uses": _entry_uses,
        "collect_signal_records": collect_signal_records,
        "load_manifest_entries": load_manifest_entries,
        "score_smoothing_entry": score_smoothing_entry,
    }
    for name, imported in aliases.items():
        assert imported is getattr(vh, name)
        assert getattr(ct, name) is imported
    assert COMPRESSION_HEURISTICS is ct.COMPRESSION_HEURISTICS
    assert COMPRESSION_HEURISTICS is va.COMPRESSION_HEURISTICS
    assert len(COMPRESSION_HEURISTICS) == len(va.COMPRESSION_HEURISTICS)
    assert list(COMPRESSION_HEURISTICS) == list(va.COMPRESSION_HEURISTICS)
    assert COMPRESSION_HEURISTICS.keys() == va.COMPRESSION_HEURISTICS.keys()
    assert COMPRESSION_HEURISTICS.items() == va.COMPRESSION_HEURISTICS.items()
    first = next(iter(COMPRESSION_HEURISTICS))
    assert COMPRESSION_HEURISTICS[first] is va.COMPRESSION_HEURISTICS[first]

    replacement = object()
    with mock.patch.object(ct, "score_smoothing_entry", replacement):
        assert ct.score_smoothing_entry is replacement
    assert ct.score_smoothing_entry is vh.score_smoothing_entry
    with pytest.raises(AttributeError):
        ct.this_calibration_attribute_does_not_exist
