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
    assert data["candidate_persona"] == "alice"
    assert data["n_impostors"] == 6
    assert data["proportion"] >= 0.9
    assert data["decision"] == "consistent_with_candidate"


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


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
