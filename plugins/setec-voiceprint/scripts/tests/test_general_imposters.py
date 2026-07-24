#!/usr/bin/env python3
"""Regression tests for general_imposters.py.

The GI harness is the cathedral upgrade #4 finisher — it consumes
the impostor corpus shipped 1.14.3–1.19.0 to turn distance into a
calibrated attribution claim. Tests verify:

  * The harness refuses cleanly when impostor count is below the
    MIN_IMPOSTORS floor (5) or when the candidate has no docs.
  * Win-proportion math is correct on a synthetic manifest where
    the target is a copy of a candidate doc (proportion → 1.0,
    decision: consistent_with_candidate).
  * The same harness on a target that's a copy of an IMPOSTOR doc
    produces proportion → 0.0, decision: inconsistent.
  * Gray-zone refusal fires when the proportion lands in [0.20,
    0.80] — the framework's "the evidence is mixed" guard.
  * Privacy guard refuses non-private output paths.
  * JSON output shape is stable.
  * Manifest filtering picks the right candidate identity baselines
    + impostors via persona + register + impostor_for cross-check.
  * Wilson 95% CI is reasonable (in [0, 1] and contains the
    proportion).

Tests build a synthetic in-memory corpus to avoid dependency on
real impostor pools (which are private).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import general_imposters as gi  # type: ignore


# ------------------- Fixtures -----------------------------------


def _make_entry(
    *,
    id: str,
    text: str,
    persona: str,
    corpus_role: str = "identity_baseline",
    register: str = "blog_essay",
    impostor_for: list[str] | None = None,
    author: str = "Anon",
) -> gi.CorpusEntry:
    return gi.CorpusEntry(
        id=id, text=text, persona=persona, register=register,
        author=author, corpus_role=corpus_role,
        impostor_for=list(impostor_for or []),
        word_count=len(text.split()),
    )


def _candidate_docs(persona="alice"):
    """Build 4 alice docs with consistent vocabulary."""
    base = (
        "The discipline of attention is older than the disciplines that "
        "depend on it. The mathematician and the carpenter share a "
        "single habit. The looker comes back altered. "
    ) * 8  # ~150 words per doc
    return [
        _make_entry(id=f"alice_{i}", text=base + f" Document {i}.",
                   persona=persona)
        for i in range(4)
    ]


def _impostor_docs():
    """Build 6 impostor docs across 6 personas, with vocabulary
    different from alice."""
    bases = [
        "Most contemporary fiction declines structural ambition in "
        "exchange for surface velocity. The reader is not invited to "
        "stop. ",
        "The economist writes against time. Each year's book is "
        "outdated by the next election cycle. ",
        "Bone orchard, glass room, quiet house. The horror is not "
        "what's there but what isn't. ",
        "Public reason is a contract a society makes with itself. The "
        "contract has provisions and exceptions. ",
        "Slack and email and Teams are not the same medium. Each "
        "carries different conversational weight. ",
        "Recipes are propositions. Pasta dishes are arguments about "
        "season and restraint. ",
    ]
    return [
        _make_entry(
            id=f"impostor_{i}",
            text=(b * 5) + f" Impostor {i}.",
            persona=f"impostor_{i}",
            corpus_role="impostor",
            impostor_for=["alice"],
        )
        for i, b in enumerate(bases)
    ]


# ------------------- Refusal gates -------------------------------


def test_refuses_when_too_few_impostors():
    """Below MIN_IMPOSTORS → harness refuses cleanly."""
    candidate = _candidate_docs()
    impostors = _impostor_docs()[:3]  # only 3, below floor of 5
    target = candidate[0].text  # target = a candidate doc

    result = gi.run_gi(
        target_text=target, target_id="target",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=20, seed=42,
    )
    assert result.refused is True
    assert "at least" in result.refusal_reason.lower()
    assert result.decision == "refused"
    assert result.iterations == 0


def test_refuses_when_no_candidate_docs():
    impostors = _impostor_docs()
    result = gi.run_gi(
        target_text="some text",
        target_id="t",
        candidate_docs=[],
        impostor_docs=impostors,
        iterations=20, seed=42,
    )
    assert result.refused is True
    assert "candidate" in result.refusal_reason.lower()


def test_refused_result_builds_structured_r3_envelope():
    """A hard refusal must NOT ship a success envelope with proportion=NaN
    (which tripped build_output's R4 bounds gate and surfaced as an
    internal_error through the dispatcher). It emits an available=False R3
    refusal with reason_category bad_input, empty results, and JSON that
    carries no NaN."""
    candidate = _candidate_docs()
    impostors = _impostor_docs()[:3]  # below MIN_IMPOSTORS
    result = gi.run_gi(
        target_text=candidate[0].text, target_id="target",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=20, seed=42,
    )
    assert result.refused is True

    env = gi._build_envelope(result)
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert env["schema_version"] == "1.0"
    assert env["results"] == {}
    assert "at least" in env["reason"].lower()
    # Serializable with strict JSON (NaN would need allow_nan and would break
    # downstream parsers); the proportion=NaN must never reach the envelope.
    json.dumps(env, allow_nan=False)


def test_gray_zone_decision_is_available_not_refused():
    """The gray-zone DECISION (a valid proportion where the harness declines
    an attribution CLAIM) is distinct from a hard refusal: it ships
    available=True with a real numeric proportion, untouched by the refusal
    path."""
    candidate = _candidate_docs()
    impostors = _impostor_docs()
    # A target equal to a candidate doc tends to win cleanly; we only assert
    # the *shape* contract for a non-refused run, not a particular band.
    result = gi.run_gi(
        target_text=candidate[0].text, target_id="target",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=20, seed=42,
    )
    assert result.refused is False
    env = gi._build_envelope(result)
    assert env["available"] is True
    assert isinstance(env["results"].get("proportion"), (int, float))
    json.dumps(env, allow_nan=False)


# ------------------- Math invariants -----------------------------


def test_target_identical_to_candidate_yields_high_proportion():
    """If the target is byte-identical to a candidate doc, the
    target should be closer to candidate than to any impostor on
    every iteration → proportion ≈ 1.0."""
    candidate = _candidate_docs()
    impostors = _impostor_docs()
    target = candidate[0].text  # exact copy

    result = gi.run_gi(
        target_text=target, target_id="target",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=50, seed=42,
    )
    assert result.refused is False
    # Identical text → almost always closest. Allow slight noise from
    # the feature-subset bootstrap, but proportion should be very high.
    assert result.proportion >= 0.95, (
        f"identical target should win nearly every iteration; "
        f"got proportion {result.proportion}"
    )
    assert result.decision == "consistent_with_candidate"


def test_target_identical_to_impostor_yields_low_proportion():
    """If the target is byte-identical to an impostor doc, the
    proportion should be near 0 (impostor wins every iteration)."""
    candidate = _candidate_docs()
    impostors = _impostor_docs()
    target = impostors[0].text  # exact copy of an impostor

    result = gi.run_gi(
        target_text=target, target_id="target",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=50, seed=42,
    )
    assert result.refused is False
    assert result.proportion <= 0.05, (
        f"impostor-identical target should lose every iteration; "
        f"got proportion {result.proportion}"
    )
    assert result.decision == "inconsistent_with_candidate"


def test_proportion_bounded_to_zero_one():
    """Proportion is wins/iterations; must be in [0,1]."""
    candidate = _candidate_docs()
    impostors = _impostor_docs()
    target = "Some completely unrelated text about the desert wind. " * 30

    result = gi.run_gi(
        target_text=target, target_id="t",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=20, seed=42,
    )
    assert 0.0 <= result.proportion <= 1.0
    assert result.wins + result.losses == result.iterations


def test_wilson_ci_contains_proportion_and_in_range():
    """Wilson CI on the proportion must be valid: lo ≤ proportion ≤
    hi, and both in [0, 1]."""
    candidate = _candidate_docs()
    impostors = _impostor_docs()
    target = candidate[0].text

    result = gi.run_gi(
        target_text=target, target_id="t",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=50, seed=42,
    )
    assert result.proportion_ci_95 is not None
    lo, hi = result.proportion_ci_95
    assert 0.0 <= lo <= hi <= 1.0
    assert lo <= result.proportion <= hi


# ------------------- Decision regions ----------------------------


def test_decision_consistent_at_high_proportion():
    """Proportion ≥ GRAY_ZONE_HIGH → consistent_with_candidate."""
    assert gi._decide(gi.GRAY_ZONE_HIGH) == "consistent_with_candidate"
    assert gi._decide(0.95) == "consistent_with_candidate"


def test_decision_inconsistent_at_low_proportion():
    """Proportion ≤ GRAY_ZONE_LOW → inconsistent_with_candidate."""
    assert gi._decide(gi.GRAY_ZONE_LOW) == "inconsistent_with_candidate"
    assert gi._decide(0.05) == "inconsistent_with_candidate"


def test_decision_gray_zone_in_middle():
    """Mid-range → gray_zone_refused."""
    assert gi._decide(0.50) == "gray_zone_refused"
    assert gi._decide(0.30) == "gray_zone_refused"
    assert gi._decide(0.79) == "gray_zone_refused"


# ------------------- Feature pipeline ----------------------------


def test_feature_vocab_returns_top_n():
    entries = _candidate_docs() + _impostor_docs()
    vocab = gi._feature_vocab(entries, top_n=10)
    assert len(vocab) <= 10
    assert all(isinstance(t, str) for t in vocab)


def test_feature_vector_length_matches_vocab():
    vocab = ["the", "and", "of", "is"]
    text = "the the and is the of"
    vec = gi._feature_vector(text, vocab)
    assert len(vec) == len(vocab)
    # Sum of (count/total) must be ≤ 1.
    assert sum(vec) <= 1.0 + 1e-9


def test_cosine_distance_zero_for_identical_vectors():
    a = [0.5, 0.3, 0.2]
    assert abs(gi._cosine(a, a)) < 1e-9


def test_cosine_distance_one_for_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(gi._cosine(a, b) - 1.0) < 1e-9


def test_cosine_distance_one_for_zero_vector():
    """Zero-norm vector: harness returns 1.0 (orthogonal-distance
    floor) rather than NaN."""
    assert gi._cosine([0.0, 0.0, 0.0], [1.0, 1.0, 1.0]) == 1.0


# ------------------- Manifest selection --------------------------


def test_select_candidate_docs_respects_persona_and_register():
    entries = _candidate_docs() + _impostor_docs()
    cand = gi._select_candidate_docs(
        entries, candidate_persona="alice", register="blog_essay",
    )
    assert len(cand) == 4
    assert all(c.persona == "alice" for c in cand)
    assert all(c.corpus_role == "identity_baseline" for c in cand)


def test_select_impostor_docs_filters_by_impostor_for():
    entries = _candidate_docs() + _impostor_docs()
    imps = gi._select_impostor_docs(
        entries, candidate_persona="alice", register="blog_essay",
    )
    # All 6 impostors named "alice" in their impostor_for.
    assert len(imps) == 6
    assert all(c.corpus_role == "impostor" for c in imps)


def test_select_impostor_docs_excludes_unmatched_persona():
    """Impostors whose impostor_for doesn't include the candidate
    persona must be filtered out."""
    entries = _candidate_docs()
    entries.append(_make_entry(
        id="other_imp", text="Some text. " * 50, persona="other",
        corpus_role="impostor", impostor_for=["bob"],  # not alice
    ))
    imps = gi._select_impostor_docs(
        entries, candidate_persona="alice", register="blog_essay",
    )
    assert len(imps) == 0


# ------------------- End-to-end manifest -------------------------


def _write_manifest(tmp_path: Path) -> Path:
    """Write a real manifest JSONL pointing at real .txt files for
    the end-to-end test."""
    text_dir = tmp_path / "texts"
    text_dir.mkdir()
    rows = []
    # 4 alice docs.
    base = ("The discipline of attention is older than the "
            "disciplines that depend on it. ") * 30
    for i in range(4):
        p = text_dir / f"alice_{i}.txt"
        p.write_text(base + f" Document {i}.", encoding="utf-8")
        rows.append({
            "id": f"alice_{i}", "path": str(p),
            "persona": "alice", "register": "blog_essay",
            "author": "Alice", "corpus_role": "identity_baseline",
            "ai_status": "pre_ai_human", "use": ["voice_profile"],
            "split": "baseline", "privacy": "private",
        })
    # 6 impostors.
    for i in range(6):
        p = text_dir / f"impostor_{i}.txt"
        p.write_text(
            f"Most contemporary fiction declines structural ambition. "
            f"Impostor {i}. " * 30,
            encoding="utf-8",
        )
        rows.append({
            "id": f"impostor_{i}", "path": str(p),
            "persona": f"impostor_{i}", "register": "blog_essay",
            "author": f"Impostor {i}", "corpus_role": "impostor",
            "impostor_for": ["alice"],
            "register_match": "high", "topic_match": "medium",
            "consent_status": "fair_use_research",
            "era": "pre_chatgpt",
            "acquired_via": "test_fixture",
            "ai_status": "pre_ai_human", "use": ["voice_impostor"],
            "split": "baseline", "privacy": "private",
        })
    manifest = tmp_path / "manifest.jsonl"
    with manifest.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return manifest


def test_run_end_to_end_with_real_manifest(tmp_path):
    """Drive the full CLI pipeline against a synthetic on-disk
    manifest. Target is a copy of an alice doc → expect high
    proportion."""
    manifest = _write_manifest(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text(
        ("The discipline of attention is older than the "
         "disciplines that depend on it. ") * 30 + " Document 0.",
        encoding="utf-8",
    )
    out_md = tmp_path / "ai-prose-baselines-private" / "gi.md"
    out_json = tmp_path / "ai-prose-baselines-private" / "gi.json"

    args = argparse.Namespace(
        target=str(target), target_id=None,
        manifest=str(manifest),
        candidate_persona="alice", register="blog_essay",
        iterations=30, feature_fraction=0.5,
        top_n_features=200, seed=42,
        out=str(out_md), json_out=str(out_json),
        allow_public_output=False,
    )
    rc = gi.run(args)
    assert rc == 0
    assert out_md.is_file()
    assert out_json.is_file()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    # schema_version 1.0 envelope: per-script payload under results.
    # GIResult.to_dict() is preserved unchanged for internal/legacy
    # consumers; the envelope wraps it for CLI JSON consumers.
    assert data["schema_version"] == "1.0"
    inner = data["results"]
    assert inner["candidate_persona"] == "alice"
    assert inner["n_impostors"] == 6
    assert inner["proportion"] >= 0.9
    assert inner["decision"] == "consistent_with_candidate"


def test_privacy_guard_refuses_non_private(tmp_path):
    manifest = _write_manifest(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("Test " * 100, encoding="utf-8")
    public_out = tmp_path / "public_oops" / "gi.md"

    args = argparse.Namespace(
        target=str(target), target_id=None,
        manifest=str(manifest),
        candidate_persona="alice", register="blog_essay",
        iterations=10, feature_fraction=0.5,
        top_n_features=100, seed=42,
        out=str(public_out), json_out=None,
        allow_public_output=False,
    )
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            gi.run(args)
        assert exc.value.code == 2


# ------------------- JSON shape ----------------------------------


def test_to_dict_carries_documented_fields():
    """The JSON ledger shape stays stable so downstream tooling
    (a future cross-target dashboard) can rely on it."""
    candidate = _candidate_docs()
    impostors = _impostor_docs()
    result = gi.run_gi(
        target_text=candidate[0].text, target_id="t",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=20, seed=42,
    )
    d = result.to_dict()
    expected = {
        "task_surface", "tool", "version",
        "target_id", "candidate_persona", "candidate_n_docs",
        "n_impostors", "impostor_personas",
        "iterations", "feature_fraction", "top_n_features",
        "wins", "losses", "proportion", "proportion_ci_95",
        "refused", "refusal_reason", "decision",
        "claim_license",
    }
    missing = expected - d.keys()
    assert not missing, f"missing keys: {missing}"
    # Claim license is structured.
    assert "licenses" in d["claim_license"]
    assert "does_not_license" in d["claim_license"]
    assert "gray_zone" in d["claim_license"]


# ------------------- CLI surface ---------------------------------


def test_cli_help_lists_required_flags():
    parser = gi.build_arg_parser()
    help_text = parser.format_help()
    for flag in (
        "--target", "--manifest", "--candidate-persona",
        "--register", "--iterations", "--feature-fraction",
        "--top-n-features", "--seed",
        "--out", "--json-out", "--allow-public-output",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_cli_rejects_missing_required():
    parser = gi.build_arg_parser()
    if pytest is not None:
        with pytest.raises(SystemExit):
            parser.parse_args([])  # all required flags missing


# ------------------- Determinism --------------------------------


def test_deterministic_under_same_seed():
    """Same seed → same proportion across runs."""
    candidate = _candidate_docs()
    impostors = _impostor_docs()
    target = "Different text not closely matching anything. " * 50

    a = gi.run_gi(
        target_text=target, target_id="t",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=30, seed=42,
    )
    b = gi.run_gi(
        target_text=target, target_id="t",
        candidate_docs=candidate, impostor_docs=impostors,
        iterations=30, seed=42,
    )
    assert a.proportion == b.proportion
    assert a.wins == b.wins


# ---------- Persona floor (1.29.1) ---------------------------------


class TestPersonaFloor:
    """The MIN_IMPOSTORS gate counts distinct impostor *personas*
    (writers), not docs. 5 docs from one persona must NOT clear the
    floor — that's 1 writer, and the GI methodology requires M
    writers. Reproduces the reviewer-flagged P2."""

    def test_five_docs_one_persona_refuses(self):
        candidate = _candidate_docs()
        # Five impostor docs, all from the same persona.
        single_persona_impostors = [
            gi.CorpusEntry(
                id=f"single_{i}",
                text=(
                    "Most contemporary fiction declines structural "
                    "ambition in exchange for surface velocity. " * 5
                ) + f" Doc {i}.",
                persona="impostor_solo",
                register="blog_essay",
                author="Anon",
                corpus_role="impostor",
                impostor_for=["alice"],
                word_count=100,
            )
            for i in range(5)
        ]
        result = gi.run_gi(
            target_text=candidate[0].text, target_id="t",
            candidate_docs=candidate,
            impostor_docs=single_persona_impostors,
            iterations=20, seed=42,
        )
        assert result.refused is True
        assert result.decision == "refused"
        assert "persona" in result.refusal_reason.lower()
        assert "writer" in result.refusal_reason.lower()

    def test_five_personas_passes_floor(self):
        candidate = _candidate_docs()
        impostors = _impostor_docs()[:5]  # exactly 5, all distinct
        # Sanity check the fixture: 5 distinct personas.
        assert len({i.persona for i in impostors}) == 5
        result = gi.run_gi(
            target_text=candidate[0].text, target_id="t",
            candidate_docs=candidate, impostor_docs=impostors,
            iterations=10, seed=42,
        )
        assert result.refused is False, result.refusal_reason

    def test_refusal_message_mentions_doc_count_too(self):
        """Doc count is reported as an adequacy diagnostic alongside
        the persona-count gate; both numbers help the user diagnose."""
        candidate = _candidate_docs()
        single_persona_impostors = [
            gi.CorpusEntry(
                id=f"s_{i}", text=f"text {i} " * 50,
                persona="solo", register="blog_essay", author="A",
                corpus_role="impostor", impostor_for=["alice"],
                word_count=50,
            )
            for i in range(7)
        ]
        result = gi.run_gi(
            target_text="target", target_id="t",
            candidate_docs=candidate,
            impostor_docs=single_persona_impostors,
            iterations=10, seed=42,
        )
        assert result.refused is True
        assert "1 persona" in result.refusal_reason
        assert "7 docs" in result.refusal_reason


# ---------- Target self-entry filter (1.29.1) ---------------------


class TestTargetSelfEntry:
    """If the target file is also referenced by a manifest entry —
    same resolved path — the harness must filter that entry before
    running. Otherwise the target self-normalizes (proportion → 1.0)
    against itself, biasing the report. Reproduces the reviewer-
    flagged P2.
    """

    def test_resolved_path_filter_drops_overlap(self, tmp_path):
        target_file = tmp_path / "alice_draft.txt"
        target_file.write_text("identical text", encoding="utf-8")
        # Build entries by hand with resolved_path set
        entry_overlap = gi.CorpusEntry(
            id="alice_1",
            text="some text",
            persona="alice", register="blog_essay", author="A",
            corpus_role="identity_baseline", impostor_for=[],
            word_count=100,
            resolved_path=target_file.resolve(),
        )
        entry_distinct = gi.CorpusEntry(
            id="alice_2",
            text="other text",
            persona="alice", register="blog_essay", author="A",
            corpus_role="identity_baseline", impostor_for=[],
            word_count=100,
            resolved_path=tmp_path / "different_file.txt",
        )
        result = gi._exclude_target_path(
            [entry_overlap, entry_distinct], target_file,
        )
        assert len(result) == 1
        assert result[0].id == "alice_2"

    def test_no_resolved_path_passes_through(self, tmp_path):
        """In-memory entries (no resolved_path) are not filtered."""
        target_file = tmp_path / "draft.txt"
        target_file.write_text("hello", encoding="utf-8")
        entry = gi.CorpusEntry(
            id="x", text="x", persona="alice", register="blog_essay",
            author="A", corpus_role="identity_baseline",
            impostor_for=[], word_count=10,
            resolved_path=None,
        )
        result = gi._exclude_target_path([entry], target_file)
        assert len(result) == 1

    def test_content_duplicate_at_other_path_dropped(self, tmp_path):
        """A COPY of the target under a DIFFERENT filename evades the path guard; the content guard
        (opt-in via target_text) drops it, so the target cannot win against its own copy among the
        impostors (which would bias the proportion toward 1.0)."""
        target_file = tmp_path / "alice_draft.txt"
        target_text = "The moral weather shifted and the quiet calculus began again in earnest."
        target_file.write_text(target_text, encoding="utf-8")
        dup = gi.CorpusEntry(
            id="dup", text=target_text,  # same content, DIFFERENT path
            persona="alice", register="blog_essay", author="A",
            corpus_role="identity_baseline", impostor_for=[], word_count=100,
            resolved_path=(tmp_path / "copy_elsewhere.txt").resolve(),
        )
        distinct = gi.CorpusEntry(
            id="distinct", text="An entirely different passage with its own cadence and concerns.",
            persona="bob", register="blog_essay", author="B",
            corpus_role="impostor", impostor_for=["alice"], word_count=100,
            resolved_path=(tmp_path / "distinct.txt").resolve(),
        )
        result = gi._exclude_target_path([dup, distinct], target_file, target_text=target_text)
        ids = {e.id for e in result}
        assert "dup" not in ids          # content-duplicate dropped
        assert "distinct" in ids         # genuinely different pool doc kept

    def test_content_guard_is_opt_in(self, tmp_path):
        """Without target_text the guard stays path-only (backward compatible)."""
        target_file = tmp_path / "t.txt"
        target_file.write_text("same body of text here", encoding="utf-8")
        dup = gi.CorpusEntry(
            id="dup", text="same body of text here",
            persona="a", register="blog_essay", author="A",
            corpus_role="identity_baseline", impostor_for=[], word_count=10,
            resolved_path=(tmp_path / "other.txt").resolve(),
        )
        # no target_text -> content match NOT applied -> duplicate survives (path differs)
        assert len(gi._exclude_target_path([dup], target_file)) == 1

    def test_load_manifest_populates_resolved_path(self, tmp_path):
        text_path = tmp_path / "essay.txt"
        text_path.write_text("body of the essay" * 20, encoding="utf-8")
        manifest = tmp_path / "manifest.jsonl"
        manifest.write_text(
            json.dumps({
                "id": "essay_1",
                "path": "essay.txt",
                "persona": "alice",
                "register": "blog_essay",
                "corpus_role": "identity_baseline",
                "use": ["baseline"],
            }) + "\n",
            encoding="utf-8",
        )
        entries = gi._load_manifest(manifest)
        assert len(entries) == 1
        assert entries[0].resolved_path is not None
        assert entries[0].resolved_path == text_path.resolve()

# A passage-deduped manifest (rows carrying the spec-36 `passage_dedup` marker).
_GUARD_MARKED_ROWS = [
    {"id": "docA#p0000",
     "text": "The harbor lights came on one at a time along the western pier while the last of the fishing boats turned home tonight.",
     "passage_dedup": {"source_doc_id": "docA", "ordinal": 0}},
    {"id": "docB#p0000",
     "text": "Quantum entanglement continues to defy ordinary intuition about locality and separability in ways that still puzzle working physicists today somehow.",
     "passage_dedup": {"source_doc_id": "docB", "ordinal": 0}},
    {"id": "docC#p0000",
     "text": "Parliament debated the contentious measure for many hours before adjourning without any clear resolution late that long gray winter evening.",
     "passage_dedup": {"source_doc_id": "docC", "ordinal": 0}},
]


# --- spec 36: the pool guard must NOT creep onto this comparison pool -------
#
# Contract 17 (negative control). general_imposters defines its own
# `_load_manifest`, so it matches the coverage sweep's loader pattern — but it is
# an impostor-pool COMPARISON consumer, and near-dup dedup of an impostor pool is
# legitimate and sometimes required (#306/#307's purpose rule). Refusing a
# passage-deduped pool here would be the exact inversion the guard forbids.

def test_pool_guard_does_not_creep_onto_the_impostor_pool(tmp_path):
    import pool_guard  # type: ignore

    corpus = tmp_path / "texts"
    corpus.mkdir()
    rows = []
    for i, r in enumerate(_GUARD_MARKED_ROWS):
        f = corpus / f"{i}.txt"
        f.write_text(r["text"], encoding="utf-8")
        rows.append({
            "id": r["id"], "path": f"texts/{i}.txt", "persona": "p",
            "register": "blog_essay", "corpus_role": "impostor",
            "passage_dedup": r["passage_dedup"],
        })
    m = tmp_path / "marked.jsonl"
    m.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    # The marker is present...
    assert len(pool_guard.scan_manifest_for_passage_dedup(m)) == 3
    # ...and this surface's own loader consumes the pool anyway, unrefused.
    entries = gi._load_manifest(m)
    assert [e.id for e in entries] == [r["id"] for r in rows]
    assert "pool_guard" not in Path(gi.__file__).read_text(encoding="utf-8")


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
