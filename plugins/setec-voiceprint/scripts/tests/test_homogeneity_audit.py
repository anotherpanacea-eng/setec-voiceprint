#!/usr/bin/env python3
"""Tests for homogeneity_audit.py (spec 30, M1) — pool-level set homogeneity, stdlib lens.

M1 is fully CI-testable (stdlib local-stylometric lens, no model, no API). Covers the spec-28 /
findings-folded acceptance set: deterministic output, envelope shape, claim-license-present +
refuses-verdict, the recursive no-`is_ai`/no-band field guard, the model-free numeric pins (collapsed
pool → cos≈1 / modes≈1; diverse pool → lower cos / modes>1; orthonormal pin; proximity monotone), the
set-floor abstention, bad-input graceful degradation, lens-label honesty, the reference-threshold
honesty (the ~0.8 line is named as the upstream semantic figure, NOT a band), and the privacy-gate
posture (M1's local lens does NOT trip the gate — writing to a public --out path is allowed).
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest  # type: ignore

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import homogeneity_audit as ha  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

# Twelve genuinely distinct ~19-word prose lines (above the 15-word per-text floor, >= the default
# set floor of 10) — a DIVERSE pool.
_DIVERSE = [
    "The ocean swallowed the last light of the day while gulls wheeled overhead crying loudly into the salt wind.",
    "Quantum entanglement defies our intuition about locality and separability in modern physics and continues to puzzle researchers everywhere today.",
    "She baked three loaves of sourdough bread before the heavy storm knocked the power out across the whole sleepy village.",
    "Tax policy reform requires carefully balancing equity against efficiency across many competing constituencies and entrenched political interests over time.",
    "The dragon coiled around the jagged mountain peak and exhaled a long plume of green fire into the cold morning.",
    "Investors fled toward safe havens as the bond market loudly signaled a coming recession sometime in the months ahead.",
    "My grandmother always kept her cherished recipes in a small tin box rusted shut by decades of kitchen damp.",
    "The algorithm sorts the enormous array in logarithmic time using a clever recursive divide and conquer approach throughout.",
    "Rain hammered the rattling tin roof all through the night and the swollen river rose far past its muddy banks.",
    "Parliament debated the contentious measure for many hours before adjourning without any clear resolution late that gray evening.",
    "He tuned the battered old guitar very slowly listening closely for the faint buzz of one loose metal string.",
    "The telescope captured the faint ancient light from a distant galaxy billions of years deep in the cosmic past.",
]

# A collapsed pool: twelve copies of one >= 15-word text.
_COLLAPSED_TEXT = (
    "The committee reviewed the proposal carefully and decided to approve the new budget for the "
    "upcoming fiscal year after much deliberation and several long meetings about the matter."
)
_COLLAPSED = [_COLLAPSED_TEXT] * 12

_FORBIDDEN_KEYS = {"is_ai", "is_human", "verdict", "label", "same_author", "score"}


def _envelope(argv):
    out = io.StringIO()
    with redirect_stdout(out):
        rc = ha.main(argv)
    return rc, json.loads(out.getvalue())


def _manifest(tmp_path, texts, name="pool.jsonl"):
    p = tmp_path / name
    with p.open("w", encoding="utf-8") as f:
        for i, t in enumerate(texts):
            f.write(json.dumps({"id": f"x{i}", "text": t}) + "\n")
    return p


def _walk_keys(obj):
    """Yield every dict key anywhere in a nested structure (the recursive no-verdict walk)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


# --- method-level pins (audit_pool / audit_proximity) ------------------------

def test_deterministic_output():
    a = ha.audit_pool([(f"d{i}", t) for i, t in enumerate(_DIVERSE)])
    b = ha.audit_pool([(f"d{i}", t) for i, t in enumerate(_DIVERSE)])
    assert a == b


def test_collapsed_pool_cos_near_one_modes_near_one():
    r = ha.audit_pool([(f"c{i}", t) for i, t in enumerate(_COLLAPSED)])
    assert r["mean_pairwise_cosine"] == pytest.approx(1.0, abs=1e-9)
    assert r["effective_modes"] == pytest.approx(1.0, abs=1e-6)


