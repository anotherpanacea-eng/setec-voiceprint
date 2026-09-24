"""Behavior and posture checks for the Spec 82 M1 profile and mechanical fixtures."""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from contextlib import redirect_stdout

import pytest

import generate_verbatim_mosaic_fixture as generator
import originality_audit as original
import segmentation_feature_lens as lens
import verbatim_mosaic_audit as mosaic
import variance_audit as variance


POOL = [
    ("a", "Beyond the quiet harbor the lantern keeper opened every shutter before the evening tide arrived. "
          "The old vessel rested beside the stone quay after its final journey ended."),
    ("b", "Across the broad meadow a patient gardener counted every row of seedlings before the morning rain began. "
          "The narrow path curved past the orchard while distant bells marked the hour."),
    ("c", "Inside the village hall the clerk arranged the faded ledgers before the council gathered for its session. "
          "The wooden door stood open as the final visitor arrived from the road."),
]


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def test_originality_opt_in_preserves_existing_payload():
    target = POOL[0][1] + " Separately, the matter continued. " + POOL[1][1]
    old = original.audit_originality(target, POOL, min_ngram=8)
    new = original.audit_originality(target, POOL, min_ngram=8, include_spans=True)
    assert {k: v for k, v in new.items() if k != "all_spans"} == old
    assert {s["source"] for s in new["all_spans"]} == {"a", "b"}


def test_fixture_is_deterministic_and_coverage_is_source_grounded():
    first = generator.generate_fixture(POOL, seed="spec82", paragraphs=3)
    assert first == generator.generate_fixture(POOL, seed="spec82", paragraphs=3)
    text, labels = first
    result = mosaic.audit_mosaic(text, POOL, min_ngram=8)
    assert result["n_distinct_sources"] == 3
    assert result["n_counted_spans"] >= 3
    assert result["coverage"] > .65
    assert all(label["source"] in {"a", "b", "c"} for label in labels if label["kind"] == "copied")
    assert sum(label["length"] for label in labels) == len(original._tokens(text))
    assert result["junctions"] and all(0 <= j["distance"] <= 1
                                        for j in result["junctions"] if j["distance"] is not None)
    recovered = original.audit_originality(text, POOL, min_ngram=8, include_spans=True)["all_spans"]
    source_by_token = {}
    for span in recovered:
        for token in range(span["start"], span["start"] + span["length"]):
            source_by_token[token] = span["source"]
    for label in labels:
        for token in range(label["start"], label["start"] + label["length"]):
            assert source_by_token.get(token) == label["source"]


