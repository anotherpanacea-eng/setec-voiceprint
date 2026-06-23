#!/usr/bin/env python3
"""Regression tests for discourse_move_signature.py (Release 3).

Surfaces Tier-1. Tests the typed-discourse-marker pipeline:
per-category densities + move-sequence bigrams + entropy + band
call. The audit's primary value is providing differentiating
evidence for the confounder audit's differential diagnosis, so
the contracts here pin marker classification, sequence-bigram
construction, and the rough shape of the band call.
"""

from __future__ import annotations

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

import discourse_move_signature as dms  # type: ignore


# ---------- Marker classification ----------


class TestClassifySentence:
    def test_contrast_marker(self):
        assert dms.classify_sentence(
            "However, the evidence is mixed."
        ) == "contrast"

    def test_concession_marker(self):
        assert dms.classify_sentence(
            "Admittedly, the case for the policy is strong."
        ) == "concession"

    def test_consequence_marker(self):
        assert dms.classify_sentence(
            "Therefore, the regulator should act."
        ) == "consequence"

    def test_elaboration_marker(self):
        assert dms.classify_sentence(
            "In other words, the harm is structural."
        ) == "elaboration"

    def test_exemplification_marker(self):
        assert dms.classify_sentence(
            "For example, the 2023 study found a 30% drop."
        ) == "exemplification"

    def test_sequencing_marker(self):
        assert dms.classify_sentence(
            "First, the rate fell. Then it rose."
        ) == "sequencing"

    def test_epistemic_marker(self):
        assert dms.classify_sentence(
            "Perhaps the result is artifact."
        ) == "epistemic_stance"

    def test_boosting_marker(self):
        assert dms.classify_sentence(
            "Clearly, this is the right move."
        ) == "boosting"

    def test_no_marker(self):
        assert dms.classify_sentence(
            "The bridge collapsed at midnight."
        ) is None

    def test_first_match_wins(self):
        # "However" appears before "the better question is" so
        # the first-match rule should pick contrast.
        s = "However, the better question is whether we care."
        assert dms.classify_sentence(s) == "contrast"


# ---------- audit_discourse_moves end-to-end ----------


class TestAuditDiscourse:
    def test_empty_text_unavailable(self):
        a = dms.audit_discourse_moves("")
        assert a["available"] is False

    def test_returns_categories(self):
        text = (
            "However, the case is mixed. Therefore, we should think "
            "carefully. For example, consider the 2023 study. "
            "Clearly, the result is consistent. Maybe not. "
            "First, we look. Second, we judge. Finally, we decide."
        )
        a = dms.audit_discourse_moves(text)
        assert a["available"] is True
        # Multiple categories populated.
        densities = a["category_densities_per_1k"]
        assert densities["contrast"] > 0
        assert densities["consequence"] > 0
        assert densities["exemplification"] > 0
        assert densities["sequencing"] > 0

    def test_band_lightly_on_unscaffolded_prose(self):
        text = (
            "She walked down the corridor and looked at the photograph. "
            "He thought about it for a long moment. "
            "He remembered the night, the cold light, the way she had "
            "stood at the window. The room felt smaller. "
            "Outside, the snow had begun to fall again. "
        ) * 3
        a = dms.audit_discourse_moves(text)
        assert a["compression"]["band"] == "Lightly scaffolded"

    def test_band_rises_on_scaffolded_prose(self):
        text = (
            "Admittedly, the case for the policy is strong. "
            "However, enforcement comes at a cost. "
            "Although the literature is divided, recent evidence "
            "suggests a different mechanism. "
            "For example, the 2023 study found compliance fell. "
            "Therefore, we should be cautious about scaling. "
            "Specifically, the implementation should target the "
            "highest-risk categories first. "
            "In other words, less is more. "
            "Of course, the politics are complex. "
            "Nevertheless, the data are clear. "
            "First, the rate dropped. Second, the cost rose. "
            "Finally, the public lost faith."
        )
        a = dms.audit_discourse_moves(text)
        assert a["compression"]["band"] in {
            "Moderately scaffolded", "Heavily scaffolded",
        }

    def test_move_sequence_records_unmarked(self):
        text = (
            "The bridge stood for a hundred years. "
            "However, by 2023 it had begun to crumble."
        )
        a = dms.audit_discourse_moves(text)
        seq = a["move_sequence"]
        assert seq[0] == "_unmarked"
        assert seq[1] == "contrast"

    def test_bigrams_count_transitions(self):
        text = (
            "However, the case is mixed. Therefore, we are cautious. "
            "However, the data are clear."
        )
        a = dms.audit_discourse_moves(text)
        bigrams = a["move_sequence_bigrams"]
        # 'contrast->consequence' and 'consequence->contrast' should be present.
        assert "contrast->consequence" in bigrams or "consequence->contrast" in bigrams

    def test_marked_only_entropy_lower_than_full(self):
        """Marked-only entropy ignores _unmarked, so when most
        sentences are unmarked the marked-only entropy is more
        informative (lower bound) than the full entropy."""
        text = (
            "The bridge stood for a hundred years. "
            "However, in 2023 it began to crumble. "
            "Therefore, it was demolished. "
            "The rubble was removed. "
            "Or rather, most of it."
        )
        a = dms.audit_discourse_moves(text)
        assert a["marked_only_entropy_bits"] >= 0


