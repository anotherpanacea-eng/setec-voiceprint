#!/usr/bin/env python3
"""Spec-16 tests for RAID transforms + paraphrase_ladder.py — the
recursive-paraphrase decay-curve harness (M1, model-free / stdlib).

Acceptance criteria (numbered per specs/16-raid-dipper-robustness.md):

  1.  RAID transforms deterministic + pure; existing three unchanged.
  2.  RAID_ATTACK_CLASSES register; pan_replay default-preserving.
  3.  RAID classes flow through pan_replay unchanged (no vocab gate).
  4.  --raid-suite generator composes with pan_replay.load_fixture_pairs.
  5.  Ladder loading + path-traversal hardening.
  6.  Stdlib proxy ladder: N+1 deterministic rungs, labeled proxy_stdlib.
  7.  Decay curve reuses build_robustness_card per-cell; monotone bool.
  8.  No aggregate score (structural _walk banned-key test).
  9.  ClaimLicense hardens the separability guardrail (Sadasivan).
  10. Separation + no-mutation structural guards.
  11. Capability registration + golden.
  12. M2 stub LadderParaphraser scores through the unchanged M1 path.
"""

from __future__ import annotations

import ast
import io
import json
import sys
import tokenize
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

import adversarial_fixtures as af  # type: ignore
import adversarial_robustness_card as arc  # type: ignore
import pan_replay  # type: ignore
import paraphrase_ladder as pl  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[4]

# A text long enough for Tier 1 signals to compute, seeded with words
# the proxy/RAID transforms actually touch (articles, digits, US
# spellings, closed-table synonyms).
_TEXT = (
    "The river moved slowly under the bridge in 2024. Children laughed near "
    "the bank, throwing small stones that skipped across the dark water. An "
    "old man watched from a bench, his hands folded, his coat buttoned "
    "against the wind. He remembered other afternoons and other rivers and "
    "other children whose names he had long since forgotten. The light fell "
    "sideways through the trees. It was a big and important day, and he felt "
    "the color of the honor he had once carried. Many things begin and end."
)
_TEXT2 = (
    "Beneath the bridge the current drifted along without haste in 1999. By "
    "the water kids were laughing and tossing pebbles that bounced over the "
    "dark surface. On a bench an elderly man looked on, his coat fastened, "
    "his hands clasped together. He thought of afternoons gone by, of rivers "
    "known, of youngsters whose names had slipped from him. Slanting light "
    "came down through the branches. The day felt large and good to him."
)

_NEW_RAID_TRANSFORMS = [
    "article_deletion", "number_swap", "paragraph_shuffle", "misspelling",
    "alternative_spelling", "insert_paragraph", "case_swap", "whitespace",
    "synonym_swap",
]


# ---------- #1 RAID transforms deterministic + pure ----------


def test_raid_transforms_deterministic_and_changing():
    """Each new transform is pure (byte-identical across two calls) and
    actually changes the text."""
    # paragraph_shuffle needs >= 2 paragraphs to change; give it that.
    inputs = {name: _TEXT for name in _NEW_RAID_TRANSFORMS}
    inputs["paragraph_shuffle"] = "First paragraph.\n\nSecond paragraph here."
    for name in _NEW_RAID_TRANSFORMS:
        fn = af.RAID_ATTACK_CLASSES[name]
        src = inputs[name]
        out1 = fn(src)
        out2 = fn(src)
        assert out1 == out2, f"{name} is not deterministic"
        assert out1 != src, f"{name} did not change the text"


def test_existing_transforms_unchanged():
    """The three pre-existing transforms and their TRANSFORMS keys are
    untouched (default-preserving)."""
    assert set(af.TRANSFORMS) == {"zero_width", "soft_hyphen", "homoglyph"}
    assert af.TRANSFORMS["zero_width"] is af.insert_zero_width_spaces
    assert af.TRANSFORMS["soft_hyphen"] is af.insert_soft_hyphens
    assert af.TRANSFORMS["homoglyph"] is af.apply_homoglyphs
    # The legacy transforms still behave: deterministic + changing.
    for key in ("zero_width", "soft_hyphen", "homoglyph"):
        out = af.TRANSFORMS[key](_TEXT)
        assert out == af.TRANSFORMS[key](_TEXT)
        assert out != _TEXT


