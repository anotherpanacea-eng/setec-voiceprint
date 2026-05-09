#!/usr/bin/env python3
"""Regression tests for the calibration provenance ledger.

Two layers:

1. **Corpus-independent (always run):** ledger parseability, slug
   format, referential integrity between
   `COMPRESSION_HEURISTICS[*].provenance` and the entries in
   `scripts/calibration/thresholds_calibrated.json`. Catches drift
   between the registry's encoded thresholds and the JSON ledger
   regardless of whether the private corpus is available.

2. **Corpus-dependent (skip when absent):** if
   `ai-prose-baselines-private/editlens/` exists, re-derive each
   calibrated threshold via `calibrate_thresholds.derive_threshold`
   and verify the result matches the encoded `value` in
   `COMPRESSION_HEURISTICS` to within a tolerance.

CI environments without the private corpus skip the second layer
silently. The maintainer's local environment runs both. Tolerance:
1e-3 for v1 (thresholds reported to 3 decimal places).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

from variance_audit import COMPRESSION_HEURISTICS  # type: ignore


LEDGER_PATH = REPO_ROOT / "scripts" / "calibration" / "thresholds_calibrated.json"
PROVENANCE_MD = REPO_ROOT / "scripts" / "calibration" / "PROVENANCE.md"
PRIVATE_DIR = REPO_ROOT / "ai-prose-baselines-private" / "editlens"

# Slug format: alphanumerics, underscores, dots, dashes. The
# convention is <corpus>_<signal>_fpr<target>_<iso-date>; the regex
# is permissive enough to accept variations but strict enough to
# catch malformed slugs.
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")

CALIBRATION_TOLERANCE = 1e-3


def test_ledger_file_exists_and_parses() -> None:
    """The committed ledger must be a JSON list. v1 ships an empty
    list; later commits append entries."""
    assert LEDGER_PATH.exists(), f"missing ledger at {LEDGER_PATH}"
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, list), (
        f"ledger root must be a list, got {type(data).__name__}"
    )


def test_provenance_md_exists() -> None:
    """The human-readable companion to the JSON ledger."""
    assert PROVENANCE_MD.exists(), f"missing {PROVENANCE_MD}"
    text = PROVENANCE_MD.read_text(encoding="utf-8")
    assert "SETEC threshold calibration provenance" in text


def test_every_ledger_slug_is_well_formed() -> None:
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    for entry in data:
        slug = entry.get("slug")
        assert slug, f"entry missing slug: {entry!r}"
        assert SLUG_RE.match(slug), (
            f"malformed slug {slug!r}; expected alphanumerics + "
            f"underscores/dots/dashes"
        )


def test_every_ledger_entry_has_required_fields() -> None:
    """Catches schema drift in the provenance entry shape."""
    required = {
        "slug", "signal", "signal_path", "direction", "derived_value",
        "corpus", "calibration", "setec_commit", "derivation_date",
    }
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    for entry in data:
        missing = required - set(entry)
        assert not missing, (
            f"entry {entry.get('slug')} missing fields: {missing}"
        )
        cal = entry["calibration"]
        cal_required = {
            "method", "split_role", "fpr_target", "fpr_resolution",
            "n_pos", "n_neg", "empirical_fpr", "empirical_tpr",
        }
        cal_missing = cal_required - set(cal)
        assert not cal_missing, (
            f"entry {entry['slug']} calibration missing: {cal_missing}"
        )


def test_referential_integrity_registry_to_ledger() -> None:
    """Every COMPRESSION_HEURISTICS entry with a non-None provenance
    slug must have a matching entry in the ledger. This is the load-
    bearing integrity check: it catches stale slugs in the registry
    that point at calibrations someone deleted from the ledger."""
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    ledger_slugs = {e["slug"] for e in data}
    for signal_key, spec in COMPRESSION_HEURISTICS.items():
        if spec.provenance is None:
            continue
        assert spec.provenance in ledger_slugs, (
            f"COMPRESSION_HEURISTICS[{signal_key!r}].provenance = "
            f"{spec.provenance!r} but no matching slug in "
            f"{LEDGER_PATH.relative_to(REPO_ROOT)}. Either the slug "
            f"is stale or the ledger entry was removed."
        )


def test_referential_integrity_ledger_to_registry_signal_path() -> None:
    """Every ledger entry's signal_path must match the
    COMPRESSION_HEURISTICS entry's signal_path. Catches drift after
    code refactors that moved a signal under a different audit-output
    key without updating calibration entries."""
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    for entry in data:
        signal_key = entry["signal"]
        if signal_key not in COMPRESSION_HEURISTICS:
            # Unknown signal key — could be a calibration for a signal
            # that's since been removed. Not necessarily a failure;
            # warn only.
            continue
        spec = COMPRESSION_HEURISTICS[signal_key]
        assert entry["signal_path"] == spec.signal_path, (
            f"entry {entry['slug']} has signal_path "
            f"{entry['signal_path']!r}, but registry has "
            f"{spec.signal_path!r}. Update the ledger or the "
            f"registry."
        )


def test_referential_integrity_ledger_to_registry_direction() -> None:
    """Direction-mismatch is the bug that produces a useless
    threshold. The ledger and the registry must agree."""
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    for entry in data:
        signal_key = entry["signal"]
        if signal_key not in COMPRESSION_HEURISTICS:
            continue
        spec = COMPRESSION_HEURISTICS[signal_key]
        assert entry["direction"] == spec.direction, (
            f"entry {entry['slug']} has direction "
            f"{entry['direction']!r}, but registry has "
            f"{spec.direction!r}. Inverted direction produces useless "
            f"thresholds; investigate."
        )


def test_calibrated_thresholds_match_ledger_values() -> None:
    """Every calibrated COMPRESSION_HEURISTICS entry's `value` must
    equal the matching ledger entry's `derived_value` to floating-
    point exactness. This catches a maintainer editing the registry
    threshold without re-running calibration (a calibrated value is
    only valid for the empirical metrics in the ledger)."""
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    by_slug = {e["slug"]: e for e in data}
    for signal_key, spec in COMPRESSION_HEURISTICS.items():
        if spec.provenance is None:
            continue
        if spec.provenance not in by_slug:
            continue  # caught by the referential-integrity test above
        derived = by_slug[spec.provenance]["derived_value"]
        assert spec.value == derived, (
            f"COMPRESSION_HEURISTICS[{signal_key!r}].value = "
            f"{spec.value} but ledger entry "
            f"{spec.provenance!r}.derived_value = {derived}. The "
            f"registered value must match the calibration that "
            f"justified it; rerun calibration if you want a new value."
        )


def test_calibration_provenance_recoverable_when_corpus_present() -> None:
    """If the private EditLens corpus is available locally, re-derive
    each calibrated threshold via calibrate_thresholds.derive_threshold
    and assert the result matches the encoded value within tolerance.
    Catches silent threshold drift if the harness math changes
    underneath the calibration."""
    if not PRIVATE_DIR.exists():
        if pytest is not None:
            pytest.skip("Private EditLens corpus not available")
        return
    data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    if not data:
        if pytest is not None:
            pytest.skip("Ledger is empty; nothing to re-derive")
        return
    # Lazy import — avoids loading sklearn / pyarrow when no entries
    # exist to verify.
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "calibration"))
    import argparse as _argparse  # noqa: F401  (used by derive_threshold's args.Namespace)
    from calibrate_thresholds import derive_threshold  # type: ignore

    for entry in data:
        manifest_path = entry["corpus"].get("manifest_path")
        if not manifest_path or not Path(manifest_path).exists():
            if pytest is not None:
                pytest.skip(
                    f"Manifest for {entry['slug']} not found at "
                    f"{manifest_path}; cannot re-derive."
                )
            return
        # Re-run the calibration sweep with the same parameters.
        args = type("Args", (), {})()
        args.manifest = manifest_path
        args.use = entry["corpus"].get("use", "validation")
        args.signal = entry["signal"]
        args.fpr_target = entry["calibration"]["fpr_target"]
        args.bootstrap_resamples = entry["calibration"].get(
            "bootstrap_resamples", 2000
        )
        args.bootstrap_confidence = 0.95
        args.bootstrap_seed = entry["calibration"].get("bootstrap_seed", 42)
        args.tier2 = True
        args.tier3 = True
        args.slug = entry["slug"]
        args.notes = None

        rederived = derive_threshold(args)
        assert (
            abs(rederived["derived_value"] - entry["derived_value"])
            < CALIBRATION_TOLERANCE
        ), (
            f"threshold drift on {entry['slug']}: encoded "
            f"{entry['derived_value']}, re-derived "
            f"{rederived['derived_value']}. Either the harness math "
            f"changed underneath the calibration, or the corpus "
            f"file at {manifest_path} differs from the original "
            f"calibration source."
        )
