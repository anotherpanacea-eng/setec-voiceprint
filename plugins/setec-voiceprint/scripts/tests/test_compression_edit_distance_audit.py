#!/usr/bin/env python3
"""Tests for compression_edit_distance_audit.py — the compression_edit_distance
surface (paired-input mechanical edit-magnitude, literature_anchored, stdlib).

Every test is model-free and deterministic (stdlib lzma only). The two locked
golden pairs (a minimal-edit high-similarity pair + a major-edit low-similarity
pair) carry hand-checked raw + normalized values to fixed precision.
"""

from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
import lzma
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_REPO_ROOT = _SCRIPTS.parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import compression_edit_distance_audit as c  # type: ignore  # noqa: E402
from output_schema import (  # type: ignore  # noqa: E402
    VALID_TASK_SURFACES,
    OutputValidityError,
)
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402


# ----------------------------------------------------------------------
# Locked golden pairs (hand-checked; see module DECISION 1/2).
#   MINIMAL edit  → high similarity → small distance
#   MAJOR edit    → low similarity  → large distance
# The values are re-derived below from a stdlib re-implementation of the pinned
# formula, so the regression is anchored to the METHOD, not a magic number a
# refactor could silently drift from — AND the exact fixed-precision literals are
# asserted, so a change in the formula/compressor params is caught.
# ----------------------------------------------------------------------

REF = (
    "The committee reviewed the proposal carefully before the vote. "
    "Each member raised concerns about the budget and the timeline. "
    "After a long discussion they agreed to postpone the decision."
)
TGT_MINIMAL = (
    "The committee reviewed the revised proposal carefully before the vote. "
    "Each member raised concerns about the budget and the timeline. "
    "After a long discussion they agreed to postpone the decision."
)
TGT_MAJOR = (
    "Migratory seabirds navigate thousands of miles using magnetic cues. "
    "Their colonies cluster on remote cliffs far from any predator. "
    "Warming oceans now shift the fish stocks these birds depend on."
)

# Hand-checked locked values (fixed precision).
GOLDEN_MINIMAL_RAW = 10.0
GOLDEN_MINIMAL_NORM = 0.063694  # round(0.06369426751592357, 6)
GOLDEN_MAJOR_RAW = 132.0
GOLDEN_MAJOR_NORM = 0.790419  # round(0.7904191616766467, 6)


def _independent_ced(ref: str, tgt: str):
    """A second, independent implementation of the pinned directional formula (raw
    LZMA2, preset 9 | EXTREME, FORMAT_RAW) so the regression checks the METHOD
    against a from-scratch computation, not just the module against itself."""
    def C(s: bytes) -> int:
        filters = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}]
        return len(lzma.compress(s, format=lzma.FORMAT_RAW, filters=filters))
    rb, tb = ref.encode("utf-8"), tgt.encode("utf-8")
    raw = C(rb + tb) - C(rb)
    norm = raw / C(tb) if C(tb) else 0.0
    return float(raw), norm


# ----------------------------------------------------------------------
# Surface registration.
# ----------------------------------------------------------------------

def test_surface_registered():
    assert c.TASK_SURFACE == "compression_edit_distance"
    assert "compression_edit_distance" in VALID_TASK_SURFACES
    assert "compression_edit_distance" in TASK_SURFACE_LABELS


def test_surface_fragment_file_is_source_of_truth():
    frag = _SCRIPTS / "claim_license_surfaces" / "compression_edit_distance.txt"
    assert frag.exists()


# ----------------------------------------------------------------------
# refuses without --reference (fail-loud, nonzero exit, no single-doc mode).
# ----------------------------------------------------------------------

def test_refuses_without_reference():
    """Missing --reference → nonzero exit. argparse raises SystemExit(2) for the
    missing required arg; the surface NEVER degrades to a single-document mode."""
    with pytest.raises(SystemExit) as ei:
        c.main([str(_HERE / "nonexistent_target.txt")])
    assert ei.value.code != 0


