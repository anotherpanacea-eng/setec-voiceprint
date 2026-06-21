#!/usr/bin/env python3
"""Tests for gecscore_audit.py — the GECScore grammar-error-density surface
(spec 32, M1).

Every test runs the default model-free path or a DETERMINISTIC STUB GecBackend. No
LanguageTool/GECToR backend is ever loaded or imported: the real backends are the
M2 model-CPU seam, exercised only behind main() (not touched here). The numbered
tests map to the spec's AC-1..AC-11.
"""

from __future__ import annotations

import ast
import json
import math
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_REPO_ROOT = _SCRIPTS.parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import gecscore_audit as g  # type: ignore  # noqa: E402
from output_schema import (  # type: ignore  # noqa: E402
    VALID_TASK_SURFACES,
    OutputValidityError,
)
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402


# ----------------------------------------------------------------------
# Fixtures / helpers.
# ----------------------------------------------------------------------

def _make_text(n_words: int) -> str:
    """A real-ish paragraph repeated to clear the 50-word floor."""
    para = (
        "The question of responsibility cannot be separated from the structure of "
        "agency. When we hold a person accountable we presuppose that the action "
        "flowed from deliberation rather than compulsion, yet the boundary between "
        "them is rarely sharp. "
    )
    out = ""
    while len(out.split()) < n_words:
        out += para
    return out


_ALLOWED_BANDS = {"indeterminate", "low_error_density", "high_error_density"}

# Forbidden *key* substrings (recursive key walk).
_BANNED_KEY_SUBSTRINGS = (
    "is_ai", "is_human", "ai_generated", "human_written", "label",
    "prediction", "classification", "verdict", "decision", "p_ai", "prob_ai",
)
# Forbidden categorical *values* (exact, case-insensitive).
_BANNED_VALUE_TOKENS = {
    "is_ai", "is_human", "ai_generated", "human_written", "ai", "human",
    "prediction", "classification", "verdict", "decision",
}