def test_fixture_cli_writes_token_labels(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    for source, passage in POOL:
        (pool / f"{source}.txt").write_text(passage, encoding="utf-8")
    target = tmp_path / "mosaic.txt"
    labels_path = tmp_path / "labels.json"
    assert generator.main(["--reference-dir", str(pool), "--seed", "spec82",
                           "--paragraphs", "3", "--out", str(target),
                           "--labels-out", str(labels_path)]) == 0
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    assert len(labels["tokens"]) == len(original._tokens(target.read_text(encoding="utf-8")))
    assert {row["kind"] for row in labels["tokens"]} == {"copied", "connective"}


def test_fixture_rejects_duplicate_paragraphs_with_false_source_labels():
    passage = ("In the quiet garden the patient keeper opened every gate before "
               "the morning bells sounded. A second sentence followed with "
               "enough distinct tokens for matching.")
    with pytest.raises(ValueError, match="unambiguous token-level fixture labels"):
        generator.generate_fixture([("a", passage), ("b", passage)],
                                   seed="1", paragraphs=2)


def test_zero_cover_and_empty_control_are_null():
    target = "No borrowed sequence is present here, though this account has several distinct words."
    result = mosaic.audit_mosaic(target, POOL, min_ngram=8)
    assert result["coverage"] == 0
    assert result["n_distinct_sources"] == 0
    assert result["sources_per_100_covered_tokens"] is None
    assert result["largest_source_share"] is None
    assert result["within_span_distance_quantiles"] is None


def test_unicode_lowercase_expansion_preserves_original_offsets():
    text = "\u0130. Alpha beta gamma delta. Red blue green gold."
    result = mosaic.audit_mosaic(
        text, [("a", "i alpha beta gamma delta"), ("b", "red blue green gold")],
        min_ngram=4, junction_sentences=1)
    assert result["junctions"][0]["char_offset"] == text.index("Red")
    assert [(s["token_start"], s["token_end"]) for s in result["sentence_features"]] == [
        (0, 1), (1, 5), (5, 9)]


def test_single_source_and_max_span_split_still_form_one_control_run():
    text = POOL[0][1]
    result = mosaic.audit_mosaic(text, [("a", text + " A distinct tail follows here.")],
                                 min_ngram=4, max_span=12)
    assert result["n_counted_spans"] > 1
    assert result["n_distinct_sources"] == 1
    assert result["junctions"] == []
    assert result["max_span_cap"] == 12 and result["longest_match_capped"]


def test_copied_run_final_sentence_counts_toward_within_span_control():
    """Regression: containment used sentence char offsets, which include the
    closing period no token covers, so every run's last sentence was dropped and
    a four-sentence copy yielded no two-plus-two within-span control."""
    source = POOL[0][1] + " " + POOL[1][1]
    result = mosaic.audit_mosaic(source, [("a", source)], min_ngram=8,
                                 junction_sentences=2)
    assert len(result["sentence_features"]) == 4
    assert result["junctions"] == []
    assert result["within_span_distance_quantiles"] is not None


def test_cap_split_does_not_invent_source_join_or_extra_source():
    words = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa"
    short = " ".join(words.split()[:8])
    result = mosaic.audit_mosaic(words + " unmatched", [("a", short), ("b", words)],
                                 min_ngram=4, max_span=8)
    assert result["n_counted_spans"] == 2
    assert result["n_distinct_sources"] == 1
    assert not any(j["token_offset"] == 8 for j in result["junctions"])


def test_shared_lens_preserves_single_sentence_burstiness():
    for text in ("Hello world.", "Hello world. Another sentence follows."):
        sentences = [text[a:b] for a, b in lens.sentence_spans(text)]
        expected = variance.sentence_length_stats(sentences)["burstiness_B"]
        assert lens.window_features(text)["sent_shape_burstiness_B"] == pytest.approx(expected)


def test_all_document_sentences_supply_the_zscore_basis(monkeypatch):
    a, b = POOL[0][1], POOL[1][1]
    tail = "An unrelated account now changes the details and setting completely. " * 25
    text = a + " " + b + " " + tail
    seen = []
    actual = mosaic.z_score_against
    def record(rows, basis, names):
        seen.append((len(rows), len(basis)))
        return actual(rows, basis, names)
    monkeypatch.setattr(mosaic, "z_score_against", record)
    result = mosaic.audit_mosaic(text, [("a", a), ("b", b)])
    assert seen == [(4, 2 * len(result["sentence_features"]) - 1)]
    assert len(result["sentence_features"]) > seen[0][0]


def test_two_sentence_windows_have_non_degenerate_document_basis():
    sentences = ["Hello world.", "Goodbye town.", "Different prose."]
    basis = [lens.window_features(s) for s in sentences]
    basis += [lens.window_features(" ".join(sentences[i:i + 2])) for i in range(2)]
    pair = [lens.window_features("Hello world. Goodbye town."),
            lens.window_features("Different prose.")]
    z = lens.z_score_against(pair, basis, ["sent_shape_n_sentences"])
    assert all(abs(row["sent_shape_n_sentences"]) < 2 for row in z)
    absent = lens.z_score_against([{"ch3:xyz": 0.5}], basis, ["ch3:xyz"])
    assert absent == [{"ch3:xyz": 0.0}]


def test_char_vocabulary_uses_raw_counts_with_lexicographic_ties():
    varied = "".join(chr(0x400 + i) for i in range(300))
    target = "Ababa " + varied + " ends here. Xyzw ends here."
    result = mosaic.audit_mosaic(target, [("other", "Entirely unrelated reference words appear here instead")])
    sentences = lens.sentence_spans(target)
    windows = [target[a:b] for a, b in sentences]
    windows += [target[sentences[i][0]:sentences[i + 1][1]]
                for i in range(len(sentences) - 1)]
    counts = Counter()
    for window in windows:
        normalized = re.sub(r"\s+", " ", window.lower()).strip()
        for n in (3, 4, 5):
            counts.update(f"ch{n}:{normalized[i:i+n]}"
                          for i in range(len(normalized) - n + 1))
    expected = sorted(counts, key=lambda name: (-counts[name], name))[:256]
    assert result["feature_vocabulary"][-256:] == expected


def test_cli_envelope_self_exclusion_and_recursive_no_verdict(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text(POOL[0][1] + " And then a new scene began. " + POOL[1][1], encoding="utf-8")
    manifest = tmp_path / "pool.jsonl"
    manifest.write_text("\n".join(json.dumps({"id": src, "text": passage}) for src, passage in POOL)
                        + "\n" + json.dumps({"id": "self", "text": target.read_text(encoding="utf-8")}) + "\n",
                        encoding="utf-8")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = mosaic.main(["--target", str(target), "--manifest", str(manifest), "--json"])
    envelope = json.loads(stdout.getvalue())
    assert rc == 0 and envelope["available"]
    assert envelope["task_surface"] == "set_level_diversity"
    assert envelope["results"]["n_distinct_sources"] == 2
    assert envelope["results"]["assumptions"]["n_dropped_self"] == 1
    assert envelope["results"]["sentence_features"]
    assert {"source_join_distance_quantiles", "coverage_join_distance_quantiles"} <= envelope["results"].keys()
    assert not ({"is_ai", "is_human", "verdict", "label", "selection_key"}
                & {str(key).lower() for key in _walk_keys(envelope["results"])})


def test_empty_target_and_pool_refuse_as_bad_input(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("", encoding="utf-8")
    pool = tmp_path / "pool"
    pool.mkdir()
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = mosaic.main(["--target", str(target), "--reference-dir", str(pool), "--json"])
    envelope = json.loads(stdout.getvalue())
    assert rc == 3 and not envelope["available"]
    assert envelope["reason_category"] == "bad_input"


def test_operator_limits_bound_work_and_refuse_overrides(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text(POOL[0][1] + " Some further different words appeared here.", encoding="utf-8")
    pool = tmp_path / "pool"
    pool.mkdir()
    (pool / "a.txt").write_text(POOL[0][1], encoding="utf-8")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = mosaic.main(["--target", str(target), "--reference-dir", str(pool),
                          "--max-target-tokens", "5", "--json"])
    envelope = json.loads(stdout.getvalue())
    assert rc == 3 and envelope["reason_category"] == "bad_input"
    assert "limit is 5" in envelope["reason"]
    with pytest.raises(ValueError, match="hard limit"):
        mosaic.audit_mosaic(target.read_text(), POOL, max_target_tokens=60_001)
    with pytest.raises(ValueError, match="hard limit"):
        mosaic.audit_mosaic(target.read_text(), POOL, max_span=1_025)
