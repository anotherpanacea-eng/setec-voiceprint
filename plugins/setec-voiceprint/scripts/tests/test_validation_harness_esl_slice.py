#!/usr/bin/env python3
"""Tests for the ESL / L2 fairness slice in validation_harness.py
(spec 05-esl-fairness-slice).

The slice reports per-`language_status` FPR/TPR/ROC alongside the
existing surface × register × length × AI-status slices, so SETEC can
MEASURE — and refuse to pool away — the documented non-native-English
false-positive failure mode (Liang et al., Patterns 2023: 61% of human
TOEFL essays flagged as AI).

The five named spec tests:

  * test_language_status_slices_present — per-status FPR/TPR present.
  * test_refuses_pooled_fpr — mixing native + non-native without slicing
    trips the don't-pool guard (no single aggregate FPR emitted).
  * test_empty_slice_caveat — a status with zero entries of a class
    yields an explicit underpowered/refusal message, not a silent 0.
  * test_native_only_annotation — aggregate FPR labeled native-only when
    that's all that's present.
  * test_backward_compat — manifests with no language status still
    produce the prior report shape.

The fixture prose lives in test_data/esl_fairness_fixture/ and is
SYNTHETIC (the operator-/public-domain-sourced L2 corpus is the spec's
gating follow-up). These tests pin the slice's *logic* — shape, guard,
caveats, license — not any empirical ESL false-positive rate.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "validation_harness.py"
FIXTURE_DIR = ROOT / "test_data" / "esl_fairness_fixture"

# Fixture prose files (synthetic; see the fixture README).
NATIVE_HUMAN = ("native_human_01.txt", "native_human_02.txt")
NATIVE_AI = ("native_ai_01.txt",)
NON_NATIVE_HUMAN_INT = ("non_native_human_01.txt",)
NON_NATIVE_HUMAN_ADV = ("non_native_human_02.txt",)
LEARNER_HUMAN = ("learner_human_01.txt",)
NON_NATIVE_AI = ("non_native_ai_01.txt",)


def _entry(
    eid: str,
    fname: str,
    ai_status: str,
    language_status: str | None,
) -> dict:
    # Absolute path to the fixture prose so the manifest resolves
    # correctly from a tmp_path location (mirrors the absolute-path
    # convention in test_validation_harness_check_corpus.py).
    entry = {
        "id": eid,
        "path": str(FIXTURE_DIR / fname),
        "ai_status": ai_status,
        "register": "blog_essay",
        "use": ["validation"],
        "split": "test",
        "privacy": "shareable",
    }
    if language_status is not None:
        entry["language_status"] = language_status
    return entry


def write_manifest(path: Path, entries: list[dict]) -> None:
    """Write a JSONL manifest. UTF-8 with no BOM (the manifest validator
    rejects a BOM)."""
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )


def run_harness(manifest: Path, *extra: str) -> dict:
    """Run the harness in JSON mode and return the parsed envelope.

    --no-tier2/--no-tier3 keep this CPU-only and model-free;
    --metric-bootstrap-resamples 0 keeps it fast (the slice's CI shape
    is exercised via the per-rate Wilson intervals, which don't need
    the bootstrap)."""
    proc = subprocess.run(
        [
            sys.executable,
            str(HARNESS),
            str(manifest),
            "--no-tier2",
            "--no-tier3",
            "--metric-bootstrap-resamples",
            "0",
            "--json",
            *extra,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"harness exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------
# 1. test_language_status_slices_present
# ---------------------------------------------------------------------


def test_language_status_slices_present(tmp_path: Path) -> None:
    """A manifest with native + non-native, AI + human entries produces
    a `language_status_slices` block carrying per-status FPR/TPR/ROC."""
    entries = [
        _entry("nh1", NATIVE_HUMAN[0], "pre_ai_human", "native"),
        _entry("nh2", NATIVE_HUMAN[1], "pre_ai_human", "native"),
        _entry("na1", NATIVE_AI[0], "ai_generated", "native"),
        _entry("nnh1", NON_NATIVE_HUMAN_INT[0], "pre_ai_human",
               "non_native_intermediate"),
        _entry("nnh2", NON_NATIVE_HUMAN_ADV[0], "pre_ai_human",
               "non_native_advanced"),
        _entry("lh1", LEARNER_HUMAN[0], "pre_ai_human", "learner"),
        _entry("nna1", NON_NATIVE_AI[0], "ai_generated",
               "non_native_advanced"),
    ]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, entries)

    payload = run_harness(manifest, "--fpr-target", "0.1")
    result = payload["results"]

    # On by default when any entry carries a non-`unknown` status.
    assert result["language_slice_active"] is True
    slices = result["language_status_slices"]
    assert slices, "language_status_slices block should be non-empty"

    # Every present status has a slice; each carries the spec contract
    # keys {n, fpr, tpr, roc_auc, ci}.
    for status in (
        "native",
        "non_native_advanced",
        "non_native_intermediate",
        "learner",
    ):
        assert status in slices, f"missing slice for {status}"
        block = slices[status]
        for key in ("n", "fpr", "tpr", "roc_auc", "ci"):
            assert key in block, f"{status} slice missing key {key!r}"
        assert isinstance(block["n"], int)

    # The non-native statuses are flagged as such.
    assert slices["non_native_advanced"]["is_non_native"] is True
    assert slices["native"]["is_non_native"] is False

    # FPR/TPR are thresholded rates (dicts with a numeric value) once an
    # FPR target is supplied — present, not a silent 0 placeholder.
    native = slices["native"]
    assert native["n_negative"] >= 1
    assert isinstance(native["fpr"], dict)
    assert native["fpr"]["value"] is not None


# ---------------------------------------------------------------------
# 2. test_refuses_pooled_fpr
# ---------------------------------------------------------------------


def test_refuses_pooled_fpr(tmp_path: Path) -> None:
    """Native + non-native present with the slice DISABLED trips the
    don't-pool guard: no single aggregate FPR is emitted."""
    entries = [
        _entry("nh1", NATIVE_HUMAN[0], "pre_ai_human", "native"),
        _entry("nh2", NATIVE_HUMAN[1], "pre_ai_human", "native"),
        _entry("na1", NATIVE_AI[0], "ai_generated", "native"),
        _entry("nnh1", NON_NATIVE_HUMAN_INT[0], "pre_ai_human",
               "non_native_intermediate"),
    ]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, entries)

    payload = run_harness(
        manifest, "--fpr-target", "0.1", "--no-language-status-slice"
    )
    result = payload["results"]

    pooling = result["language_pooling"]
    assert pooling["disposition"] == "refused"
    assert pooling["refuse_aggregate_fpr"] is True
    assert "pool" in pooling["message"].lower()

    # The operating point is annotated as refused.
    op = result["operating_point"]
    assert op["aggregate_fpr_refused"] is True

    # The pooled aggregate FPR is replaced by an explicit refusal marker
    # — no single numeric aggregate FPR is published.
    overall_fpr = result["slices"]["overall"]["threshold_metrics"]["rates"]["fpr"]
    assert overall_fpr.get("refused") is True
    assert overall_fpr.get("value") is None

    # The claim license refuses on the pooling basis.
    refuses = result["claim_license"]["language_status_slice"]["refuses"]
    assert refuses is not None and "REFUSED" in refuses