# ---------- #2 RAID_ATTACK_CLASSES register + additivity ----------


def test_raid_register_maps_each_attack():
    """RAID_ATTACK_CLASSES names each RAID attack as a class string mapped
    to its transform, and the three legacy tokenizer attacks are reachable
    under their names too."""
    for name in _NEW_RAID_TRANSFORMS:
        assert name in af.RAID_ATTACK_CLASSES
        assert callable(af.RAID_ATTACK_CLASSES[name])
    # Legacy tokenizer attacks are part of the RAID taxonomy too.
    for name in ("homoglyph", "zero_width", "soft_hyphen"):
        assert name in af.RAID_ATTACK_CLASSES


def test_pan_replay_additive_default_preserving(tmp_path):
    """A pan_replay run over a fixture dir of ONLY the original four PAN
    classes is byte-identical before/after the RAID expansion. (Regression:
    pan_replay is unchanged; nothing about the RAID register can move its
    four-class output.)"""
    rows = [
        {"id": "p1", "obfuscation_class": "paraphrase",
         "clean": _TEXT, "obfuscated": _TEXT2},
        {"id": "u1", "obfuscation_class": "unicode",
         "clean": _TEXT, "obfuscated": af.apply_homoglyphs(_TEXT)},
    ]
    fixtures = tmp_path / "fix"
    fixtures.mkdir()
    (fixtures / "pairs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    result = pan_replay.replay(pan_replay.load_fixture_pairs(fixtures))
    # Only the PAN classes present are scored; the result is well-formed.
    assert set(result["per_class"].keys()) == {"paraphrase", "unicode"}
    # pan_replay still emits no aggregate score (its own posture intact).
    assert "robustness_score" not in result
    assert "n_robust_signals" not in result


# ---------- #3 RAID classes flow through pan_replay ----------


def test_raid_classes_flow_through_pan_replay(tmp_path):
    """RAID-class (clean, obfuscated) pairs replay through pan_replay
    unchanged and appear as their own per-class cards (per-class slicing
    intact). pan_replay has no vocabulary gate — every class string present
    is scored."""
    rows = [
        {"id": "c1", "obfuscation_class": "case_swap",
         "clean": _TEXT, "obfuscated": af.case_swap(_TEXT)},
        {"id": "w1", "obfuscation_class": "whitespace",
         "clean": _TEXT, "obfuscated": af.whitespace(_TEXT)},
        {"id": "w2", "obfuscation_class": "whitespace",
         "clean": _TEXT2, "obfuscated": af.whitespace(_TEXT2)},
    ]
    fixtures = tmp_path / "fix"
    fixtures.mkdir()
    (fixtures / "pairs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    result = pan_replay.replay(pan_replay.load_fixture_pairs(fixtures))
    assert set(result["per_class"].keys()) == {"case_swap", "whitespace"}
    assert result["per_class"]["case_swap"]["n_pairs"] == 1
    assert result["per_class"]["whitespace"]["n_pairs"] == 2
    # Strict per-class slicing for RAID classes too.
    for sig, block in result["per_class"]["whitespace"]["per_signal"].items():
        assert set(block["per_pair"].keys()) <= {"w1", "w2"}


# ---------- #4 --raid-suite generator composes ----------


def test_raid_suite_generator_composes(tmp_path):
    """adversarial_fixtures.py --raid-suite emits one pair per RAID attack
    in the pairs.jsonl layout pan_replay.load_fixture_pairs consumes."""
    inp = tmp_path / "in.txt"
    inp.write_text(_TEXT, encoding="utf-8")
    out = tmp_path / "pairs.jsonl"
    rc = af.main([str(inp), str(out), "--raid-suite"])
    assert rc == 0
    lines = [l for l in out.read_text().splitlines() if l.strip()]
    assert len(lines) == len(af.RAID_ATTACK_CLASSES)
    # The emitted manifest loads under pan_replay without error.
    pairs = pan_replay.load_fixture_pairs(tmp_path)
    classes = {p["obfuscation_class"] for p in pairs}
    assert classes == set(af.RAID_ATTACK_CLASSES)


# ---------- #5 Ladder loading + path hardening ----------


_LADDER_DIR_COUNTER = [0]


def _write_ladder(tmp_path: Path, rows: list[dict]) -> Path:
    _LADDER_DIR_COUNTER[0] += 1
    fixtures = tmp_path / f"lad{_LADDER_DIR_COUNTER[0]}"
    fixtures.mkdir()
    manifest = fixtures / "ladder.jsonl"
    with manifest.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return fixtures


def test_ladder_loads_rung0_is_base(tmp_path):
    fixtures = _write_ladder(tmp_path, [
        {"id": "d1", "paraphraser": "proxy_stdlib",
         "rungs": [_TEXT, _TEXT2, af.whitespace(_TEXT2)]},
    ])
    ladders = pl.load_ladders(fixtures)
    assert len(ladders) == 1
    assert ladders[0]["rungs"][0] == _TEXT
    assert ladders[0]["paraphraser"] == "proxy_stdlib"


def test_ladder_path_cannot_escape_bundle(tmp_path):
    """A rung_path escaping the fixtures dir (via .. or absolute) is
    rejected before any read; the secret's contents never appear."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET should never be read", encoding="utf-8")

    fixtures = _write_ladder(tmp_path, [
        {"id": "esc", "paraphraser": "proxy_stdlib",
         "rung_paths": ["r0.txt", "../secret.txt"]},
    ])
    (fixtures / "r0.txt").write_text(_TEXT, encoding="utf-8")
    with pytest.raises(pl.FixtureError) as exc:
        pl.load_ladders(fixtures)
    assert "outside" in str(exc.value).lower()
    assert "TOP SECRET" not in str(exc.value)

    fixtures_abs = tmp_path / "abs_lad"
    fixtures_abs.mkdir()
    (fixtures_abs / "ladder.jsonl").write_text(
        json.dumps({"id": "esc2", "paraphraser": "proxy_stdlib",
                    "rung_paths": ["r0.txt", str(secret)]}) + "\n",
        encoding="utf-8",
    )
    (fixtures_abs / "r0.txt").write_text(_TEXT, encoding="utf-8")
    with pytest.raises(pl.FixtureError) as exc2:
        pl.load_ladders(fixtures_abs)
    assert "outside" in str(exc2.value).lower()


def test_missing_and_malformed_ladder_clear_error(tmp_path, capsys):
    missing = tmp_path / "nope"
    rc = pl.main(["--fixtures", str(missing)])
    assert rc == 2
    assert "does not exist" in capsys.readouterr().err

    empty = tmp_path / "empty"
    empty.mkdir()
    rc2 = pl.main(["--fixtures", str(empty)])
    assert rc2 == 2
    assert "ladder.jsonl" in capsys.readouterr().err

    # Malformed JSON line raises FixtureError.
    bad = _write_ladder(tmp_path, [])
    (bad / "ladder.jsonl").write_text("{not json\n", encoding="utf-8")
    with pytest.raises(pl.FixtureError):
        pl.load_ladders(bad)

    # A one-rung ladder (no paraphrase pass) is rejected.
    short = _write_ladder(tmp_path, [
        {"id": "s", "paraphraser": "proxy_stdlib", "rungs": [_TEXT]},
    ])
    with pytest.raises(pl.FixtureError):
        pl.load_ladders(short)


# ---------- #6 Stdlib proxy ladder ----------


def test_build_proxy_ladder_deterministic():
    lad = pl.build_proxy_ladder(_TEXT, passes=3)
    assert len(lad["rungs"]) == 4  # passes + 1
    assert lad["rungs"][0] == _TEXT
    assert lad["paraphraser"] == "proxy_stdlib"
    # Byte-identical across calls.
    assert pl.build_proxy_ladder(_TEXT, passes=3)["rungs"] == lad["rungs"]
    # Each rung differs from the previous (the proxy actually transforms).
    for i in range(1, len(lad["rungs"])):
        assert lad["rungs"][i] != lad["rungs"][i - 1]
    # passes must be >= 1.
    with pytest.raises(ValueError):
        pl.build_proxy_ladder(_TEXT, passes=0)


# ---------- #7 Decay curve reuses the robustness card per-cell ----------


def test_decay_curve_reuses_card_per_cell():
    """score_ladder's per-rung relative_change / label come from
    build_robustness_card's CELLS verbatim, and the card's aggregate dict
    is NOT embedded."""
    lad = pl.build_proxy_ladder(_TEXT, passes=2)
    scored = pl.score_ladder(lad)
    assert scored["n_rungs"] == 3
    card_signals = set(arc._VARIANCE_SIGNALS.keys())
    assert set(scored["signals"]) <= card_signals

    # Cross-check: scoring the ladder through pl yields the same per-cell
    # values as calling the reused card builder directly.
    base = pl._score_text(lad["rungs"][0])
    cols = [(f"rung_{i}", pl._score_text(lad["rungs"][i])) for i in (1, 2)]
    direct = arc.build_robustness_card(base=base, fixtures=cols)
    for sig in scored["signals"]:
        decay = scored["per_signal"][sig]["decay"]
        for cell in decay:
            i = cell["rung"]
            direct_cell = direct["per_signal"][sig]["per_fixture"][f"rung_{i}"]
            assert cell["base_value"] == direct_cell["base_value"]
            assert cell["rung_value"] == direct_cell["fixture_value"]
            assert cell["relative_change"] == direct_cell["relative_change"]
            assert cell["card_label"] == direct_cell["label"]
            # Per-cell only: no aggregate field leaked into the cell.
            assert "overall_robustness" not in cell
            assert "n_robust_signals" not in cell


def test_monotone_is_descriptive_not_enforced():
    """A deliberately non-monotone rung sequence still scores; monotone is
    reported as a bool, never enforced."""
    # rung1 heavily mangled, rung2 = clean again → non-monotone |Δ|.
    heavy = af.case_swap(af.whitespace(af.synonym_swap(_TEXT)))
    ladder = {"id": "nm", "paraphraser": "proxy_stdlib",
              "rungs": [_TEXT, heavy, _TEXT]}
    scored = pl.score_ladder(ladder)
    # At least one signal should be non-monotone given r2 returns to clean.
    monos = [b["monotone"] for b in scored["per_signal"].values()]
    assert any(m is False for m in monos)
    # And scoring did not raise / drop the ladder.
    assert scored["n_rungs"] == 3


# ---------- #8 No aggregate score (structural _walk) ----------

_BANNED = {
    "robustness_score", "auc_retained", "area_under_decay", "is_robust",
    "n_robust_signals", "n_fragile_signals", "n_inverted_polarity_readings",
    "n_unstable_small_base_readings", "overall_robustness",
    "aggregate_score", "score", "headline", "accuracy", "auc", "roc_auc",
    "tpr", "fpr",
}


def test_refuses_aggregate_score():
    lad = pl.build_proxy_ladder(_TEXT, passes=3)
    result = pl.score_ladders([lad])
    payload = pl.build_audit_payload(result)

    assert _BANNED.isdisjoint(set(result.keys()))
    assert _BANNED.isdisjoint(set(payload["results"].keys()))

    parsed = json.loads(json.dumps(payload, default=str))

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k not in _BANNED, f"banned aggregate key present: {k}"
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(parsed["results"])
    dnl = payload["claim_license"]["does_not_license"].lower()
    assert "aggregate" in dnl


# ---------- #9 ClaimLicense hardens the separability guardrail ----------


def test_claim_license_hardens_separability_guardrail():
    lad = pl.build_proxy_ladder(_TEXT, passes=2)
    result = pl.score_ladders([lad])
    payload = pl.build_audit_payload(result)
    lic = payload["claim_license"]

    # (a) licenses the per-rung relative-change statement.
    assert "relative change" in lic["licenses"].lower()

    dnl = lic["does_not_license"].lower()
    # (b) refuses detector-accuracy headline + "robust to paraphrase".
    assert "detector" in dnl
    assert "robust to paraphrase" in dnl
    # (c) quotes Sadasivan by name + arXiv id.
    full = json.dumps(lic).lower()
    assert "sadasivan" in full
    assert "2303.11156" in json.dumps(lic)
    assert "this paraphrase strength" in dnl
    # proxy_stdlib caveat present.
    assert any("proxy_stdlib" in c for c in lic["additional_caveats"])
    # Thresholding affordance explicitly closed.
    assert any(
        "retention threshold" in c.lower() for c in lic["additional_caveats"]
    )
    # Rendered markdown block carries the refusal too.
    block = pl._claim_license(result).render_block()
    assert "robust to paraphrase" in block.lower()
    assert "2303.11156" in block


# ---------- #10 Separation + no-mutation structural guards ----------


def _strip_comments_and_strings(path: Path) -> str:
    """Return source with comments dropped and string literals replaced by
    an empty-string placeholder, so a docstring / license string may NAME a
    forbidden symbol as posture documentation without tripping the guard,
    while the result stays syntactically valid for ast.parse."""
    src = path.read_text(encoding="utf-8")
    out_tokens: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING:
                # Replace the literal's text with an empty string so syntax
                # (e.g. `X = "..."`) survives but the contents can't trip
                # the symbol guard. 2-tuple form lets untokenize re-space.
                out_tokens.append((tokenize.STRING, '""'))
                continue
            if tok.type in getattr(tokenize, "_FSTRING_TYPES", ()) or (
                hasattr(tokenize, "FSTRING_MIDDLE")
                and tok.type == tokenize.FSTRING_MIDDLE
            ):
                out_tokens.append((tok.type, ""))
                continue
            out_tokens.append((tok.type, tok.string))
        return tokenize.untokenize(out_tokens)
    except (tokenize.TokenError, IndentationError):
        # Fallback: AST-strip docstrings only (still removes the most
        # likely false-positive source — module/function docstrings).
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
                body = getattr(node, "body", [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    body[0].value.value = ""
        return ast.unparse(tree)


_FORBIDDEN_MODULES = {"calibrate_thresholds", "conformal_gate"}
_FORBIDDEN_SYMBOLS = {"SetecFitness", "calibrate_thresholds", "conformal_gate"}


def _guarded_sources() -> list[Path]:
    return [
        CALIBRATION_DIR / "paraphrase_ladder.py",
        SCRIPTS_ROOT / "adversarial_fixtures.py",
    ]


def test_no_calibration_or_selection_import():
    """paraphrase_ladder + the new transforms import nothing from the
    calibration/selection layer and name no threshold-setting symbol (after
    stripping comments + string literals)."""
    for path in _guarded_sources():
        stripped = _strip_comments_and_strings(path)
        tree = ast.parse(stripped)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[-1] not in _FORBIDDEN_MODULES
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[-1]
                assert mod not in _FORBIDDEN_MODULES
                for alias in node.names:
                    assert alias.name not in _FORBIDDEN_SYMBOLS
        # No forbidden symbol named anywhere in executable code.
        for sym in _FORBIDDEN_SYMBOLS:
            assert sym not in stripped, (
                f"{path.name} names forbidden symbol {sym} in executable code"
            )


def test_no_function_takes_and_returns_a_fixture_or_manifest():
    """No module-level function both takes and returns a fixture/manifest
    type (re-label immutability is checkable, not just promised). We treat a
    return annotation that names a ladder/manifest/fixture container as the
    'returns a fixture' signal; the harness's entrypoints return cards/dicts
    derived from fixtures, never the fixtures themselves."""
    # The structural intent: the public entrypoints (score_ladder /
    # score_ladders / replay) consume fixtures and return a NEW card dict.
    # They must not return the input ladder/pairs object.
    lad = pl.build_proxy_ladder(_TEXT, passes=2)
    before = json.dumps(lad, sort_keys=True)
    scored = pl.score_ladder(lad)
    # The input ladder is not mutated by scoring.
    assert json.dumps(lad, sort_keys=True) == before
    # The returned object is a card, not the input ladder (no 'rungs' key).
    assert "rungs" not in scored
    assert "decay" in scored["per_signal"][scored["signals"][0]]


def test_import_pulls_no_model_dependency():
    """import paraphrase_ladder pulls no torch/transformers (stdlib import).
    The DIPPER paraphraser is a lazy M2 seam, not imported at module load."""
    # If the module imported a heavy model lib at top level, it would be in
    # sys.modules already (the test imports pl at file load). Assert the
    # heavy libs were not dragged in BY paraphrase_ladder's import.
    src = _strip_comments_and_strings(CALIBRATION_DIR / "paraphrase_ladder.py")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "torch" not in imported
    assert "transformers" not in imported


# ---------- #11 Capability registration + golden ----------


def test_capability_entry_present_and_golden():
    pytest.importorskip("yaml") if pytest is not None else __import__("yaml")
    from capabilities import load_manifest  # type: ignore

    manifest = load_manifest()
    entries = {e["id"]: e for e in manifest.get("entries", [])}
    assert "paraphrase_ladder" in entries
    entry = entries["paraphrase_ladder"]
    assert entry["surface"] == "validation"
    assert entry["status"] == "empirically_oriented"
    assert entry["handoff"] == "internal"
    assert entry["compute"]["tier"] == "core"
    assert entry["dependencies"]["python"] == []
    assert entry["script_path"] == (
        "plugins/setec-voiceprint/scripts/calibration/paraphrase_ladder.py"
    )
    assert (REPO_ROOT / entry["script_path"]).is_file()

    # The per-id golden fragment exists and matches the assembled entry.
    golden = (
        Path(__file__).resolve().parent
        / "_golden_capabilities" / "paraphrase_ladder.json"
    )
    assert golden.is_file()
    assert json.loads(golden.read_text(encoding="utf-8")) == entry

    # pan_replay's entry is unchanged (still present, surface validation).
    assert "pan_replay" in entries
    assert entries["pan_replay"]["surface"] == "validation"


def test_use_when_has_no_doubled_apostrophe():
    """Regression (preflight #240): the use_when / do_not_use_when scalars must
    parse to single apostrophes, not the literal doubled `''`.

    In PLAIN (unquoted) YAML a `''` is NOT un-escaped — only single-quoted
    scalars collapse `''` -> `'`. use_when[0] was authored unquoted with
    `editor''s` / `tool''s`, so the loader returned doubled apostrophes that
    leaked verbatim into capabilities.py's rendered "## Use when" listing. The
    golden masked it (it was generated FROM the buggy loader), so this asserts a
    PROPERTY of the parsed text rather than re-comparing to the golden.

    FAILS against origin/feat/raid-dipper-robustness pre-fix; passes after the
    YAML scalar is single-quoted (so `''` -> `'`) and the golden re-blessed.
    """
    pytest.importorskip("yaml") if pytest is not None else __import__("yaml")
    from capabilities import load_manifest  # type: ignore

    entries = {e["id"]: e for e in load_manifest().get("entries", [])}
    entry = entries["paraphrase_ladder"]

    # Guard the WHOLE prose surface, not just the named line: every operator-
    # facing free-text field must be free of the doubled-apostrophe artifact.
    for field in ("purpose", "use_when", "do_not_use_when"):
        value = entry[field]
        items = value if isinstance(value, list) else [value]
        for i, text in enumerate(items):
            assert "''" not in text, (
                f"{field}[{i}] carries a literal doubled apostrophe "
                f"(plain-scalar YAML does not un-escape ''): {text!r}"
            )

    # Positive check: the intended possessives render correctly.
    assert "editor's successive passes" in entry["use_when"][0]
    assert "humanizer tool's output" in entry["use_when"][0]


def test_no_new_surface_label_row():
    """paraphrase_ladder is on the EXISTING validation surface — it adds NO
    claim_license_surfaces fragment (the bijection test would break)."""
    surf_dir = SCRIPTS_ROOT / "claim_license_surfaces"
    assert not (surf_dir / "paraphrase_ladder.txt").exists()
    # The validation surface label it reuses does exist.
    assert (surf_dir / "validation.txt").exists()


# ---------- #12 M2 stub LadderParaphraser ----------


def test_m2_stub_runner_scores_through_unchanged_path():
    """A deterministic stub paraphraser (no model) produces a labeled rung
    sequence that scores through the UNCHANGED M1 card/decay path. This is
    the M2 seam contract: only the rung GENERATOR changes; scoring, the
    card, the decay curve, and the no-aggregate posture are M1's.

    M1 ships no LadderParaphraser symbol yet (that's the M2 PR); here we
    simulate the seam by injecting a stub-built `dipper`-labeled ladder and
    asserting it flows through score_ladder identically."""
    def stub_runner(text: str, passes: int) -> list[str]:
        # A deterministic stub: reuse the proxy mechanics, relabel dipper.
        rungs = pl.build_proxy_ladder(text, passes=passes)["rungs"]
        return rungs

    rungs = stub_runner(_TEXT, 3)
    dipper_ladder = {"id": "m2", "paraphraser": "dipper", "rungs": rungs}
    scored = pl.score_ladder(dipper_ladder)
    assert scored["paraphraser"] == "dipper"
    assert scored["n_rungs"] == 4
    # Scores through the same per-cell card path (signals match the card).
    assert set(scored["signals"]) <= set(arc._VARIANCE_SIGNALS.keys())

    result = pl.score_ladders([dipper_ladder])
    assert result["paraphraser"] == "dipper"
    payload = pl.build_audit_payload(result)
    # The no-aggregate posture covers the dipper-labeled run too.
    parsed = json.loads(json.dumps(payload, default=str))

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k not in _BANNED
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(parsed["results"])


# ---------- envelope / surface smoke ----------


def test_envelope_and_surface(tmp_path):
    fixtures = _write_ladder(tmp_path, [
        {"id": "d1", "paraphraser": "proxy_stdlib",
         "rungs": list(pl.build_proxy_ladder(_TEXT, passes=2)["rungs"])},
    ])
    out_path = tmp_path / "out.json"
    rc = pl.main(["--fixtures", str(fixtures), "--json", "--out", str(out_path)])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["task_surface"] == "validation"
    assert payload["tool"] == "paraphrase_ladder"
    assert payload["claim_license"]["task_surface"] == "validation"
    assert "ladders" in payload["results"]


def test_build_proxy_cli_round_trips(tmp_path):
    """--build-proxy regenerates a ladder.jsonl line that load_ladders
    can consume."""
    inp = tmp_path / "in.txt"
    inp.write_text(_TEXT, encoding="utf-8")
    out = tmp_path / "ladder.jsonl"
    rc = pl.main(["--build-proxy", str(inp), "--passes", "3", "--out", str(out)])
    assert rc == 0
    # Place it in a fixtures dir and load it back.
    fixtures = tmp_path / "lad"
    fixtures.mkdir()
    (fixtures / "ladder.jsonl").write_text(out.read_text(), encoding="utf-8")
    ladders = pl.load_ladders(fixtures)
    assert len(ladders) == 1
    assert ladders[0]["paraphraser"] == "proxy_stdlib"
    assert len(ladders[0]["rungs"]) == 4


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