def test_diverse_pool_lower_cos_more_modes():
    coll = ha.audit_pool([(f"c{i}", t) for i, t in enumerate(_COLLAPSED)])
    div = ha.audit_pool([(f"d{i}", t) for i, t in enumerate(_DIVERSE)])
    # more spread -> lower mean pairwise cosine AND more effective modes (monotone direction).
    assert div["mean_pairwise_cosine"] < coll["mean_pairwise_cosine"]
    assert div["effective_modes"] > 1.0
    assert div["effective_modes"] > coll["effective_modes"]


def test_effective_modes_bounded_and_orthonormal_pin():
    # effective_modes is in [1, n] for any pool.
    div = ha.audit_pool([(f"d{i}", t) for i, t in enumerate(_DIVERSE)])
    assert 1.0 <= div["effective_modes"] <= div["n_texts"]
    # An orthonormal set of n vectors: after mean-centering the participation ratio is ~ n-1
    # (centering removes one degree of freedom), well above the collapsed value of 1 and within [1, n].
    n = 8
    ortho = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    modes = ha.effective_modes(ortho)
    assert 1.0 <= modes <= n
    assert modes >= n - 1.5  # close to the maximal-spread bound, not collapsed


def test_proximity_monotone_self_is_one():
    self_prox = ha.audit_proximity(_DIVERSE[0], [("c", _DIVERSE[0])], centroid_source="self")
    other_prox = ha.audit_proximity(_DIVERSE[0], [("c", _DIVERSE[5])], centroid_source="other")
    assert self_prox["hivemind_proximity"] == pytest.approx(1.0, abs=1e-9)
    # a target equal to the centroid source is at least as close as a different target.
    assert self_prox["hivemind_proximity"] >= other_prox["hivemind_proximity"]


def test_effective_modes_none_without_numpy(monkeypatch):
    # Guarded numpy import: when numpy is unavailable, effective_modes degrades to None (the
    # distribution still ships) — never crashes.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *a, **k):
        if name == "numpy":
            raise ImportError("simulated: numpy absent")
        return real_import(name, *a, **k)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert ha.effective_modes([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]) is None


# --- envelope / posture (full CLI path) --------------------------------------

def test_envelope_shape_pool(tmp_path):
    rc, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["task_surface"] == "set_level_diversity"
    assert env["task_surface"] in VALID_TASK_SURFACES
    assert env["tool"] == "homogeneity_audit"
    r = env["results"]
    assert r["lens"] == "local-stylometric"
    assert r["n_texts"] == 12
    assert set(r["pairwise_cosine_distribution"]) == {"n", "mean", "sd", "min", "p10", "p50", "p90"}
    assert "mean_pairwise_cosine" in r and "effective_modes" in r
    assert "hivemind_proximity" not in r  # pool mode


def test_envelope_shape_proximity(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text(_DIVERSE[0], encoding="utf-8")
    centroid = tmp_path / "c.txt"
    centroid.write_text(_DIVERSE[0], encoding="utf-8")
    rc, env = _envelope(["--target", str(target), "--centroid", str(centroid), "--json"])
    assert rc == 0 and env["available"] is True
    r = env["results"]
    assert r["lens"] == "local-stylometric"
    assert "hivemind_proximity" in r
    assert r["centroid_provenance"]["n_texts"] == 1
    assert "pairwise_cosine_distribution" not in r  # single-doc mode


def test_claim_license_present_and_refuses_verdict(tmp_path):
    _, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)), "--json"])
    cl = env["claim_license"]
    assert cl is not None
    dnl = json.dumps(cl).lower()
    assert "ai/human" in dnl or "ai / human" in dnl
    assert "high homogeneity is not" in dnl  # the confound caveat
    assert "semantic" in dnl  # the lens-incomparability caveat
    assert "no verdict" in dnl