def test_refuses_pooled_fpr_default_on_does_not_refuse(tmp_path: Path) -> None:
    """Sanity companion: the SAME mixed manifest with the slice ON (the
    default) does NOT refuse — it slices instead. This pins that the
    refusal is specifically about pooling-without-slicing, not about
    mixed backgrounds per se."""
    entries = [
        _entry("nh1", NATIVE_HUMAN[0], "pre_ai_human", "native"),
        _entry("na1", NATIVE_AI[0], "ai_generated", "native"),
        _entry("nnh1", NON_NATIVE_HUMAN_INT[0], "pre_ai_human",
               "non_native_intermediate"),
    ]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, entries)

    payload = run_harness(manifest, "--fpr-target", "0.1")
    result = payload["results"]
    assert result["language_slice_active"] is True
    assert result["language_pooling"]["disposition"] == "sliced"
    assert result["language_pooling"]["refuse_aggregate_fpr"] is False
    # Aggregate FPR is NOT refused when sliced.
    overall_fpr = result["slices"]["overall"]["threshold_metrics"]["rates"]["fpr"]
    assert overall_fpr.get("refused") is not True


# ---------------------------------------------------------------------
# 3. test_empty_slice_caveat
# ---------------------------------------------------------------------


def test_empty_slice_caveat(tmp_path: Path) -> None:
    """A non-native status with zero control (negative) records yields an
    explicit empty/underpowered caveat and a None FPR — never a silent
    0. Here the only non-native entry is AI (positive), so the
    non-native slice has zero negatives for FPR."""
    entries = [
        _entry("nh1", NATIVE_HUMAN[0], "pre_ai_human", "native"),
        _entry("nh2", NATIVE_HUMAN[1], "pre_ai_human", "native"),
        _entry("na1", NATIVE_AI[0], "ai_generated", "native"),
        # Only non-native entry is AI => the non-native slice has NO
        # control/negative records.
        _entry("nna1", NON_NATIVE_AI[0], "ai_generated",
               "non_native_advanced"),
    ]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, entries)

    payload = run_harness(manifest, "--fpr-target", "0.1")
    result = payload["results"]

    nn = result["language_status_slices"]["non_native_advanced"]
    assert nn["n_negative"] == 0
    # FPR is undefined (None), NOT a silent 0.
    assert nn["fpr"] is None
    assert nn.get("fpr_powered") is False
    assert "fpr_caveat" in nn
    caveat = nn["fpr_caveat"].lower()
    assert "underpowered" in caveat or "empty" in caveat
    # The caveat must NOT read as "0 false positives".
    assert "not 0" in nn["fpr_caveat"]

    # The claim license refuses evaluative/disciplinary use of the
    # underpowered non-native slice.
    refuses = result["claim_license"]["language_status_slice"]["refuses"]
    assert refuses is not None
    assert "non_native_advanced" in refuses

    # A warning surfaces the underpowered slice too.
    warnings_text = " ".join(result.get("warnings") or [])
    assert "non_native_advanced" in warnings_text