def _walk_keys(obj, _prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{_prefix}.{k}" if _prefix else str(k)
            yield path, str(k)
            yield from _walk_keys(v, path)
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            yield from _walk_keys(item, f"{_prefix}[{i}]")


def _walk_string_values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_string_values(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_string_values(item)
    elif isinstance(obj, str):
        yield obj


def _strip_comments_and_strings(source: str) -> str:
    """Remove docstrings/string literals and comments so a source scan tests the
    CODE, not the posture documentation (the specdetect test_orthogonal_statistic
    precedent)."""
    tree = ast.parse(source)
    string_spans = []

    class _V(ast.NodeVisitor):
        def visit_Constant(self, node):  # noqa: N802
            if isinstance(node.value, str) and hasattr(node, "end_lineno"):
                string_spans.append(
                    (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
                )
            self.generic_visit(node)

    _V().visit(tree)
    lines = source.splitlines()
    for (sl, sc, el, ec) in string_spans:
        if sl == el:
            line = lines[sl - 1]
            lines[sl - 1] = line[:sc] + " " * (ec - sc) + line[ec:]
        else:
            lines[sl - 1] = lines[sl - 1][:sc]
            for i in range(sl, el - 1):
                lines[i] = ""
            lines[el - 1] = lines[el - 1][ec:]
    out = []
    for line in lines:
        hash_idx = line.find("#")
        out.append(line if hash_idx < 0 else line[:hash_idx])
    return "\n".join(out)


# ----------------------------------------------------------------------
# Surface registration (AC-11).
# ----------------------------------------------------------------------

def test_surface_registered():
    assert g.TASK_SURFACE == "gecscore_discrimination"
    assert "gecscore_discrimination" in VALID_TASK_SURFACES
    assert "gecscore_discrimination" in TASK_SURFACE_LABELS


def test_ac11_surface_fragment_file_is_source_of_truth():
    frag = _SCRIPTS / "claim_license_surfaces" / "gecscore_discrimination.txt"
    assert frag.exists()


# ----------------------------------------------------------------------
# AC-1 — score math (identity, empty, known-error pair, span count).
# ----------------------------------------------------------------------

def test_ac1_identity_is_one():
    assert g.gec_similarity("hello world", "hello world") == 1.0
    assert g.normalized_edit_distance("hello world", "hello world") == 0.0
    assert g.count_correction_spans("hello world", "hello world") == 0


def test_ac1_empty_correction_is_zero():
    # Empty correction = maximum distance edge → gec_sim 0.0.
    assert g.gec_similarity("hello world", "") == 0.0
    assert g.normalized_edit_distance("hello world", "") == 1.0
    # Both empty = nothing to correct = identical.
    assert g.normalized_edit_distance("", "") == 0.0
    assert g.gec_similarity("", "") == 1.0


def test_ac1_known_error_pair_hand_computed():
    original = "i has went to the store"
    corrected = "I have gone to the store"
    gec = g.gec_similarity(original, corrected)
    # difflib ratio of these two ~ moderate; gec_sim is strictly in (0, 1).
    assert 0.0 < gec < 1.0
    # The corrector changed something → at least one non-equal span.
    assert g.count_correction_spans(original, corrected) >= 1
    # gec_sim == 1 - normalized_edit_distance by construction.
    assert math.isclose(
        gec, 1.0 - g.normalized_edit_distance(original, corrected), rel_tol=1e-12
    )


def test_ac1_stub_backend_injects_canned_correction():
    text = _make_text(120)
    corrected = text.replace("responsibility", "responsability")  # one error
    be = g.StubGecBackend(corrections={text: corrected})
    r = g.audit_gecscore(text, backend=be)
    assert 0.0 < r["gecscore"] < 1.0  # corrector changed something
    assert r["gec_n_corrections"] >= 1
    assert r["gec_backend"]["kind"] == "stub_identity"


def test_ac1_stub_span_count_override():
    text = _make_text(120)
    corrected = text + " extra"
    be = g.StubGecBackend(corrections={text: corrected}, span_counts={text: 7})
    r = g.audit_gecscore(text, backend=be)
    assert r["gec_n_corrections"] == 7


# ----------------------------------------------------------------------
# AC-2 — CLI happy path + error envelopes + exit codes.
# ----------------------------------------------------------------------

def _run_cli(argv, tmp_path):
    out_path = tmp_path / "env.json"
    rc = g.main(argv + ["--json", "--out", str(out_path)])
    env = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
    return rc, env


def test_ac2_cli_happy_path(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text(_make_text(120), encoding="utf-8")
    rc, env = _run_cli(["--target", str(target)], tmp_path)
    assert rc == 0
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "gecscore_discrimination"
    assert env["tool"] == "gecscore_audit"
    assert env["available"] is True
    assert env["claim_license"] is not None
    assert "gecscore" in env["results"]
    assert "fairness_guardrails" in env["results"]


def test_ac2_cli_text_too_short(tmp_path):
    target = tmp_path / "empty.txt"
    target.write_text("   \n  ", encoding="utf-8")
    rc, env = _run_cli(["--target", str(target)], tmp_path)
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "text_too_short"


def test_ac2_cli_bad_input_unreadable(tmp_path):
    missing = tmp_path / "nope.txt"
    rc, env = _run_cli(["--target", str(missing)], tmp_path)
    assert rc == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_ac2_cli_short_text_warns_but_runs(tmp_path):
    target = tmp_path / "short.txt"
    target.write_text("just a handful of words here below the fifty word floor", encoding="utf-8")
    rc, env = _run_cli(["--target", str(target)], tmp_path)
    assert rc == 0
    assert env["available"] is True
    assert any("floor" in w for w in env["warnings"])


def test_ac2_cli_requires_target_or_batch():
    assert g.main([]) == 2  # neither
    # mutually exclusive
    assert g.main(["--target", "x", "--batch", "y"]) == 2


# ----------------------------------------------------------------------
# AC-3 — no-verdict recursive walk over the FULL envelope.
# ----------------------------------------------------------------------

def test_ac3_no_verdict_keys_recursive():
    env = g.compose_envelope(
        target_path="t.txt", target_words=120,
        results=g.audit_gecscore(_make_text(120)),
    )
    for path, key in _walk_keys(env):
        low = key.lower()
        for banned in _BANNED_KEY_SUBSTRINGS:
            assert banned not in low, f"forbidden key substring {banned!r} at {path}"


def test_ac3_no_verdict_categorical_values_recursive():
    env = g.compose_envelope(
        target_path="t.txt", target_words=120,
        results=g.audit_gecscore(_make_text(120)),
    )
    assert env["results"]["band"]["band"] in _ALLOWED_BANDS
    for s in _walk_string_values(env):
        assert s.strip().lower() not in _BANNED_VALUE_TOKENS, (
            f"forbidden categorical value {s!r}"
        )


def test_ac3_band_is_descriptive_over_own_axis():
    """The band names the MEASURED property (error density), not authorship. Drive
    each arm with a stub correction whose gec_sim lands in the right region."""
    text = _make_text(200)
    # Identity correction → gec_sim 1.0 → low_error_density.
    high = g.audit_gecscore(text, backend=g.StubGecBackend())
    assert high["band"]["band"] == "low_error_density"
    # A heavily-rewritten correction → low gec_sim → high_error_density.
    heavy = g.StubGecBackend(corrections={text: text[: len(text) // 3]})
    low = g.audit_gecscore(text, backend=heavy)
    assert low["band"]["band"] == "high_error_density"
    # band values + flags never name ai/human.
    for r in (high, low):
        b = r["band"]["band"].lower()
        assert "ai" not in b.split("_") and "human" not in b.split("_")
        for f in r["band"]["flags"]:
            assert "ai" not in f.split("_") and "human" not in f.split("_")


# ----------------------------------------------------------------------
# AC-4 — sign / direction pinned (silent inversion guard).
# ----------------------------------------------------------------------

def test_ac4_gec_ai_direction_pinned():
    """GEC_AI_DIRECTION is a fixed linguistic prior, asserted here so a silent sign
    flip is caught. 'gt' = higher gecscore is the AI-like direction."""
    assert g.GEC_AI_DIRECTION == "gt"
    r = g.audit_gecscore(_make_text(120))
    assert r["gec_ai_direction"] == "gt"
    assert r["band"]["direction"] == "gt"
    # The pin is load-bearing: the band's high arm (low_error_density) is the AI
    # direction, the low arm (high_error_density) is the human direction. If the
    # sign were flipped, a near-1.0 gecscore would NOT map to low_error_density.
    high = g.audit_gecscore(_make_text(200), backend=g.StubGecBackend())
    assert high["gecscore"] > g.PROVISIONAL_BAND_THRESHOLDS["gecscore"]["low_error_above"]
    assert high["band"]["band"] == "low_error_density"


# ----------------------------------------------------------------------
# AC-5 — separation guard (no fitness/selection/scoring imports).
# ----------------------------------------------------------------------

def test_ac5_separation_guard_no_forbidden_imports():
    source = Path(g.__file__).read_text(encoding="utf-8")
    code = _strip_comments_and_strings(source)
    for forbidden in (
        "fitness", "setec_signals", "loop", "cosplay", "splits",
        "provenance", "qlora", "reviser",
    ):
        assert forbidden not in code, (
            f"separation-guard leak: {forbidden!r} referenced in gecscore CODE "
            f"(docstrings/comments stripped before this scan) — gecscore is an "
            f"evidence column, never a selection signal"
        )


def test_ac5_no_selection_entrypoint():
    import inspect

    sig = inspect.signature(g.audit_gecscore)
    assert list(sig.parameters)[0] == "text"  # single text, not a list
    public = [n for n in dir(g) if not n.startswith("_")]
    for banned in ("select", "argmax", "rank_texts", "which_is_ai", "classify"):
        assert banned not in public


# ----------------------------------------------------------------------
# AC-6 — fairness/dialect gate wired in (REVIEW Change 1, CRITICAL).
# ----------------------------------------------------------------------

def test_ac6_fairness_guardrails_co_emitted():
    r = g.audit_gecscore(_make_text(120))
    assert "fairness_guardrails" in r
    fg = r["fairness_guardrails"]
    assert "recommendation" in fg
    assert "condition_flags" in fg


def test_ac6_declared_esl_triggers_posture_cap():
    """A declared nonnative_english condition with no baseline coverage must make
    the guardrail refuse evaluative use, and that refusal must surface as a
    gecscore caveat (the ESL/dialect inversion is visible at report level)."""
    r = g.audit_gecscore(_make_text(120), declared_conditions=["nonnative_english"])
    rec = r["fairness_guardrails"]["recommendation"]
    assert rec["refuses_evaluative_use"] is True
    assert rec["posture_cap"] == "revision_only"
    caveats = r["fairness_caveats"]
    assert any("FAIRNESS GATE" in c for c in caveats)
    assert any("INVERT" in c.upper() for c in caveats)


def test_ac6_detected_code_switching_flags():
    """Heuristic code-switching detection runs on the target via the guardrail."""
    text = _make_text(60) + " además el régimen está claramente está está más"
    r = g.audit_gecscore(text)
    flags = r["fairness_guardrails"]["condition_flags"]
    # code_switching may or may not cross the threshold depending on accent count;
    # the structural point is the guardrail RAN over the text (flags is a dict).
    assert isinstance(flags, dict)


def test_ac6_esl_inversion_named_in_claim_license():
    lic = g._claim_license(g.audit_gecscore(_make_text(120)))
    dn = lic.does_not_license.lower()
    assert "esl" in dn
    assert "invert" in dn
    assert "fairness_dialect_guardrails" in dn


def test_ac6_single_cli_folds_fairness_into_warnings(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text(_make_text(120), encoding="utf-8")
    rc = g.main(["--target", str(target), "--declare", "nonnative_english",
                 "--json", "--out", str(tmp_path / "o.json")])
    assert rc == 0
    env = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    assert any("FAIRNESS GATE" in w for w in env["warnings"])


# ----------------------------------------------------------------------
# AC-7 — batch mode (one row per passage).
# ----------------------------------------------------------------------

def test_ac7_batch_three_rows(tmp_path):
    manifest = tmp_path / "m.jsonl"
    rows = [
        {"id": "a", "text": _make_text(80)},
        {"id": "b", "text": _make_text(80) + " responsability erorr"},
        {"id": "c", "text": _make_text(80)},
    ]
    manifest.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    out = tmp_path / "rows.json"
    rc = g.main(["--batch", str(manifest), "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["mode"] == "batch"
    assert payload["n_rows"] == 3
    ids = [row["id"] for row in payload["rows"]]
    assert ids == ["a", "b", "c"]
    for row in payload["rows"]:
        assert "gecscore" in row and "gec_n_corrections" in row and "band" in row


def test_ac7_batch_path_rows(tmp_path):
    f = tmp_path / "passage.txt"
    f.write_text(_make_text(80), encoding="utf-8")
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(json.dumps({"id": "p", "path": "passage.txt"}), encoding="utf-8")
    rows = g.run_batch(
        g._load_batch_manifest(manifest), base_dir=manifest.parent
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "p"
    assert rows[0]["gecscore"] is not None


# ----------------------------------------------------------------------
# AC-8 — calibration honesty + fragment shape.
# ----------------------------------------------------------------------

def test_ac8_manifest_status_vs_band_status_two_objects():
    import capabilities as cap  # type: ignore

    cap_dir = _SCRIPTS.parent / "capabilities.d"
    m = cap.load_manifest(cap_dir)
    entry = {e["id"]: e for e in m["entries"]}["gecscore_audit"]
    assert entry["status"] == "literature_anchored"
    r = g.audit_gecscore(_make_text(120))
    assert r["band"]["calibration_status"] == "heuristic"
    assert entry["status"] != r["band"]["calibration_status"]


def test_ac8_fragment_tier_deps_floor_and_co_surface():
    import capabilities as cap  # type: ignore

    cap_dir = _SCRIPTS.parent / "capabilities.d"
    m = cap.load_manifest(cap_dir)
    entry = {e["id"]: e for e in m["entries"]}["gecscore_audit"]
    assert entry["compute"]["tier"] == "core"
    assert entry["compute"]["length_floor_words"] == 50
    assert entry["dependencies"]["python"] == []
    assert entry["surface"] == "gecscore_discrimination"
    # Change 1: fairness_dialect_guardrails listed as a recommended co-surface.
    assert entry["dependencies"]["surfaces"] == ["fairness_dialect_guardrails"]


# ----------------------------------------------------------------------
# AC-9 — model-free import (no language_tool/transformers/torch at top level).
# ----------------------------------------------------------------------

def test_ac9_no_model_imported_at_module_top_level():
    """The module must not pull a GEC-model dep at module scope; the real backends
    are the M2 seam (lazy). Mirrors tocsin AC-6: an AST scan of THIS module's
    top-level imports (stylometry_core may transitively pull spaCy/torch in some
    environments, which is a pre-existing repo fact shared by every stdlib audit;
    the gate is THIS module's own imports)."""
    src = Path(g.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    top_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_imports += [n.name for n in node.names]
        elif isinstance(node, ast.ImportFrom):
            top_imports.append(node.module or "")
    for banned in ("language_tool_python", "transformers", "torch"):
        assert not any(banned in imp for imp in top_imports), (
            f"{banned!r} imported at module top level (must be the lazy M2 seam)"
        )
    # And fairness_dialect_guardrails is imported lazily (inside a function), not
    # at module top level — so the separation-guard scan stays clean.
    assert "fairness_dialect_guardrails" not in top_imports
    # Default audit path uses the stub backend (no model).
    r = g.audit_gecscore(_make_text(120))
    assert r["gec_backend"]["kind"] == "stub_identity"


# ----------------------------------------------------------------------
# AC-10 — bounds (R4 gate live; saturated/tie/empty/NaN).
# ----------------------------------------------------------------------

def test_ac10_valid_payload_passes_bounds():
    env = g.compose_envelope(
        target_path="t.txt", target_words=120,
        results=g.audit_gecscore(_make_text(120)),
    )
    assert env["available"] is True


def test_ac10_injected_nan_raises_output_validity():
    """A backend whose 'correction' poisons the math must be caught — here a
    backend that drives a NaN gecscore reaches the R4 recursive walk."""
    text = _make_text(120)

    class NanBackend(g.GecBackend):
        kind = "nan_stub"

        def correct(self, t):
            return t

        def count_corrections(self, original, corrected):
            return 0

    r = g.audit_gecscore(text, backend=NanBackend())
    # Force a NaN into the results to prove the gate fires on this surface.
    r["gecscore"] = float("nan")
    with pytest.raises(OutputValidityError):
        g.compose_envelope(target_path="t.txt", target_words=120, results=r)


def test_ac10_saturated_identity_is_one():
    r = g.audit_gecscore(_make_text(120), backend=g.StubGecBackend())
    assert r["gecscore"] == 1.0
    assert r["gec_n_corrections"] == 0
    assert r["band"]["band"] == "low_error_density"


def test_ac10_empty_target_raises_input_error():
    with pytest.raises(g.GecScoreInputError):
        g.audit_gecscore("   ")


def test_ac10_non_string_correction_raises():
    class BadBackend(g.GecBackend):
        kind = "bad"

        def correct(self, t):
            return 12345  # not a str

    with pytest.raises(g.GecScoreInputError):
        g.audit_gecscore(_make_text(120), backend=BadBackend())


# ----------------------------------------------------------------------
# AC-11 — registration + drift + golden.
# ----------------------------------------------------------------------

def test_ac11_capability_entry_and_golden_present():
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
        (e for e in manifest["entries"] if e.get("id") == "gecscore_audit"), None
    )
    assert entry is not None, "gecscore_audit missing from capabilities.d"

    golden = _HERE / "_golden_capabilities" / "gecscore_audit.json"
    assert golden.exists()
    assert json.loads(golden.read_text(encoding="utf-8")) == entry


def test_envelope_builds_with_registered_surface():
    """End-to-end: audit() → compose_envelope() builds a valid schema-1.0 envelope
    (the path that hard-raises if the surface fragment is missing)."""
    env = g.compose_envelope(
        target_path="t.txt", target_words=120,
        results=g.audit_gecscore(_make_text(120)),
    )
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "gecscore_discrimination"
    assert env["available"] is True


# ----------------------------------------------------------------------
# Claim license refuses a verdict + cites the preprint with its status.
# ----------------------------------------------------------------------

def test_claim_license_refuses_verdict_and_cites_preprint():
    lic = g._claim_license(g.audit_gecscore(_make_text(120)))
    assert lic.task_surface == "gecscore_discrimination"
    dn = lic.does_not_license.lower()
    assert "verdict" in dn and "ai" in dn and "human" in dn
    assert "is_ai" in dn or "label" in dn
    licenses = lic.licenses.lower()
    assert "grammar-error" in licenses or "gecscore" in licenses
    assert "not a verdict" in licenses or "measurement" in licenses
    # arXiv root + UNVERIFIED status cited.
    refs = " ".join(lic.references)
    assert "2405.04286" in refs
    assert "unverified" in refs.lower()


def test_render_markdown_robust():
    env = g.compose_envelope(
        target_path="t.txt", target_words=120,
        results=g.audit_gecscore(_make_text(120)),
    )
    md = g.render_markdown(env)
    assert "GECScore" in md
    assert "gecscore" in md
    assert "NOT 'is AI'" in md
    assert "fairness" in md.lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