def test_no_verdict_field_guard_recursive(tmp_path):
    # The no-verdict guard is SCOPED TO results and enforced by a recursive key walk, not prose.
    _, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)), "--json"])
    keys = set(_walk_keys(env["results"]))
    assert _FORBIDDEN_KEYS.isdisjoint(keys), f"forbidden verdict key in results: {keys & _FORBIDDEN_KEYS}"
    # also no band key (finding P2: no absolute band for M1, like originality_audit)
    assert "provisional_band" not in keys
    assert "band" not in keys


def test_lens_label_honesty(tmp_path):
    _, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)), "--json"])
    assert env["results"]["lens"] == "local-stylometric"


def test_reference_threshold_named_not_a_band(tmp_path):
    _, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE)), "--json"])
    a = env["results"]["assumptions"]
    src = a["reference_threshold_source"].lower()
    assert "2510.22954" in src  # the upstream paper is cited
    assert "semantic" in src and "not calibrated" in src  # marked not-transferred to this lens
    # the band is explicitly absent, surfaced as a no_band assumption
    assert "no" in a["no_band"].lower() and "band" in a["no_band"].lower()


# --- abstention / bad input --------------------------------------------------

def test_set_floor_abstention(tmp_path):
    rc, env = _envelope(["--manifest", str(_manifest(tmp_path, _DIVERSE[:4])), "--json"])
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_below_floor_texts_dropped_then_set_floor(tmp_path):
    # short stubs (< 15 words) are dropped; a pool of only stubs abstains as bad_input.
    stubs = ["too short here friend"] * 12
    rc, env = _envelope(["--manifest", str(_manifest(tmp_path, stubs)), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input"


def test_empty_pool_bad_input(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    rc, env = _envelope(["--manifest", str(p), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input"


def test_malformed_manifest_skips_bad_rows(tmp_path):
    # a valid-JSON-but-non-object row and a non-JSON row are skipped, not crashed on.
    p = tmp_path / "m.jsonl"
    lines = [json.dumps({"id": f"x{i}", "text": t}) for i, t in enumerate(_DIVERSE)]
    lines.insert(0, "not json at all")
    lines.insert(1, "[1, 2, 3]")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rc, env = _envelope(["--manifest", str(p), "--json"])
    assert env["available"] is True and env["results"]["n_texts"] == 12


def test_proximity_without_centroid_is_bad_input(tmp_path):
    # No bundled centroid — a shipped default would smuggle a verdict. Absent --centroid the mode
    # is unavailable, not defaulted.
    target = tmp_path / "t.txt"
    target.write_text(_DIVERSE[0], encoding="utf-8")
    rc, env = _envelope(["--target", str(target), "--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input"


def test_pool_mode_needs_input(tmp_path):
    rc, env = _envelope(["--json"])
    assert env["available"] is False and env["reason_category"] == "bad_input"


# --- privacy-gate posture (M1 local lens does NOT trip the gate) -------------

def test_m1_local_lens_public_out_allowed(tmp_path):
    # Finding P2: the privacy gate (acquisition_core.check_output_privacy, a bare sys.exit(2) +
    # stderr, wired in general_imposters) is an M2 LUAR-only concern. M1's stdlib vectors are NOT
    # voiceprint-shaped, so writing to a PUBLIC --out path is allowed: no refusal, no exit 2.
    out = tmp_path / "public_out.json"  # not under any 'private' dir
    manifest = _manifest(tmp_path, _DIVERSE)
    # --out without --json writes the file and prints nothing; no SystemExit(2), no refusal.
    sink = io.StringIO()
    with redirect_stdout(sink):
        rc = ha.main(["--manifest", str(manifest), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["available"] is True


def test_dir_mode(tmp_path):
    d = tmp_path / "pool"
    d.mkdir()
    for i, t in enumerate(_DIVERSE):
        (d / f"p{i}.txt").write_text(t, encoding="utf-8")
    rc, env = _envelope(["--dir", str(d), "--json"])
    assert rc == 0 and env["available"] is True
    assert env["results"]["n_texts"] == 12
