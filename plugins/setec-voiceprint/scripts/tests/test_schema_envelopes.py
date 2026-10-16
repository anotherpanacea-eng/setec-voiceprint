#!/usr/bin/env python3
"""Pins the schema_version 1.0 envelope contract across the audit surfaces.

Every envelope here is real module output. Most surfaces run their own
CLI ``--json`` path on small stdlib-only fixtures; the rest call the
audit function and the module's ``build_audit_payload``/``render_json``.
Where the real pipeline needs a model, the test supplies only the model's
output and everything downstream is real: POS-bigram counts stand in for
spaCy tagging in the two bigram diffs, and a fixed series stands in for
the causal LM in surprisal_audit.

The checks every surface shares (top-level keys, identity, claim license,
results keys, target words) run once per surface via parametrize.
Surface-specific behaviour follows: unavailable paths, baseline blocks,
ai_status routing, warnings, and the voice-profile privacy gates.

Not covered here: aesthetic_authority_audit, pov_voice_profile and
voice_drift_tracker. Their real pipelines load spaCy (and Brysbaert data),
so the only cheap fixture was a hand-built result dict, which pinned
nothing. Their own test files cover their envelopes.

Also not here: test_output_schema.py (tests the shared output_schema
library directly) and the two *_feature_schema.py taxonomy tests.
"""

from __future__ import annotations

import contextlib
import functools
import io
import json
import sys
import types
from collections import Counter
from pathlib import Path

import pytest

import agency_abstraction_audit as aaa
import aic_pattern_audit as aic
import bigram_diff as bd
import chapter_distinctiveness_audit as cda
import check_corpus as cc
import construction_signature_audit as csa
import controls_audit as ca
import discourse_move_signature as dms
import function_word_grammar_audit as fwg
import idiolect_detector as idd
import kicker_density as kd
import known_editor_profile as kep
import manifest_validator as mv
import manuscript_audit as ma
import manuscript_bigram_diff as mbd
import manuscript_repetition_audit as mra
import mimicry_cosplay_audit as mca
import paragraph_audit as pa
import phraseological_signature_audit as psa
import punctuation_cadence_audit as pca
import repetition_audit as ra
import stance_modality_audit as sma
import surprisal_audit as sa
import variance_audit as va
import voice_distance as vd
import voice_profile as vp


REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})

CLAIM_LICENSE_KEYS = frozenset({
    "task_surface", "licenses", "does_not_license", "comparison_set",
    "length_range_words", "register_match", "language_match",
    "fpr_target", "confidence_interval_95", "additional_caveats",
    "references",
})

RENDERED_HEADER = "## What this result licenses"


# ---------------------------------------------------------------------------
# Fixture text. One passage carries discourse, stance, punctuation, agency
# and construction markers so every text audit has something to measure.
# ---------------------------------------------------------------------------

SAMPLE = (
    "First, the committee reviewed the proposal. However, the timeline "
    "remained ambiguous. Moreover, the budget needed revisiting. In "
    "contrast, the original deadline was firm. Therefore, the schedule "
    "was extended. For example, the implementation phase was pushed back. "
    "In short, the project survived.\n\n"
    "The committee may consider the proposal. Members must review the "
    "timeline. Clearly the budget is constrained. Possibly the deadline "
    "could be extended. We believe the team can deliver. It seems "
    "reasonable to defer. To be honest, the timeline is tight.\n\n"
    "The committee — meeting briefly — endorsed the proposal. Members "
    "reviewed the timeline; some objected. The room (crowded, warm) "
    "deliberated. \"What now?\" asked the chair. To be clear: scope "
    "matters. Did the team consider alternatives? They did, twice.\n\n"
    "There is a draft. What matters is the voice. The proposal was "
    "reviewed and the timeline was extended. Implementation will commence "
    "following authorization. Although tired, Daria signed the agreement "
    "on Tuesday. On the one hand, the prose is fine. On the other hand, "
    "it could improve.\n\n"
    "Onward.\n\n"
) * 4

BASELINE_TEXTS = {
    "a.md": (
        "The committee deliberated through the afternoon. The room was "
        "warm, and the chair kept the agenda short. Daria signed the "
        "agreement on Tuesday after a long review.\n\n"
    ) * 12,
    "b.md": (
        "Members reviewed the budget on Tuesday. The proposal landed after "
        "lunch, and Maria called the vendor twice before the vote.\n\n"
    ) * 12,
    "c.md": (
        "The river ran quick past the mill. The boat tilted at the bend, "
        "and brackish water lapped the hull while the crew waited.\n\n"
    ) * 12,
}

CHAPTERS = [
    ("Chapter 1", "The forge glowed. Iron sang under the hammer. The smith "
                  "counted his blows. The forge glowed brighter. " * 8),
    ("Chapter 2", "The river ran quick. The boat tilted at the bend. Brackish "
                  "water lapped the hull. The bend opened wide. " * 8),
    ("Chapter 3", "The committee deliberated. The proposal landed. The budget "
                  "contracted. Daria signed off on the timeline. " * 8),
]