def test_refuses_without_reference_prints_message_before_json(tmp_path, capsys):
    """The pinned paired-input message is printed to stderr (before any JSON could
    be emitted), so the fail-loud contract is explicit and greppable."""
    tgt = tmp_path / "t.txt"
    tgt.write_text(TGT_MINIMAL, encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        c.main([str(tgt)])
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert c.NO_REFERENCE_MESSAGE in err
    assert "--reference is required" in err
    # No JSON envelope was emitted to stdout on the fail-loud path.
    assert "schema_version" not in capsys.readouterr().out


def test_refuses_without_reference_subprocess_nonzero(tmp_path):
    """End-to-end: a real shell invocation with no --reference exits nonzero and
    prints nothing that looks like a completed JSON envelope on stdout."""
    tgt = tmp_path / "t.txt"
    tgt.write_text(TGT_MINIMAL, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "compression_edit_distance_audit.py"), str(tgt)],
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "--reference is required" in proc.stderr
    assert "schema_version" not in proc.stdout


# ----------------------------------------------------------------------
# Determinism (same pair → same value).
# ----------------------------------------------------------------------

def test_deterministic():
    a = c.compression_edit_distance(REF, TGT_MAJOR)
    b = c.compression_edit_distance(REF, TGT_MAJOR)
    assert a["distance_raw"] == b["distance_raw"]
    assert a["distance_normalized"] == b["distance_normalized"]
    assert a["compressed_sizes"] == b["compressed_sizes"]


def test_compressed_size_is_header_free_and_deterministic():
    """C(s) uses raw LZMA2 (FORMAT_RAW): no xz container, no header, no checksum —
    so the count is a pure function of the input, stable across runs."""
    data = (REF * 3).encode("utf-8")
    assert c.compressed_size(data) == c.compressed_size(data)
    # A FORMAT_RAW stream carries no xz magic (\xfd7zXZ\x00) or .lzma header —
    # verify against the container format directly.
    raw = lzma.compress(data, format=lzma.FORMAT_RAW, filters=c.LZMA_FILTERS)
    assert not raw.startswith(b"\xfd7zXZ\x00")
    assert len(raw) == c.compressed_size(data)
    assert isinstance(c.compressed_size(data), int)
    assert c.compressed_size(data) > 0


# ----------------------------------------------------------------------
# Known-pair value regression (the two locked golden pairs).
# ----------------------------------------------------------------------

def test_golden_minimal_edit_pair():
    r = c.compression_edit_distance(REF, TGT_MINIMAL)
    assert r["distance_raw"] == GOLDEN_MINIMAL_RAW
    assert round(r["distance_normalized"], 6) == GOLDEN_MINIMAL_NORM
    # Anchored to an independent re-implementation of the method.
    ind_raw, ind_norm = _independent_ced(REF, TGT_MINIMAL)
    assert r["distance_raw"] == ind_raw
    assert math.isclose(r["distance_normalized"], ind_norm, rel_tol=1e-12)


def test_golden_major_edit_pair():
    r = c.compression_edit_distance(REF, TGT_MAJOR)
    assert r["distance_raw"] == GOLDEN_MAJOR_RAW
    assert round(r["distance_normalized"], 6) == GOLDEN_MAJOR_NORM
    ind_raw, ind_norm = _independent_ced(REF, TGT_MAJOR)
    assert r["distance_raw"] == ind_raw
    assert math.isclose(r["distance_normalized"], ind_norm, rel_tol=1e-12)


def test_minimal_edit_is_smaller_than_major_edit():
    """The whole point: a near-copy separates by far less than an unrelated rewrite."""
    mn = c.compression_edit_distance(REF, TGT_MINIMAL)
    mj = c.compression_edit_distance(REF, TGT_MAJOR)
    assert mn["distance_raw"] < mj["distance_raw"]
    assert mn["distance_normalized"] < mj["distance_normalized"]


def test_metric_is_directional_not_ncd():
    """DECISION 2 pinned: the metric is the directional C(ref+tgt) - C(ref), NOT
    symmetric NCD. On an asymmetric pair the directional value must differ from the
    NCD value, so a silent switch to NCD is caught."""
    def C(s):
        filters = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}]
        return len(lzma.compress(s.encode(), format=lzma.FORMAT_RAW, filters=filters))
    directional = c.compression_edit_distance(REF, TGT_MAJOR)["distance_raw"]
    c_ref, c_tgt, c_both = C(REF), C(TGT_MAJOR), C(REF + TGT_MAJOR)
    ncd = (c_both - min(c_ref, c_tgt)) / max(c_ref, c_tgt)
    assert directional == float(c_both - c_ref)   # directional definition
    # The two measures are genuinely different quantities on this pair.
    assert not math.isclose(directional, ncd, rel_tol=1e-9)


