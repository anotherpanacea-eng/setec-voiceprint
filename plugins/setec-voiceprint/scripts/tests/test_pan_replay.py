#!/usr/bin/env python3
"""Spec-04 tests for calibration/pan_replay.py — the PAN obfuscation
replay harness.

The six named tests in the spec's test contract:
  - test_envelope_and_surface
  - test_per_class_slicing
  - test_refuses_aggregate_score
  - test_robustness_card_reuse
  - test_missing_fixtures_clear_error
  - test_capabilities_entry_present
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))
CALIBRATION_DIR = SCRIPTS_ROOT / "calibration"
if str(CALIBRATION_DIR) not in sys.path:
    sys.path.insert(0, str(CALIBRATION_DIR))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import pan_replay  # type: ignore
import adversarial_robustness_card as arc  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[4]
BUNDLED_FIXTURE = SCRIPTS_ROOT / "test_data" / "pan_replay_fixture"


# ---------- Fixture helpers ----------


def _write_fixture(tmp_path: Path, rows: list[dict]) -> Path:
    """Write a pairs.jsonl fixture dir from inline-text rows."""
    fixtures = tmp_path / "fix"
    fixtures.mkdir()
    manifest = fixtures / "pairs.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return fixtures


# A pair of texts long enough for Tier 1 signals to compute.
_CLEAN = (
    "The river moved slowly under the bridge. Children laughed near the bank, "
    "throwing small stones that skipped across the dark water. An old man watched "
    "from a bench, his hands folded, his coat buttoned against the wind. He "
    "remembered other afternoons and other rivers and other children whose names "
    "he had long since forgotten. The light fell sideways through the trees."
)
_OBF = (
    "Beneath the bridge the current drifted along without haste. By the water kids "
    "were laughing and tossing pebbles that bounced over the dark surface. On a "
    "bench an elderly man looked on, his coat fastened, his hands clasped together. "
    "He thought of afternoons gone by, of rivers known, of youngsters whose names "
    "had slipped from him. Slanting light came down through the branches."
)


def _two_class_rows() -> list[dict]:
    return [
        {"id": "p1", "obfuscation_class": "paraphrase",
         "clean": _CLEAN, "obfuscated": _OBF},
        {"id": "u1", "obfuscation_class": "unicode",
         "clean": _CLEAN, "obfuscated": _OBF},
    ]


# ---------- test_envelope_and_surface ----------


def test_envelope_and_surface(tmp_path):
    """The JSON envelope validates (schema_version 1.0) and the task
    surface is the existing `validation` surface."""
    fixtures = _write_fixture(tmp_path, _two_class_rows())
    out_path = tmp_path / "out.json"
    rc = pan_replay.main([
        "--fixtures", str(fixtures), "--json", "--out", str(out_path),
    ])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["task_surface"] == "validation"
    assert payload["tool"] == "pan_replay"
    # ClaimLicense present and on the validation surface.
    assert payload["claim_license"]["task_surface"] == "validation"
    assert "per_class" in payload["results"]
    # The license refuses any detector-accuracy headline / aggregate score.
    dnl = payload["claim_license"]["does_not_license"].lower()
    assert "detector" in dnl
    assert "no aggregate" in dnl or "aggregate" in dnl


# ---------- test_per_class_slicing ----------


def test_per_class_slicing(tmp_path):
    """Output has independent per-(signal × class) entries with no
    cross-class mixing: each class's card is computed on its own pairs
    only."""
    rows = [
        {"id": "p1", "obfuscation_class": "paraphrase",
         "clean": _CLEAN, "obfuscated": _OBF},
        {"id": "u1", "obfuscation_class": "unicode",
         "clean": _CLEAN, "obfuscated": _OBF},
        {"id": "u2", "obfuscation_class": "unicode",
         "clean": _CLEAN, "obfuscated": _OBF},
    ]
    fixtures = _write_fixture(tmp_path, rows)
    result = pan_replay.replay(pan_replay.load_fixture_pairs(fixtures))

    per_class = result["per_class"]
    assert set(per_class.keys()) == {"paraphrase", "unicode"}
    # Strict slicing: paraphrase saw exactly its 1 pair, unicode its 2.
    assert per_class["paraphrase"]["n_pairs"] == 1
    assert per_class["unicode"]["n_pairs"] == 2
    assert result["n_pairs_by_class"] == {"paraphrase": 1, "unicode": 2}

    # Each (signal, class) cell is independent; the unicode class's
    # per-pair cells reference only unicode pair ids (no paraphrase id).
    for sig, block in per_class["unicode"]["per_signal"].items():
        assert set(block["per_pair"].keys()) <= {"u1", "u2"}
    for sig, block in per_class["paraphrase"]["per_signal"].items():
        assert set(block["per_pair"].keys()) <= {"p1"}

    # Selecting a subset of classes slices cleanly.
    sliced = pan_replay.replay(
        pan_replay.load_fixture_pairs(fixtures), classes=["unicode"],
    )
    assert set(sliced["per_class"].keys()) == {"unicode"}


# ---------- test_refuses_aggregate_score ----------


def test_refuses_aggregate_score(tmp_path):
    """No single robustness / accuracy number is emitted anywhere in
    the output."""
    fixtures = _write_fixture(tmp_path, _two_class_rows())
    result = pan_replay.replay(pan_replay.load_fixture_pairs(fixtures))
    payload = pan_replay.build_audit_payload(result)

    # No aggregate-score-shaped key may appear at the results top level.
    banned = {
        "robustness_score", "accuracy", "auc", "roc_auc", "tpr", "fpr",
        "overall_robustness", "aggregate_score", "score", "headline",
        "n_robust_signals", "n_fragile_signals",
    }
    assert banned.isdisjoint(set(result.keys()))
    assert banned.isdisjoint(set(payload["results"].keys()))

    # And no banned key hides deeper in the serialized payload.
    serialized = json.dumps(payload)
    parsed = json.loads(serialized)

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k not in banned, f"banned aggregate key present: {k}"
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(parsed["results"])
    # The license explicitly refuses an aggregate score.
    dnl = payload["claim_license"]["does_not_license"].lower()
    assert "aggregate" in dnl


# ---------- test_robustness_card_reuse ----------


def test_robustness_card_reuse(tmp_path):
    """Output conforms to adversarial_robustness_card's shape: the
    per-cell readings are produced by the reused build_robustness_card,
    carrying that card's signal names, value fields, and labels."""
    fixtures = _write_fixture(tmp_path, _two_class_rows())
    result = pan_replay.replay(pan_replay.load_fixture_pairs(fixtures))

    # Every signal reported is one the robustness card knows.
    card_signals = set(arc._VARIANCE_SIGNALS.keys())
    assert set(result["signals"]) <= card_signals
    assert set(result["signals"])  # non-empty

    # Per-cell shape mirrors the card cell: base_value / fixture_value /
    # relative_change / label. pan_replay renames fixture_value →
    # obfuscated_value and label → card_label but preserves the card's
    # label vocabulary.
    card_labels = {
        "stable", "moderate", "fragile", "inverted_polarity",
        "small_base", "unstable_small_base", "unknown",
    }
    for cls_block in result["per_class"].values():
        for sig, sig_block in cls_block["per_signal"].items():
            assert sig in card_signals
            for cell in sig_block["per_pair"].values():
                assert "base_value" in cell
                assert "obfuscated_value" in cell
                assert "relative_change" in cell
                assert cell["card_label"] in card_labels

    # And a direct cross-check: scoring one pair through pan_replay's
    # path yields the same per-signal cell as calling the reused card
    # builder directly.
    pair = pan_replay.load_fixture_pairs(fixtures)[0]
    base = pan_replay._score_text(pair["clean"])
    obf = pan_replay._score_text(pair["obfuscated"])
    direct_card = arc.build_robustness_card(
        base=base, fixtures=[(pair["id"], obf)],
    )
    assert "per_signal" in direct_card
    assert set(direct_card["per_signal"].keys()) == card_signals