def _cli(module, *argv) -> tuple[int, str, str]:
    """Run a surface's main() with patched argv; return (rc, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    orig = sys.argv
    sys.argv = ["prog", *map(str, argv)]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = module.main()
    finally:
        sys.argv = orig
    return rc, out.getvalue(), err.getvalue()


def _cli_json(module, *argv) -> dict:
    _rc, out, err = _cli(module, *argv)
    assert out.strip(), f"{module.__name__} wrote no JSON; stderr: {err[-500:]}"
    return json.loads(out)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("envelopes")
    base = root / "baseline"
    base.mkdir()
    for name, text in BASELINE_TEXTS.items():
        (base / name).write_text(text, encoding="utf-8")
    (root / "target.md").write_text(SAMPLE, encoding="utf-8")
    (root / "manuscript.md").write_text(
        "".join(f"# {label}\n\n{text}\n\n" for label, text in CHAPTERS),
        encoding="utf-8",
    )
    ref = root / "reference"
    ref.mkdir()
    (ref / "r.md").write_text(SAMPLE, encoding="utf-8")
    # A baseline with one entry that cannot be read (a directory named
    # like a text file), to drive the skipped-file warnings.
    broken = root / "broken_baseline"
    broken.mkdir()
    (broken / "a.md").write_text(BASELINE_TEXTS["a.md"], encoding="utf-8")
    (broken / "locked.md").mkdir()
    rows = [
        {"id": name[0], "path": f"baseline/{name}", "use": ["baseline"],
         "register": "blog_essay", "ai_status": "pre_ai_human"}
        for name in BASELINE_TEXTS
    ]
    (root / "manifest.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8",
    )
    # Edit pairs for known_editor_profile: the "after" drops intensifiers.
    pairs = []
    for i, text in enumerate(BASELINE_TEXTS.values()):
        before = text.replace("warm,", "very warm indeed,").replace(
            "landed", "really landed").replace("quick", "very quick")
        (root / f"before_{i}.md").write_text(before, encoding="utf-8")
        (root / f"after_{i}.md").write_text(text, encoding="utf-8")
        pairs.append({"before": str(root / f"before_{i}.md"),
                      "after": str(root / f"after_{i}.md")})
    (root / "pairs.json").write_text(json.dumps(pairs), encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# Per-surface builders. Each returns the real envelope for one surface.
# ---------------------------------------------------------------------------

def _text_cli(module):
    return lambda c: _cli_json(module, c / "target.md", "--json")


def _aic(c):
    return _cli_json(aic, c / "target.md", "--json")


def _chapter_distinctiveness(_c):
    result = cda.audit_chapter_distinctiveness(
        [{"label": label, "text": text} for label, text in CHAPTERS],
        function_words=set(), anchor_words=set(),
        min_count=2, min_word_len=4, cluster_window=300, min_ratio=1.0,
    )
    return cda.build_audit_payload(result, target_path=Path("manuscript.md"))


def _controls(c, baseline=True):
    report = ca.run_controls_audit(
        questioned_text="The team has produced a draft. There are concerns about scope.",
        baseline_texts=list(BASELINE_TEXTS.values()) if baseline else [],
        negative_control_text="Daria and Maria reviewed the deliverable. The dashboard shows progress.",
        positive_control_text=(
            "We must consider whether the strategic alignment can be achieved "
            "within the parameters established by the framework."
        ),
    )
    return ca.build_audit_payload(report, target_path=Path("questioned.md"))


CONSTRUCTION_SAMPLE = (
    "There is a draft. What matters is the voice. It is important to revise. "
    "Although tired, she continued. Despite the timeline, the team produced a "
    "draft. To begin with, the framing helped. From the outset, the budget "
    "constrained the scope."
) * 3


@functools.lru_cache(maxsize=1)
def _construction_detected():
    # spaCy parses each sentence when it is installed: short text, run once.
    return csa.detect_constructions(CONSTRUCTION_SAMPLE)


def _construction(_c, baseline_density=None, baseline_loaded=()):
    results, n_words = _construction_detected()
    return csa.build_audit_payload(csa.build_audit(
        target_path=Path("draft.md"), target_text=CONSTRUCTION_SAMPLE,
        target_results=results, target_words=n_words,
        baseline_density_per_1k=baseline_density, baseline_loaded=list(baseline_loaded),
        baseline_skipped=[], baseline_words=0 if baseline_density is None else 5000,
        top=10, construction_filter=None, include_baseline_filenames=False,
    ))


def _kicker(_c):
    block = kd.kicker_density(SAMPLE, nlp=None)
    return kd.build_audit_payload(block, target_path=Path("draft.md"), text=SAMPLE)


IDIOLECT = {"preservation_list": [
    {"phrase": "the committee", "score": 1.2},
    {"phrase": "the proposal", "score": 1.1},
]}


def _mimicry(_c, ai_status=None):
    kw = {"target_ai_status": ai_status} if ai_status else {}
    audit = mca.audit_cosplay(
        target_text=SAMPLE, idiolect=IDIOLECT, voice_distance=None, variance=None, **kw,
    )
    return mca.build_audit_payload(audit, target_path=Path("draft.md"), target_text=SAMPLE)


def _surprisal_series(text, **kwargs):
    # Stands in for the causal LM: a deterministic per-token surprisal series.
    return [4.0, 4.5, 4.2, 4.8, 4.1, 4.6, 4.3, 4.7, 4.0, 4.4] * 10


def _surprisal(_c, text=SAMPLE):
    audit = sa.audit_surprisal(text, score_fn=_surprisal_series)
    audit["backend"] = {"id": "stub-model", "revision": "stub"}
    return sa.build_audit_payload(audit, target_path=Path("draft.txt"))


# POS-bigram counts stand in for spaCy tagging; the diffs and the envelope
# are computed by the real module functions.
TARGET_BIGRAMS = {"NOUN_VERB": 50, "DET_NOUN": 40, "ADJ_NOUN": 30}
CLUSTER_BIGRAMS = {"NOUN_VERB": 30, "DET_NOUN": 60, "ADJ_NOUN": 20, "ADP_DET": 10}


def _bigram_diff(_c, skipped=()):
    total = sum(CLUSTER_BIGRAMS.values())
    return json.loads(bd.render_json(
        target_path=Path("draft.txt"),
        target_counts=TARGET_BIGRAMS,
        cluster_loaded=[Path("a.txt"), Path("b.txt")],
        cluster_skipped=list(skipped),
        pooled_diff=bd.diff_pooled(TARGET_BIGRAMS, CLUSTER_BIGRAMS, alpha=1.0, min_count=1),
        mean_diff=bd.diff_mean(
            TARGET_BIGRAMS, {k: v / total for k, v in CLUSTER_BIGRAMS.items()}, min_count=1,
        ),
        top=10, alpha=1.0, min_count=1,
    ))


def _manuscript_bigram_diff(_c, a_skipped=()):
    a, b = Counter(TARGET_BIGRAMS), Counter(CLUSTER_BIGRAMS)
    return json.loads(mbd.render_json(
        a_label="hamilton", b_label="madison",
        a_loaded=[Path("a/1.txt"), Path("a/2.txt")],
        b_loaded=[Path("b/1.txt"), Path("b/2.txt"), Path("b/3.txt")],
        a_skipped=list(a_skipped), b_skipped=[],
        pooled_diff=mbd.diff_pooled_corpora(a, b, alpha=1.0, min_count=1),
        mean_diff=None,
        top=10, alpha=1.0, min_count=1,
    ))


def _known_editor(c):
    profile = c / "editor_profile.json"
    if not profile.exists():
        rc, _out, err = _cli(kep, "learn", "--pairs-json", c / "pairs.json",
                             "--profile-label", "test_editor", "--out", profile)
        assert rc == 0, err
    return _cli_json(kep, "match", "--before", c / "before_0.md",
                     "--after", c / "after_0.md", "--profile", profile, "--json")


# name -> (module, expected task_surface, builder, results keys that must be present)
SURFACES = {
    "agency_abstraction_audit": (
        aaa, "smoothing_diagnosis", _text_cli(aaa),
        {"raw_counts", "densities_per_1k", "entity_to_action_ratio", "compression"}),
    "aic_pattern_audit": (aic, "craft_restoration", _aic, {"patterns"}),
    "bigram_diff": (
        bd, "smoothing_diagnosis", _bigram_diff,
        {"target_bigrams", "target_unique", "diffs"}),
    "chapter_distinctiveness_audit": (
        cda, "smoothing_diagnosis", _chapter_distinctiveness, {"n_chapters", "chapters"}),
    "check_corpus": (
        cc, "validation", lambda c: _cli_json(cc, "--dir", c / "baseline", "--json"),
        {"status", "n_files", "thresholds"}),
    "construction_signature_audit": (
        csa, "voice_coherence", _construction,
        {"constructions"}),
    "controls_audit": (
        ca, "voice_coherence", _controls,
        {"questioned", "negative_control", "positive_control", "classification"}),
    "discourse_move_signature": (
        dms, "smoothing_diagnosis", _text_cli(dms),
        {"category_counts", "category_densities_per_1k", "total_marker_density_per_1k",
         "move_sequence", "move_sequence_bigrams", "move_sequence_entropy_bits",
         "marked_only_entropy_bits", "relation_distribution", "compression"}),
    "function_word_grammar_audit": (
        fwg, "voice_coherence", _text_cli(fwg),
        {"n_function_words", "function_word_ratio", "function_bigrams",
         "function_bigram_entropy_bits", "preposition_counts", "preposition_entropy_bits",
         "subordinator_counts", "auxiliary_chain_count", "pronoun_transition", "compression"}),
    "idiolect_detector": (
        idd, "voice_coherence",
        lambda c: _cli_json(idd, "--target-dir", c / "baseline", "--reference-dir",
                            c / "reference", "--json", "--allow-public-output"),
        {"method", "rankings", "preservation_list"}),
    "kicker_density": (
        kd, "smoothing_diagnosis", _kicker,
        {"signal_path", "family", "value", "spacing_variance", "paragraphs", "diagnostics"}),
    "known_editor_profile": (
        kep, "validation", _known_editor, {"verdict", "profile_label", "per_signal"}),
    "manifest_validator": (
        mv, "validation", lambda c: _cli_json(mv, c / "manifest.jsonl", "--json"),
        {"n_entries", "n_warnings", "issues", "summary"}),
    "manuscript_audit": (
        ma, "smoothing_diagnosis",
        lambda c: _cli_json(ma, c / "manuscript.md", "--no-tier2", "--no-tier3", "--json"),
        {"chapters"}),
    "manuscript_bigram_diff": (
        mbd, "smoothing_diagnosis", _manuscript_bigram_diff, {"label_a", "label_b", "diffs"}),
    "manuscript_repetition_audit": (
        mra, "smoothing_diagnosis",
        lambda c: _cli_json(mra, c / "manuscript.md", "--baseline-dir", c / "baseline", "--json"),
        {"n_chapters", "chapters", "aggregated"}),
    "mimicry_cosplay_audit": (
        mca, "voice_coherence", _mimicry,
        {"idiolect_survival", "voice_distance", "pos_bigram_kl", "verdict", "shapes",
         "thresholds_used"}),
    "paragraph_audit": (
        pa, "smoothing_diagnosis", _text_cli(pa),
        {"n_paragraphs", "paragraph_word_counts", "length_summary", "rhythm_signals",
         "compression"}),
    "phraseological_signature_audit": (
        psa, "voice_coherence", lambda c: _cli_json(psa, c / "target.md", "--json"),
        {"categories"}),
    "punctuation_cadence_audit": (
        pca, "voice_coherence", _text_cli(pca),
        {"n_sentence_final", "raw_counts", "densities_per_1k", "sentence_final_distribution",
         "interruption_grammar", "punctuation_bigrams", "comma_period_share", "compression"}),
    "repetition_audit": (
        ra, "smoothing_diagnosis",
        lambda c: _cli_json(ra, c / "target.md", "--baseline-dir", c / "baseline", "--json"),
        {"candidates"}),
    "stance_modality_audit": (
        sma, "voice_coherence", _text_cli(sma),
        {"category_counts", "category_densities_per_1k", "total_marker_density_per_1k",
         "stance_entropy_bits", "hedge_booster_ratio", "compression"}),
    "surprisal_audit": (
        sa, "smoothing_diagnosis", _surprisal,
        {"n_tokens_scored", "series_length", "summary", "top_k_tokens", "sliding_window",
         "band", "backend"}),
    "variance_audit": (
        va, "smoothing_diagnosis",
        lambda c: _cli_json(va, c / "target.md", "--no-tier2", "--no-tier3", "--json"),
        {"audit", "compression"}),
    "voice_distance": (
        vd, "voice_coherence",
        lambda c: _cli_json(vd, c / "target.md", "--baseline-dir", c / "baseline",
                            "--no-spacy", "--json"),
        {"overall", "families", "register_match"}),
    "voice_profile": (
        vp, "voice_coherence",
        lambda c: _cli_json(vp, "--baseline-dir", c / "baseline", "--no-spacy", "--json",
                            "--allow-public-output"),
        {"baseline_summary", "selected_features", "families"}),
}

NAMES = sorted(SURFACES)


@pytest.fixture(scope="module")
def envelope(corpus):
    cache: dict[str, dict] = {}

    def get(name: str) -> dict:
        if name not in cache:
            cache[name] = SURFACES[name][2](corpus)
        return cache[name]

    return get


# ---------------------------------------------------------------------------
# Shared contract, once per surface.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_envelope_header(name, envelope):
    module, task_surface, _build, _keys = SURFACES[name]
    env = envelope(name)
    # Exact key set: also rules out any legacy key left at the top level.
    assert set(env) == REQUIRED_TOP_LEVEL_KEYS
    assert env["schema_version"] == "1.0"
    assert env["tool"] == name
    assert env["version"] == module.SCRIPT_VERSION
    assert env["task_surface"] == task_surface
    assert env["available"] is True
    assert isinstance(env["warnings"], list)
    json.dumps(env)  # CLI-bound: must serialize without default=str


@pytest.mark.parametrize("name", NAMES)
def test_claim_license_block(name, envelope):
    env = envelope(name)
    cl = env["claim_license"]
    assert set(cl) == CLAIM_LICENSE_KEYS
    assert cl["task_surface"] == env["task_surface"]
    assert len(cl["licenses"]) > 50
    assert len(cl["does_not_license"]) > 50
    assert env["claim_license_rendered"].startswith(RENDERED_HEADER)


@pytest.mark.parametrize("name", NAMES)
def test_results_carry_surface_payload(name, envelope):
    missing = SURFACES[name][3] - set(envelope(name)["results"])
    assert not missing, f"{name} results missing {sorted(missing)}"


# No prose target word count: an edit pair, a manifest, and a POS-bigram
# comparison of two corpora.
NO_TARGET_WORDS = {"known_editor_profile", "manifest_validator", "manuscript_bigram_diff"}


@pytest.mark.parametrize("name", [n for n in NAMES if n not in NO_TARGET_WORDS])
def test_target_reports_words(name, envelope):
    words = envelope(name)["target"]["words"]
    assert isinstance(words, int) and words > 0


# Where results carry their own per-part word counts, target.words must agree.
# variance_audit is the Codex P2 regression on PR #84: the builder read
# audit["n_words"] while audit_text() stores it under audit["summary"], so
# CLI runs reported target.words == 0.
WORDS_FROM_RESULTS = {
    "variance_audit": lambda r: r["audit"]["summary"]["n_words"],
    "manuscript_audit": lambda r: sum(ch["n_words"] for ch in r["chapters"]),
    "manuscript_repetition_audit": lambda r: sum(ch["n_target_words"] for ch in r["chapters"]),
    "bigram_diff": lambda r: r["target_bigrams"],
}


@pytest.mark.parametrize("name", sorted(WORDS_FROM_RESULTS))
def test_target_words_match_results(name, envelope):
    env = envelope(name)
    assert env["target"]["words"] == WORDS_FROM_RESULTS[name](env["results"])


# Script-specific metadata that rides under ``target``.
TARGET_METADATA = [
    ("chapter_distinctiveness_audit", "n_chapters", 3),
    ("manuscript_audit", "n_chapters", 3),
    ("manuscript_repetition_audit", "n_chapters", 3),
    ("idiolect_detector", "n_files", 3),
    ("idiolect_detector", "privacy", idd.PRIVACY_WARNING),
    ("voice_profile", "n_files", 3),
    ("voice_profile", "privacy",
     "PRIVATE - DO NOT SHARE. A voice profile is a voice-cloning input."),
]


@pytest.mark.parametrize("name,key,expected", TARGET_METADATA)
def test_target_metadata(name, key, expected, envelope):
    assert envelope(name)["target"][key] == expected


# ---------------------------------------------------------------------------
# Unavailable paths: empty input yields a well-formed available:false
# envelope with no claim license and a reason in warnings.
# ---------------------------------------------------------------------------

def _empty_text_envelope(module, audit_fn):
    def build(_c):
        return module.build_audit_payload(
            audit_fn(""), target_path=Path("empty.md"),
            baseline_block=None, baseline_comparison=None,
        )
    return build


UNAVAILABLE = {
    "agency_abstraction_audit": _empty_text_envelope(aaa, aaa.audit_agency_abstraction),
    "discourse_move_signature": _empty_text_envelope(dms, dms.audit_discourse_moves),
    "function_word_grammar_audit": _empty_text_envelope(fwg, fwg.audit_function_word_grammar),
    "paragraph_audit": _empty_text_envelope(pa, pa.audit_paragraphs),
    "punctuation_cadence_audit": _empty_text_envelope(pca, pca.audit_punctuation_cadence),
    "stance_modality_audit": _empty_text_envelope(sma, sma.audit_stance_modality),
    "surprisal_audit": lambda c: _surprisal(c, text=""),
    "controls_audit": lambda c: _controls(c, baseline=False),
}


@pytest.mark.parametrize("name", sorted(UNAVAILABLE))
def test_empty_input_is_unavailable(name, corpus):
    env = UNAVAILABLE[name](corpus)
    assert set(env) == REQUIRED_TOP_LEVEL_KEYS
    assert env["available"] is False
    assert env["claim_license"] is None
    assert env["claim_license_rendered"] is None
    assert env["warnings"]


# ---------------------------------------------------------------------------
# Baseline blocks: a real --baseline-dir run populates ``baseline`` with the
# file count and a non-zero word count.
# ---------------------------------------------------------------------------

BASELINE_CLI = {
    name: (lambda m: lambda c: _cli_json(m, c / "target.md", "--baseline-dir",
                                         c / "baseline", "--json"))(SURFACES[name][0])
    for name in (
        "agency_abstraction_audit", "aic_pattern_audit",
        "discourse_move_signature", "function_word_grammar_audit", "paragraph_audit",
        "phraseological_signature_audit", "punctuation_cadence_audit",
        "stance_modality_audit",
    )
}
BASELINE_CLI["variance_audit"] = lambda c: _cli_json(
    va, c / "target.md", "--baseline-dir", c / "baseline", "--no-tier2", "--no-tier3", "--json")
BASELINE_CLI["manuscript_audit"] = lambda c: _cli_json(
    ma, c / "manuscript.md", "--baseline-dir", c / "baseline", "--no-tier2", "--no-tier3",
    "--json")


@pytest.fixture(scope="module")
def baseline_envelope(corpus):
    cache: dict[str, dict] = {}

    def get(name: str) -> dict:
        if name not in cache:
            cache[name] = BASELINE_CLI[name](corpus)
        return cache[name]

    return get


@pytest.mark.parametrize("name", sorted(BASELINE_CLI))
def test_baseline_dir_populates_baseline(name, baseline_envelope):
    env = baseline_envelope(name)
    assert env["available"] is True
    assert env["baseline"]["n_files"] == len(BASELINE_TEXTS)


# These builders read baseline_block["n_words"], which their own CLI never
# sets, so a real --baseline-dir run reports baseline.words == 0. The old
# hand-built baseline blocks supplied n_words and hid it.
BASELINE_WORDS_MISSING = {
    "agency_abstraction_audit", "discourse_move_signature", "function_word_grammar_audit",
    "paragraph_audit", "punctuation_cadence_audit", "stance_modality_audit",
}


@pytest.mark.parametrize("name", [
    pytest.param(n, marks=pytest.mark.xfail(
        strict=True, reason="baseline_block carries no n_words")
    ) if n in BASELINE_WORDS_MISSING else n
    # manuscript_audit hard-codes baseline words to 0.
    for n in sorted(set(BASELINE_CLI) - {"manuscript_audit"})
])
def test_baseline_dir_reports_baseline_words(name, baseline_envelope):
    # variance_audit: main() trims output["baseline"], so baseline.words
    # depends on summing the per-file audits before the trim.
    assert baseline_envelope(name)["baseline"]["words"] > 0


@pytest.mark.parametrize("name,n_files", [
    ("repetition_audit", len(BASELINE_TEXTS)),
    ("manuscript_repetition_audit", len(BASELINE_TEXTS)),
    ("voice_distance", len(BASELINE_TEXTS)),
    ("idiolect_detector", 1),  # the reference corpus
])
def test_default_fixture_baseline_populated(name, n_files, envelope):
    baseline = envelope(name)["baseline"]
    assert baseline["n_files"] == n_files
    assert baseline["words"] > 0


def test_aic_baseline_adds_per_pattern_deltas(baseline_envelope):
    patterns = baseline_envelope("aic_pattern_audit")["results"]["patterns"]
    assert patterns
    for block in patterns.values():
        assert "baseline_density_per_1k" in block
        assert "delta_per_1k" in block


def test_construction_baseline_hides_filenames_by_default(corpus):
    # Privacy default: baseline filenames are suppressed unless
    # --include-baseline-filenames is passed.
    env = _construction(corpus, baseline_density={"existential_there": 1.0},
                        baseline_loaded=[Path("base/a.txt")])
    assert env["baseline"]["n_files"] == 1
    assert env["baseline"]["words"] == 5000
    assert "files_loaded" not in env["baseline"]


def test_repetition_baseline_lists_loaded_files(envelope):
    loaded = envelope("repetition_audit")["baseline"]["files_loaded"]
    assert sorted(Path(p).name for p in loaded) == sorted(BASELINE_TEXTS)


def test_controls_baseline_counts_files(envelope):
    assert envelope("controls_audit")["baseline"]["n_files"] == len(BASELINE_TEXTS)


def test_kicker_baseline_comparison_lives_under_results():
    block = kd.kicker_density(SAMPLE, nlp=None, baseline_value=0.10,
                              baseline_source="test_register")
    env = kd.build_audit_payload(block, target_path=Path("draft.md"), text=SAMPLE)
    bc = env["results"]["baseline_comparison"]
    assert bc["baseline_source"] == "test_register"
    assert bc["baseline_value"] == 0.10


# ---------------------------------------------------------------------------
# ai_status routing: the declared status lands on the envelope and its
# state-routed caveat lands in claim_license.additional_caveats.
# ---------------------------------------------------------------------------

AI_STATUS = [
    ("discourse_move_signature", "ai_generated_from_outline", "outline"),
    ("stance_modality_audit", "ai_generated_from_outline", "outline"),
    ("punctuation_cadence_audit", "pre_ai_human", "pre-ai"),
    ("mimicry_cosplay_audit", "ai_generated_from_outline", "outline"),
]


@pytest.mark.parametrize("name,status,needle", AI_STATUS)
def test_ai_status_routes_caveats(name, status, needle, corpus):
    if name == "mimicry_cosplay_audit":
        env = _mimicry(corpus, ai_status=status)
    else:
        env = _cli_json(SURFACES[name][0], corpus / "target.md", "--ai-status", status, "--json")
    assert env["ai_status"] == status
    caveats = " ".join(env["claim_license"]["additional_caveats"]).lower().replace("_", "-")
    assert needle in caveats


# ---------------------------------------------------------------------------
# Warnings: conditions a consumer must see surface in envelope.warnings.
# ---------------------------------------------------------------------------

def _dirty_corpus(c):
    dirty = c / "dirty"
    dirty.mkdir(exist_ok=True)
    code = "```\n" + "x = compute(y, z)\n" * 40 + "```\n"
    (dirty / "notes.md").write_text(BASELINE_TEXTS["a.md"][:400] + code, encoding="utf-8")
    return _cli_json(cc, "--dir", dirty, "--json")


def _invalid_manifest(c):
    bad = c / "bad_manifest.jsonl"
    bad.write_text(json.dumps({"id": "a", "path": "baseline/a.md", "use": "baseline"}) + "\n",
                   encoding="utf-8")
    return _cli_json(mv, bad, "--json")


WARNINGS = {
    "check_corpus": (_dirty_corpus, "warning"),
    "manifest_validator": (_invalid_manifest, "error"),
    "repetition_audit": (
        lambda c: _cli_json(ra, c / "target.md", "--baseline-dir", c / "broken_baseline",
                            "--json"),
        "skipped"),
    "manuscript_repetition_audit": (
        lambda c: _cli_json(mra, c / "manuscript.md", "--baseline-dir",
                            c / "broken_baseline", "--json"),
        "skipped"),
    "bigram_diff": (lambda c: _bigram_diff(c, skipped=[Path("bad.txt")]), "skipped"),
    "manuscript_bigram_diff": (
        lambda c: _manuscript_bigram_diff(c, a_skipped=[Path("a/bad.txt")]), "corpus-a"),
    # The default fixtures have a three-file baseline.
    "voice_distance": (None, "fewer than 5 baseline files"),
    "voice_profile": (None, "fewer than 5 files"),
}


@pytest.mark.parametrize("name", sorted(WARNINGS))
def test_condition_surfaces_as_warning(name, corpus, envelope):
    build, needle = WARNINGS[name]
    env = envelope(name) if build is None else build(corpus)
    assert any(needle in w.lower() for w in env["warnings"]), env["warnings"]


def test_repetition_records_skipped_files(corpus):
    env = _cli_json(ra, corpus / "target.md", "--baseline-dir", corpus / "broken_baseline",
                    "--json")
    assert [Path(p).name for p in env["baseline"]["files_skipped"]] == ["locked.md"]


def test_clean_corpus_has_no_warning(envelope):
    env = envelope("check_corpus")
    assert env["results"]["status"] == "clean"
    assert env["warnings"] == []


# ---------------------------------------------------------------------------
# Surface-specific results.
# ---------------------------------------------------------------------------

def test_discourse_relation_distribution(envelope):
    """The PDTB relation layer rides in results and keeps its bounds."""
    rel = envelope("discourse_move_signature")["results"]["relation_distribution"]
    assert rel["calibration_status"] == "uncalibrated"
    assert rel["buckets"] == ["comparison", "contingency", "expansion", "temporal"]
    assert 0.0 <= rel["relation_entropy_bits"] <= rel["relation_entropy_max_bits"] == 2.0


def test_kicker_density_function_keeps_legacy_block():
    """variance_audit and aesthetic_authority_audit call kicker_density() as a
    function and read its top-level keys; the envelope must not change that.
    The legacy claim_license tag also rides inside results for them."""
    block = kd.kicker_density(SAMPLE, nlp=None)
    assert block["signal_path"] == "aic_8_9.kicker_density"
    assert block["family"] == "aic-9-closure-inflation"
    assert block["task_surface"] == "smoothing_diagnosis"
    assert block["claim_license"] == "voice_diagnostic"
    env = kd.build_audit_payload(block, target_path=Path("draft.md"), text=SAMPLE)
    assert env["results"]["claim_license"] == "voice_diagnostic"


def test_bigram_diff_counts(envelope):
    r = envelope("bigram_diff")["results"]
    assert r["target_bigrams"] == sum(TARGET_BIGRAMS.values())
    assert r["target_unique"] == len(TARGET_BIGRAMS)
    assert {"pooled", "mean"} <= set(r["diffs"])


def test_manuscript_bigram_diff_maps_corpus_a_to_target(envelope):
    env = envelope("manuscript_bigram_diff")
    assert (env["target"]["label"], env["target"]["n_files"]) == ("hamilton", 2)
    assert (env["baseline"]["label"], env["baseline"]["n_files"]) == ("madison", 3)


def test_known_editor_profile_reports_verdict(envelope):
    env = envelope("known_editor_profile")
    assert env["results"]["profile_label"] == "test_editor"
    cs = env["claim_license"]["comparison_set"]
    assert cs["verdict"] == env["results"]["verdict"]
    assert cs["profile_n_pairs"] == len(BASELINE_TEXTS)


def test_chapter_distinctiveness_reports_each_chapter(envelope):
    chapters = envelope("chapter_distinctiveness_audit")["results"]["chapters"]
    assert [ch["label"] for ch in chapters] == [label for label, _ in CHAPTERS]
    for ch in chapters:
        assert {"n_target_words", "candidates"} <= set(ch)


def test_voice_distance_register_match_reaches_comparison_set(envelope):
    env = envelope("voice_distance")
    strength = env["results"]["register_match"]["match"]["strength"]
    assert env["claim_license"]["comparison_set"]["register_match"] == strength


# ---------------------------------------------------------------------------
# Privacy: voice-cloning-grade outputs.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["idiolect_detector", "voice_profile"])
def test_does_not_license_flags_voice_cloning(name, envelope):
    text = envelope(name)["claim_license"]["does_not_license"].lower()
    assert "voice-cloning" in text or "private" in text


def _manifest_with_registers(c, registers, name):
    rows = []
    for (fname, _), register in zip(BASELINE_TEXTS.items(), registers):
        row = {"id": fname[0], "path": str(c / "baseline" / fname), "use": ["baseline"],
               "ai_status": "pre_ai_human"}
        if register is not None:
            row["register"] = register
        rows.append(row)
    path = c / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


TIERS_ZERO = {"private_composed": 0, "private_dyadic": 0,
              "public_composed": 0, "public_responsive": 0}


@pytest.mark.parametrize("registers,tier,resolved", [
    (["blog_essay"] * 3, "public_composed", 3),
    (["blog_essay", None, "not.registered"], "public_composed", 1),
])
def test_voice_distance_baseline_register_tiers(registers, tier, resolved, corpus):
    """A non-zero unresolved count must survive onto the consumed surface;
    it is the only signal that the pooled reference holds entries whose
    privacy tier could not be resolved."""
    manifest = _manifest_with_registers(corpus, registers, f"vd_{resolved}.jsonl")
    env = _cli_json(vd, corpus / "target.md", "--manifest", manifest, "--no-spacy", "--json")
    assert env["baseline"]["register_tier_counts"] == {**TIERS_ZERO, tier: resolved}
    assert env["baseline"]["unresolved_register_count"] == len(registers) - resolved


@pytest.mark.parametrize("registers,tier,resolved", [
    (["personal"] * 3, "private_composed", 3),
    (["personal", None, "not.registered"], "private_composed", 1),
])
def test_voice_profile_register_tiers(registers, tier, resolved, corpus):
    manifest = _manifest_with_registers(corpus, registers, f"vp_{resolved}.jsonl")
    env = _cli_json(vp, "--manifest", manifest, "--no-spacy", "--json", "--allow-public-output")
    summary = env["results"]["baseline_summary"]
    assert summary["register_tier_counts"] == {**TIERS_ZERO, tier: resolved}
    assert summary["unresolved_register_count"] == len(registers) - resolved


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


@pytest.mark.parametrize("name", ["voice_distance", "voice_profile"])
def test_biber_features_absent_by_default(name, envelope):
    """AC10 (neurobiber-v2): downstream consumers pin the default output, so
    'biber_features' must not appear anywhere without --include-biber."""
    assert "biber_features" not in set(_walk_keys(envelope(name)["results"]))


@pytest.mark.parametrize("name", ["voice_distance", "voice_profile"])
@pytest.mark.parametrize("neurobiber_importable", [False, True])
def test_include_biber_abstains_with_missing_dependency(
    name, neurobiber_importable, corpus, monkeypatch,
):
    """Codex P1 and round-2 P2: --include-biber with no M2 tagger must emit an
    available:false missing_dependency envelope (rc 3), not a traceback, even
    when a `neurobiber` package is importable."""
    if neurobiber_importable:
        monkeypatch.setitem(sys.modules, "neurobiber", types.ModuleType("neurobiber"))
    if name == "voice_distance":
        argv = [corpus / "target.md", "--baseline-dir", corpus / "baseline"]
    else:
        argv = ["--baseline-dir", corpus / "baseline", "--allow-public-output"]
    rc, out, _err = _cli(SURFACES[name][0], *argv, "--no-spacy", "--include-biber", "--json")
    assert rc == 3
    env = json.loads(out)
    assert env["available"] is False
    assert env["reason_category"] == "missing_dependency"
    assert "biber" in env["reason"].lower()


class TestVoiceProfileOutputPrivacy:
    """voice_profile output is voice-cloning-grade. stdout and --json-out are
    refused outside ai-prose-baselines-private/ unless --allow-public-output
    (Codex P2 on PR #82: stdout had no path, so the path guard never fired)."""

    def test_stdout_refused_without_allow_flag(self, corpus):
        rc, out, err = _cli(vp, "--baseline-dir", corpus / "baseline", "--no-spacy", "--json")
        assert rc == 2
        assert "allow-public-output" in err
        assert out == ""

    def test_json_out_to_private_path_writes_envelope(self, corpus, tmp_path):
        artifact = tmp_path / "ai-prose-baselines-private" / "profile.json"
        rc, out, _err = _cli(vp, "--baseline-dir", corpus / "baseline", "--no-spacy",
                             "--json-out", artifact)
        assert rc == 0
        assert out == ""
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        assert (payload["schema_version"], payload["tool"]) == ("1.0", "voice_profile")

    def test_json_out_to_public_path_refused(self, corpus, tmp_path):
        artifact = tmp_path / "profile.json"
        rc, _out, err = _cli(vp, "--baseline-dir", corpus / "baseline", "--no-spacy",
                             "--json-out", artifact)
        assert rc == 2
        assert "ai-prose-baselines-private" in err
        assert not artifact.exists()

    def test_json_out_public_with_allow_flag(self, corpus, tmp_path):
        artifact = tmp_path / "profile.json"
        rc, _out, _err = _cli(vp, "--baseline-dir", corpus / "baseline", "--no-spacy",
                              "--json-out", artifact, "--allow-public-output")
        assert rc == 0
        assert json.loads(artifact.read_text(encoding="utf-8"))["tool"] == "voice_profile"