def test_identical_pair_is_near_zero():
    """An identical target still costs a few bits to signal the long back-reference,
    so distance_raw for reference==target is a small positive integer (not exactly
    0) — an honest property of compression distance, pinned here."""
    r = c.compression_edit_distance(REF, REF)
    assert r["distance_raw"] >= 0.0
    # Far smaller than any real edit (the minimal-edit pair).
    minimal = c.compression_edit_distance(REF, TGT_MINIMAL)["distance_raw"]
    assert r["distance_raw"] < minimal


def test_long_reference_exceeds_deflate_window():
    """THE review P1 pinned (Fable, 2026-07-05): a verbatim excerpt of a
    manuscript-scale reference must score as a NEAR-COPY. Under the first build's
    raw DEFLATE (32 KiB window) this exact pair scored distance_normalized ≈ 0.90
    ("heavy edit") because back-references could not reach the excerpt's origin;
    under raw LZMA2 (64 MiB dictionary) it scores ≈ 0.01. Deterministic corpus
    (seeded), ~120 KB reference, verbatim first ~12 KB as the target."""
    import random
    rng = random.Random(42)
    vocab = [f"w{n}" for n in range(4000)]
    reference = " ".join(rng.choice(vocab) for _ in range(20000))  # ~120 KB
    target = reference[:12000]                                     # verbatim excerpt
    r = c.compression_edit_distance(reference, target)
    assert r["distance_normalized"] < 0.1, (
        "a verbatim excerpt of a long reference must read as a near-copy — a large "
        "value here means the compressor's window cannot span the reference "
        "(the DEFLATE 32 KiB failure mode)"
    )