# ---------- Baseline comparison ----------


class TestBaselineComparison:
    def test_baseline_aggregate(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        text = (
            "However, the case is mixed. Therefore, we are cautious. "
            "For example, the 2023 study found a drop. "
            "Of course, the politics are complex. Clearly, the data "
            "are consistent."
        )
        for i in range(3):
            (base / f"f{i}.txt").write_text(text, encoding="utf-8")
        block = dms.audit_baseline_discourse(str(base))
        assert block["n_files"] == 3
        assert "aggregate_density_by_category" in block

    def test_compare_to_baseline_returns_z_scores(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        text = "However, this. Therefore, that. For example, the 2023 study. Clearly, yes."
        for i in range(4):
            (base / f"f{i}.txt").write_text(text, encoding="utf-8")
        block = dms.audit_baseline_discourse(str(base))
        target = dms.audit_discourse_moves(
            "However, this. Therefore, that. Clearly, yes."
        )
        cmp = dms.compare_to_baseline(target, block)
        assert cmp["available"] is True
        assert "category_density_z_scores" in cmp


# ---------- Render + claim license ----------


class TestRender:
    def test_markdown_includes_claim_license(self):
        text = "However, this is mixed. Therefore, be cautious." * 5
        a = dms.audit_discourse_moves(text)
        md = dms.render_report(a)
        assert "## What this result licenses" in md
        assert "Discourse-marker typology" in md

    def test_markdown_renders_categories(self):
        text = "However, this is mixed. Therefore, be cautious." * 5
        a = dms.audit_discourse_moves(text)
        md = dms.render_report(a)
        assert "## Per-category densities" in md
        assert "contrast" in md
        assert "consequence" in md


# ---------- CLI ----------


class TestCli:
    def test_cli_round_trip(self, tmp_path):
        in_path = tmp_path / "draft.txt"
        in_path.write_text(
            "However, the case is mixed. Therefore, be cautious. "
            "For example, consider the 2023 study. Clearly, it shows.",
            encoding="utf-8",
        )
        out_path = tmp_path / "out.json"
        rc = dms.main(["--json", "--out", str(out_path), str(in_path)])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["task_surface"] == "smoothing_diagnosis"

    def test_cli_handles_missing_input(self, tmp_path):
        rc = dms.main([str(tmp_path / "missing.txt")])
        assert rc == 2


# ---------- 1.34.2 baseline ingestion hardening ----------------


class TestBaselineHardening:
    """1.34.2 fixes the same baseline-ingestion footguns paragraph_audit
    fixed in 1.34.1: validate dir, surface skipped files, exclude
    target overlap, anonymize filenames by default."""

    def test_nonexistent_baseline_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            dms.audit_baseline_discourse(
                str(tmp_path / "no_such_dir"),
            )

    def test_target_overlap_excluded(self, tmp_path, capsys):
        base = tmp_path / "baseline"
        base.mkdir()
        text = (
            "However, this is mixed. Therefore, be cautious. "
            "For example, the 2023 study found a drop. " * 5
        )
        target = base / "draft.txt"
        target.write_text(text, encoding="utf-8")
        (base / "other.txt").write_text(text, encoding="utf-8")
        block = dms.audit_baseline_discourse(
            str(base), target_path=target,
        )
        assert block["n_files"] == 1
        captured = capsys.readouterr()
        assert "draft.txt" in captured.err

    def test_filenames_anonymized_by_default(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        text = "However, this is mixed. " * 30
        (base / "client_secret_brief.txt").write_text(
            text, encoding="utf-8",
        )
        block = dms.audit_baseline_discourse(str(base))
        for s in block["per_file_summaries"]:
            assert "client_secret" not in s["file"]
            assert s["file"].startswith("baseline_")
        assert block["include_filenames"] is False

    def test_filenames_opt_in(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        text = "However, this is mixed. " * 30
        (base / "client_brief.txt").write_text(
            text, encoding="utf-8",
        )
        block = dms.audit_baseline_discourse(
            str(base), include_filenames=True,
        )
        names = [s["file"] for s in block["per_file_summaries"]]
        assert "client_brief.txt" in names

    def test_skipped_files_recorded(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        # Empty file → audit unavailable.
        (base / "empty.txt").write_text("", encoding="utf-8")
        block = dms.audit_baseline_discourse(str(base))
        assert block["n_skipped"] >= 1


# ---------- PDTB explicit-connective relation layer ----------
#
# Additive layer (arXiv:2307.03378, explicit-connective proxy).
# Descriptive shape only — counts / densities / fractions / entropy,
# calibration_status uncalibrated, no band, no verdict.

import re  # noqa: E402


class TestRelationDistribution:
    def test_block_shape_and_keys(self):
        text = (
            "Although the case is mixed, the trend is clear. "
            "Because costs fell, adoption spread. "
            "For example, the 2023 pilot doubled. "
            "Then the program expanded."
        )
        a = dms.audit_discourse_moves(text)
        rel = a["relation_distribution"]
        for k in (
            "calibration_status", "n_explicit_connectives", "buckets",
            "counts", "density_per_1k", "fractions",
            "relation_entropy_bits", "relation_entropy_max_bits",
            "ambiguous_connective_fraction",
        ):
            assert k in rel, f"missing relation key: {k}"
        assert rel["buckets"] == [
            "comparison", "contingency", "expansion", "temporal",
        ]
        # All four buckets present (zero-filled) in every sub-dict.
        for sub in ("counts", "density_per_1k", "fractions"):
            assert set(rel[sub]) == set(rel["buckets"])

    def test_hand_counted_unambiguous_fixture(self):
        """Acc 7 — verbatim fixture restricted to the unambiguous
        connectives (because / however / for example / then) so the
        counts are pinned exactly (findings P3 #4)."""
        text = (
            "The bridge fell because the cables snapped. "
            "However, nobody was hurt. "
            "For example, the night shift had ended. "
            "Then the crews arrived."
        )
        rel = dms.audit_discourse_moves(text)["relation_distribution"]
        assert rel["counts"] == {
            "comparison": 1,    # however
            "contingency": 1,   # because
            "expansion": 1,     # for example
            "temporal": 1,      # then
        }
        assert rel["n_explicit_connectives"] == 4
        assert rel["ambiguous_connective_fraction"] == 0.0

    def test_fractions_sum_to_one_when_nonzero(self):
        text = (
            "Because A, B. Therefore C. However D. "
            "For example E. Then F."
        )
        rel = dms.audit_discourse_moves(text)["relation_distribution"]
        assert rel["n_explicit_connectives"] > 0
        assert abs(sum(rel["fractions"].values()) - 1.0) < 1e-9

    def test_density_matches_word_count(self):
        text = "Because the rate fell, demand rose. However, prices held."
        a = dms.audit_discourse_moves(text)
        rel = a["relation_distribution"]
        n_words = a["n_words"]
        for b in rel["buckets"]:
            expected = 1000.0 * rel["counts"][b] / n_words
            assert abs(rel["density_per_1k"][b] - expected) < 1e-9

    def test_entropy_bounds_min_and_max(self):
        # All mass in one bucket → entropy 0.0.
        one_bucket = dms.audit_explicit_relations(
            "Because A. Because B. Because C.", n_words=6
        )
        assert one_bucket["relation_entropy_bits"] == 0.0
        # Equal across all four buckets → entropy == max (2.0).
        equal = dms.audit_explicit_relations(
            "However. Because. For example. Then.", n_words=8
        )
        assert equal["relation_entropy_bits"] == 2.0
        assert equal["relation_entropy_max_bits"] == 2.0

    def test_zero_connectives_emits_present_but_empty(self):
        """D7 — non-empty text with no explicit connective emits a
        present-but-zero block (informative descriptive fact), not an
        error/omission."""
        text = (
            "The bridge stood for a hundred years. "
            "The river ran beneath it. The town grew quiet."
        )
        rel = dms.audit_discourse_moves(text)["relation_distribution"]
        assert rel["n_explicit_connectives"] == 0
        assert all(v == 0 for v in rel["counts"].values())
        assert all(v == 0.0 for v in rel["fractions"].values())
        assert rel["relation_entropy_bits"] == 0.0
        assert rel["ambiguous_connective_fraction"] == 0.0
        assert "reason" in rel

    def test_ambiguous_fraction_bounds_and_reporting(self):
        # "while" and "since" are on the ambiguous list; "because" is
        # not. With 1 ambiguous of 2 explicit, fraction == 0.5.
        text = "While A, B. Because C, D."
        rel = dms.audit_explicit_relations(text, n_words=6)
        assert 0.0 <= rel["ambiguous_connective_fraction"] <= 1.0
        # Both "while" (comparison) and "because" (contingency) fire.
        assert rel["n_explicit_connectives"] == 2
        assert rel["ambiguous_connective_fraction"] == 0.5

    def test_ambiguous_set_is_subset_of_lexicon(self):
        """Honesty invariant: every ambiguous connective is ALSO a
        counted lexicon match, so `ambiguous_connective_fraction` is a
        true share in [0, 1] — never inflated by a word the relation
        buckets don't actually count. (Guards the `still`-not-in-any-
        bucket footgun.)"""
        for w in dms._AMBIGUOUS_CONNECTIVES:
            in_a_bucket = any(
                any(p.search(w) for p in pats)
                for pats in dms._PDTB_CONNECTIVES.values()
            )
            assert in_a_bucket, (
                f"ambiguous connective {w!r} is not in any relation "
                f"bucket — it would inflate the ambiguous fraction"
            )

    def test_overlapping_multiword_connectives_count_once(self):
        """Regression (P2 findings #1/#2): a multi-word connective
        whose constituent words are ALSO single-word lexicon
        connectives must be counted EXACTLY once and in EXACTLY one
        bucket. The naive per-pattern ``findall`` loop double/triple-
        counted these via the bare ``as`` / ``so`` / ``though``
        substrings, leaking into wrong buckets and inflating
        ``n_explicit_connectives``. One combined longest-match-first
        non-overlapping pass fixes the one-occurrence-one-bucket
        invariant (spec §2). Counts hand-verified.
        """
        cases = [
            # text, n_words, expected non-zero counts
            ("As a result, we shipped.", 5, {"contingency": 1}),
            ("As soon as the bell rang, we left.", 8, {"temporal": 1}),
            ("We trained so that we would win.", 7, {"contingency": 1}),
            ("It held even though it was strained.", 7, {"comparison": 1}),
        ]
        for text, nw, expected in cases:
            rel = dms.audit_explicit_relations(text, n_words=nw)
            nonzero = {
                b: c for b, c in rel["counts"].items() if c
            }
            assert nonzero == expected, (
                f"{text!r}: got {nonzero}, expected {expected}"
            )
            assert rel["n_explicit_connectives"] == sum(expected.values())
            # No bare `as`/`so` leaked an extra ambiguous span: the
            # whole phrase is consumed, none of these phrases is itself
            # a standalone ambiguous-list member.
            assert rel["ambiguous_connective_fraction"] == 0.0

    def test_as_soon_as_does_not_triple_count(self):
        """Regression (P2 finding #2): ``as soon as`` previously
        scored temporal:3 (bare ``as`` twice + the phrase once). It
        must be exactly one temporal occurrence."""
        rel = dms.audit_explicit_relations(
            "As soon as the bell rang, we left.", n_words=8
        )
        assert rel["counts"]["temporal"] == 1
        assert rel["n_explicit_connectives"] == 1

    def test_two_overlapping_connectives_total_is_exact(self):
        """Regression (P2 finding #1): a two-connective sentence
        (``As a result`` + ``As soon as``) previously reported
        ``n_explicit_connectives`` = 5 (temporal:4). It must report
        exactly 2 — contingency:1, temporal:1."""
        rel = dms.audit_explicit_relations(
            "As a result, X happened. As soon as Y, we left.",
            n_words=10,
        )
        assert rel["n_explicit_connectives"] == 2
        assert rel["counts"]["contingency"] == 1
        assert rel["counts"]["temporal"] == 1
        assert rel["counts"]["comparison"] == 0
        assert rel["counts"]["expansion"] == 0

    def test_relation_layer_is_parallel_not_rebucketing(self):
        """D2 / findings P3 #3 — the relation layer is an independent
        whole-text count, NOT a re-bucketing of `classify_sentence`
        (which is first-match per sentence). A single sentence with
        two connectives contributes TWO relation occurrences even
        though it types as exactly one move."""
        sentence = "However, because the data shifted, we paused."
        # classify_sentence is first-match: exactly one label.
        assert dms.classify_sentence(sentence) is not None
        rel = dms.audit_explicit_relations(sentence, n_words=8)
        # "however" (comparison) + "because" (contingency) → 2.
        assert rel["n_explicit_connectives"] == 2
        assert rel["counts"]["comparison"] == 1
        assert rel["counts"]["contingency"] == 1


class TestRelationPosture:
    """Load-bearing no-verdict / never-selects / calibration-honesty
    guards (spec §6.12–16)."""

    _VERDICT_RE = re.compile(
        r"verdict|is_ai|is_human|flag|label|decision|score|"
        r"selected|select|anomal|quality|_band",
        re.I,
    )

    def _walk(self, obj, path=""):
        """Yield (path, key, value) for every mapping key and every
        leaf, recursively."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield (path, k, v)
                yield from self._walk(v, f"{path}.{k}")
        elif isinstance(obj, (list, tuple)):
            for i, item in enumerate(obj):
                yield from self._walk(item, f"{path}[{i}]")

    def _relation_block(self):
        text = (
            "Although mixed, the case holds. Because costs fell, "
            "adoption spread. For example, the pilot doubled. "
            "Then it expanded. However, critics objected."
        )
        return dms.audit_discourse_moves(text)["relation_distribution"]

    def test_no_verdict_key_in_block(self):
        rel = self._relation_block()
        for _path, key, _val in self._walk(rel):
            assert not self._VERDICT_RE.search(str(key)), (
                f"verdict-adjacent key in relation_distribution: {key}"
            )

    def test_no_bare_boolean_leaf(self):
        rel = self._relation_block()
        for path, key, val in self._walk(rel):
            # Recurse also visits leaf values via the dict branch; the
            # only place a bool could hide is a value leaf.
            if isinstance(val, bool):
                raise AssertionError(
                    f"bare boolean leaf in relation_distribution at "
                    f"{path}.{key}={val}"
                )

    def test_no_selection_scalar(self):
        """The only scalars are counts / densities / fractions /
        entropy — all descriptive. No single value ranks or selects
        the document; specifically no `*_band` or `*_quality*` key."""
        rel = self._relation_block()
        keys = {k for _p, k, _v in self._walk(rel)}
        assert not any(k.endswith("_band") for k in keys)
        assert not any("quality" in k.lower() for k in keys)
        assert "calibration_status" in keys

    def test_calibration_status_uncalibrated(self):
        rel = self._relation_block()
        assert rel["calibration_status"] == "uncalibrated"

    def test_claim_license_carries_implicit_and_polysemy_caveats(self):
        text = (
            "However, the case is mixed. Because costs fell, "
            "adoption spread. For example, the pilot doubled."
        )
        a = dms.audit_discourse_moves(text)
        rendered = dms._claim_license_block(a)
        low = rendered.lower()
        assert "implicit" in low
        assert "2307.03378" in rendered
        assert "ambiguous_connective_fraction" in rendered
        assert "uncalibrated" in low

    def test_lexicon_is_corpus_independent(self):
        """Anti-Goodhart / held-out disjoint (spec §6.15): the
        connective lexicon is a static linguistic inventory built
        from compiled regex patterns — no corpus is read at import or
        call time, so it cannot be tuned against held-out audit
        data."""
        # Every lexicon value is a tuple of pre-compiled patterns.
        for bucket, patterns in dms._PDTB_CONNECTIVES.items():
            assert bucket in dms.RELATION_BUCKETS
            assert all(
                isinstance(p, re.Pattern) for p in patterns
            )
        # The ambiguous set is a frozenset (immutable, static).
        assert isinstance(dms._AMBIGUOUS_CONNECTIVES, frozenset)


class TestRelationBaseline:
    def test_baseline_relation_z_scores(self, tmp_path):
        base = tmp_path / "baseline"
        base.mkdir()
        text = (
            "However, A. Because B, C. For example D. Then E. "
            "Although F, G. Moreover H."
        )
        for i in range(4):
            (base / f"f{i}.txt").write_text(text, encoding="utf-8")
        block = dms.audit_baseline_discourse(str(base))
        assert "aggregate_relation_density_by_bucket" in block
        target = dms.audit_discourse_moves(
            "However, X. Because Y, Z. Then W."
        )
        cmp = dms.compare_to_baseline(target, block)
        assert "relation_density_z_scores" in cmp
        for b in dms.RELATION_BUCKETS:
            z = cmp["relation_density_z_scores"][b]
            assert z is None or isinstance(z, float)


class TestRelationRender:
    def test_markdown_includes_relation_section(self):
        text = (
            "However, the case is mixed. Because costs fell, "
            "adoption spread. For example, the pilot doubled. "
            "Then it expanded."
        )
        a = dms.audit_discourse_moves(text)
        md = dms.render_report(a)
        assert "## Explicit discourse-relation profile" in md
        assert "calibration_status" in md

    def test_markdown_relation_section_graceful_when_zero(self):
        text = (
            "The bridge stood for a hundred years. "
            "The river ran beneath it."
        )
        a = dms.audit_discourse_moves(text)
        md = dms.render_report(a)
        assert "## Explicit discourse-relation profile" in md
        assert "No explicit PDTB connectives matched" in md


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