# ---------------------------------------------------------------------
# 4. test_native_only_annotation
# ---------------------------------------------------------------------


def test_native_only_annotation(tmp_path: Path) -> None:
    """When only native entries are present, the aggregate FPR is
    annotated native-only (and not refused)."""
    entries = [
        _entry("nh1", NATIVE_HUMAN[0], "pre_ai_human", "native"),
        _entry("nh2", NATIVE_HUMAN[1], "pre_ai_human", "native"),
        _entry("na1", NATIVE_AI[0], "ai_generated", "native"),
    ]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, entries)

    payload = run_harness(manifest, "--fpr-target", "0.1")
    result = payload["results"]

    pooling = result["language_pooling"]
    assert pooling["disposition"] == "native_only"
    assert pooling["annotation"] == "native-only"
    assert pooling["refuse_aggregate_fpr"] is False

    op = result["operating_point"]
    assert op["aggregate_fpr_annotation"] == "native-only"
    # The aggregate is still published (annotated, not refused).
    overall_fpr = result["slices"]["overall"]["threshold_metrics"]["rates"]["fpr"]
    assert overall_fpr.get("refused") is not True
    assert overall_fpr.get("value") is not None

    # A native-only warning is surfaced.
    warnings_text = " ".join(result.get("warnings") or [])
    assert "native-only" in warnings_text

    # The license is explicit that nothing is licensed about non-native.
    refuses = result["claim_license"]["language_status_slice"]["refuses"]
    assert refuses is not None
    assert "non-native" in refuses.lower()


# ---------------------------------------------------------------------
# 5. test_backward_compat
# ---------------------------------------------------------------------


def test_backward_compat(tmp_path: Path) -> None:
    """Manifests with NO language_status produce the prior report shape:
    the ESL slice is off, the aggregate FPR is unmodified, and no
    refusal/annotation is applied."""
    entries = [
        _entry("a", NATIVE_HUMAN[0], "pre_ai_human", None),
        _entry("b", NATIVE_HUMAN[1], "pre_ai_human", None),
        _entry("c", NATIVE_AI[0], "ai_generated", None),
    ]
    manifest = tmp_path / "manifest.jsonl"
    write_manifest(manifest, entries)

    payload = run_harness(manifest, "--fpr-target", "0.1")
    result = payload["results"]

    # Slice is off; no per-status block.
    assert result["language_slice_active"] is False
    assert result["language_status_slices"] == {}
    assert result["language_pooling"]["disposition"] == "not_applicable"
    assert result["language_pooling"]["refuse_aggregate_fpr"] is False

    # The aggregate FPR is unmodified — present, numeric, not refused.
    overall_rates = result["slices"]["overall"]["threshold_metrics"]["rates"]
    assert overall_rates["fpr"].get("refused") is not True
    assert overall_rates["fpr"]["value"] is not None
    assert "pooled_fpr_refused" not in result["slices"]["overall"][
        "threshold_metrics"
    ]

    # The operating point carries no ESL annotation/refusal keys.
    op = result["operating_point"]
    assert "aggregate_fpr_refused" not in op
    assert "aggregate_fpr_annotation" not in op

    # The prior slice families are all still present (report shape pin).
    slices = result["slices"]
    for key in (
        "overall",
        "by_register",
        "by_length_bucket",
        "by_language_status",
        "by_adversarial_class",
        "by_ai_status",
    ):
        assert key in slices, f"prior slice family {key!r} missing"

    # The ESL claim-license clause reports itself inactive.
    lang_license = result["claim_license"]["language_status_slice"]
    assert lang_license["active"] is False