# ---------- test_missing_fixtures_clear_error ----------


def test_missing_fixtures_clear_error(tmp_path, capsys):
    """A missing --fixtures directory produces a clear error and a
    non-zero exit, not a traceback."""
    missing = tmp_path / "nope"
    rc = pan_replay.main(["--fixtures", str(missing)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--fixtures" in err
    assert "does not exist" in err

    # A directory without a pairs.jsonl manifest is also a clear error.
    empty = tmp_path / "empty"
    empty.mkdir()
    rc2 = pan_replay.main(["--fixtures", str(empty)])
    assert rc2 == 2
    err2 = capsys.readouterr().err
    assert "pairs.jsonl" in err2

    # --fixtures is required (argparse error → SystemExit 2).
    if pytest is not None:
        with pytest.raises(SystemExit):
            pan_replay.main([])


# ---------- path-traversal hardening (P3) ----------


def test_fixture_path_cannot_escape_bundle(tmp_path):
    """A pairs.jsonl pointing clean_path / obfuscated_path outside the
    --fixtures bundle (via ``..`` or an absolute path) is rejected before
    any read. Fixtures may eventually come from a gated local download, so
    a manifest must not be able to read arbitrary files off disk."""
    # A secret file OUTSIDE the fixture bundle.
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET should never be read", encoding="utf-8")

    # ".." escape: clean is inline; obfuscated_path climbs out of fix/.
    fixtures = _write_fixture(tmp_path, [
        {"id": "esc", "obfuscation_class": "unicode",
         "clean": _CLEAN, "obfuscated_path": "../secret.txt"},
    ])
    with pytest.raises(pan_replay.FixtureError) as exc:
        pan_replay.load_fixture_pairs(fixtures)
    assert "outside" in str(exc.value).lower()
    assert "TOP SECRET" not in str(exc.value)  # contents never read

    # Absolute-path escape is rejected too.
    fixtures_abs = tmp_path / "abs_fix"
    fixtures_abs.mkdir()
    (fixtures_abs / "pairs.jsonl").write_text(
        json.dumps({"id": "esc2", "obfuscation_class": "unicode",
                    "clean": _CLEAN, "obfuscated_path": str(secret)}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(pan_replay.FixtureError) as exc2:
        pan_replay.load_fixture_pairs(fixtures_abs)
    assert "outside" in str(exc2.value).lower()


# ---------- test_capabilities_entry_present ----------


def test_capabilities_entry_present():
    """capabilities.yaml carries a pan_replay entry on the validation
    surface with the spec's status / handoff / compute fields."""
    yaml = pytest.importorskip("yaml") if pytest is not None else __import__("yaml")
    manifest_path = (
        REPO_ROOT / "plugins" / "setec-voiceprint" / "capabilities.yaml"
    )
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    entries = {e["id"]: e for e in manifest.get("entries", [])}
    assert "pan_replay" in entries
    entry = entries["pan_replay"]
    assert entry["surface"] == "validation"
    assert entry["status"] == "empirically_oriented"
    assert entry["handoff"] == "internal"
    assert entry["compute"]["tier"] == "core"
    assert entry["dependencies"]["python"] == []
    # script_path points at the real file (forward-slash, repo-relative).
    assert entry["script_path"] == (
        "plugins/setec-voiceprint/scripts/calibration/pan_replay.py"
    )
    assert (REPO_ROOT / entry["script_path"]).is_file()


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