def test_other_argparse_errors_do_not_claim_missing_reference(tmp_path, capsys):
    """The pinned NO_REFERENCE_MESSAGE fires ONLY when --reference is genuinely
    absent. A different argparse failure (missing TARGET with --reference present)
    must NOT print it — that would misdiagnose the operator's actual mistake."""
    ref = tmp_path / "r.txt"
    ref.write_text(REF, encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        c.main(["--reference", str(ref)])   # TARGET missing; --reference present
    assert ei.value.code != 0
    err = capsys.readouterr().err
    assert c.NO_REFERENCE_MESSAGE not in err
    # the --reference=VALUE spelling is also recognized as "present"
    with pytest.raises(SystemExit):
        c.main([f"--reference={ref}"])
    assert c.NO_REFERENCE_MESSAGE not in capsys.readouterr().err


# ----------------------------------------------------------------------
# Envelope shape.
# ----------------------------------------------------------------------

def test_envelope_shape():
    results = c.audit_compression_edit_distance(REF, TGT_MAJOR)
    env = c.compose_envelope(
        reference_path="ref.txt", target_path="tgt.txt",
        target_words=len(TGT_MAJOR.split()), results=results,
    )
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "compression_edit_distance"
    assert env["tool"] == "compression_edit_distance_audit"
    assert env["available"] is True
    assert env["claim_license"] is not None
    r = env["results"]
    for key, typ in [
        ("distance_raw", float), ("distance_normalized", float),
        ("reference_bytes", int), ("target_bytes", int),
        ("metric", str), ("normalization", str),
    ]:
        assert key in r, f"missing results.{key}"
        assert isinstance(r[key], typ), f"results.{key} is {type(r[key])}, expected {typ}"
    assert r["metric"] == "lzma_compression_edit_distance"
    # calibration_status: literature_anchored is carried on the claim-license refs /
    # comparison mode (no corpus_provenance shipped).
    assert env["claim_license"]["comparison_set"]["mode"] == "paired_pre_post_uncalibrated"
    # No corpus_provenance (no shipped model/calibration), consistent with spec 13.
    assert "corpus_provenance" not in r
    assert "corpus_provenance" not in env


def test_envelope_carries_no_verdict_keys_recursive():
    """No is_ai / is_human / label / verdict / decision / percent key anywhere."""
    env = c.compose_envelope(
        reference_path="ref.txt", target_path="tgt.txt", target_words=30,
        results=c.audit_compression_edit_distance(REF, TGT_MAJOR),
    )
    banned = (
        "is_ai", "is_human", "ai_generated", "human_written", "label",
        "prediction", "classification", "verdict", "decision", "percent_ai",
        "pct_ai", "p_ai",
    )

    def walk(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                path = f"{prefix}.{k}" if prefix else str(k)
                low = str(k).lower()
                for b in banned:
                    assert b not in low, f"forbidden key {b!r} at {path}"
                walk(v, path)
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                walk(item, f"{prefix}[{i}]")

    walk(env)


# ----------------------------------------------------------------------
# Claim license refuses absolute % + attribution (+ cites the preprint).
# ----------------------------------------------------------------------

def test_claim_license_refuses_absolute_percent_and_attribution():
    lic = c._claim_license(c.audit_compression_edit_distance(REF, TGT_MAJOR))
    assert lic.task_surface == "compression_edit_distance"
    dn = lic.does_not_license.lower()
    # Refuses an absolute % AI-edited figure.
    assert "% ai-edited" in dn or "percent" in dn or "% ai" in dn
    # Refuses a dosage / amount-of-AI claim.
    assert "dosage" in dn
    # Refuses provenance / authorship.
    assert "provenance" in dn or "authorship" in dn
    # Refuses per-sentence localization + cross-corpus generalization.
    assert "localize" in dn or "per sentence" in dn or "per-sentence" in dn
    assert "cross-corpus" in dn or "cross corpus" in dn
    # No single-document verdict key.
    assert "is_ai" in dn or "label" in dn or "verdict" in dn
    # Licenses only the informational edit-distance between the two supplied texts.
    lz = lic.licenses.lower()
    assert "informational edit-distance" in lz or "edit-distance between the two" in lz
    assert "measurement" in lz and "not a verdict" in lz
    # arXiv root + UNVERIFIED status cited.
    refs = " ".join(lic.references)
    assert "2412.17321" in refs
    assert "unverified" in refs.lower()


def test_claim_license_states_paired_input_is_load_bearing():
    lic = c._claim_license(c.audit_compression_edit_distance(REF, TGT_MAJOR))
    caveats = " ".join(lic.additional_caveats).lower()
    assert "paired-input" in caveats or "paired input" in caveats
    assert "fails loud" in caveats or "fail loud" in caveats
    # Refuses to be a single-document detector.
    assert "single-document" in lic.does_not_license.lower()


# ----------------------------------------------------------------------
# CLI happy path + error envelopes.
# ----------------------------------------------------------------------

def _run(argv, tmp_path):
    out_path = tmp_path / "env.json"
    rc = c.main(argv + ["--json", "--out", str(out_path)])
    env = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
    return rc, env


def test_cli_happy_path(tmp_path):
    ref = tmp_path / "ref.txt"; ref.write_text(REF, encoding="utf-8")
    tgt = tmp_path / "tgt.txt"; tgt.write_text(TGT_MAJOR, encoding="utf-8")
    rc, env = _run([str(tgt), "--reference", str(ref)], tmp_path)
    assert rc == 0
    assert env["available"] is True
    assert env["results"]["distance_raw"] == GOLDEN_MAJOR_RAW
    assert env["baseline"]["role"] == "reference_pre_edit"
    assert env["baseline"]["path"].endswith("ref.txt")


def test_cli_bad_input_unreadable_reference(tmp_path):
    tgt = tmp_path / "tgt.txt"; tgt.write_text(TGT_MAJOR, encoding="utf-8")
    rc, env = _run([str(tgt), "--reference", str(tmp_path / "nope.txt")], tmp_path)
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert "reference" in env["reason"]


def test_cli_bad_input_unreadable_target(tmp_path):
    ref = tmp_path / "ref.txt"; ref.write_text(REF, encoding="utf-8")
    rc, env = _run([str(tmp_path / "nope.txt"), "--reference", str(ref)], tmp_path)
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_cli_text_too_short_empty(tmp_path):
    ref = tmp_path / "ref.txt"; ref.write_text(REF, encoding="utf-8")
    tgt = tmp_path / "empty.txt"; tgt.write_text("   \n ", encoding="utf-8")
    rc, env = _run([str(tgt), "--reference", str(ref)], tmp_path)
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "text_too_short"


def test_cli_short_text_warns_but_runs(tmp_path):
    ref = tmp_path / "ref.txt"; ref.write_text("a short pre-edit draft of only a few words", encoding="utf-8")
    tgt = tmp_path / "tgt.txt"; tgt.write_text("a short post edit draft of only a few words now", encoding="utf-8")
    rc, env = _run([str(tgt), "--reference", str(ref)], tmp_path)
    assert rc == 0
    assert env["available"] is True
    assert any("floor" in w for w in env["warnings"])


def test_cli_markdown_default(tmp_path):
    ref = tmp_path / "ref.txt"; ref.write_text(REF, encoding="utf-8")
    tgt = tmp_path / "tgt.txt"; tgt.write_text(TGT_MAJOR, encoding="utf-8")
    out = tmp_path / "report.md"
    rc = c.main([str(tgt), "--reference", str(ref), "--out", str(out)])
    assert rc == 0
    md = out.read_text(encoding="utf-8")
    assert "Compression Edit-Distance Audit" in md
    assert "distance_raw" in md
    assert "NOT a '% AI-edited'" in md
    assert "Claim license" in md


# ----------------------------------------------------------------------
# Input error + bounds (R4 gate live).
# ----------------------------------------------------------------------

def test_audit_raises_on_empty_reference():
    with pytest.raises(c.CompressionInputError):
        c.audit_compression_edit_distance("   ", TGT_MAJOR)


def test_audit_raises_on_empty_target():
    with pytest.raises(c.CompressionInputError):
        c.audit_compression_edit_distance(REF, "   ")


def test_nan_injection_caught_by_bounds_gate():
    """A poisoned NaN distance must be caught by the R4 output-validity gate."""
    results = c.audit_compression_edit_distance(REF, TGT_MAJOR)
    results["distance_normalized"] = float("nan")
    with pytest.raises(OutputValidityError):
        c.compose_envelope(
            reference_path="r", target_path="t", target_words=30, results=results,
        )


# ----------------------------------------------------------------------
# Separation guard (no fitness/selection/scoring imports).
# ----------------------------------------------------------------------

def test_separation_guard_no_forbidden_imports():
    source = Path(c.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    # Strip string/docstring spans so the scan tests CODE, not documentation.
    spans = []

    class _V(ast.NodeVisitor):
        def visit_Constant(self, node):  # noqa: N802
            if isinstance(node.value, str) and hasattr(node, "end_lineno"):
                spans.append((node.lineno, node.col_offset, node.end_lineno, node.end_col_offset))
            self.generic_visit(node)

    _V().visit(tree)
    lines = source.splitlines()
    for (sl, sc, el, ec) in spans:
        if sl == el:
            lines[sl - 1] = lines[sl - 1][:sc] + " " * (ec - sc) + lines[sl - 1][ec:]
        else:
            lines[sl - 1] = lines[sl - 1][:sc]
            for i in range(sl, el - 1):
                lines[i] = ""
            lines[el - 1] = lines[el - 1][ec:]
    code = "\n".join(
        (ln if (h := ln.find("#")) < 0 else ln[:h]) for ln in lines
    )
    for forbidden in ("fitness", "setec_signals", "cosplay", "qlora", "reviser", "argmax"):
        assert forbidden not in code, (
            f"separation-guard leak: {forbidden!r} referenced in CODE — the "
            f"distance is an evidence value, never a selection signal"
        )


# ----------------------------------------------------------------------
# Registration + drift + golden.
# ----------------------------------------------------------------------

def test_capability_entry_and_golden_present():
    tools_dir = _REPO_ROOT / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    import check_capabilities_drift as drift  # type: ignore

    report = drift.check_drift()
    assert report.passed, (
        "capabilities drift detected:\n"
        + "\n".join(v.render() for v in report.violations)
    )
    manifest = drift.load_manifest(drift.DEFAULT_MANIFEST)
    entry = next(
        (e for e in manifest["entries"] if e.get("id") == "compression_edit_distance_audit"),
        None,
    )
    assert entry is not None, "compression_edit_distance_audit missing from capabilities.d"
    assert entry["surface"] == "compression_edit_distance"
    assert entry["status"] == "literature_anchored"
    assert entry["compute"]["tier"] == "core"
    assert entry["dependencies"]["python"] == []

    golden = _HERE / "_golden_capabilities" / "compression_edit_distance_audit.json"
    assert golden.exists()
    assert json.loads(golden.read_text(encoding="utf-8")) == entry


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
