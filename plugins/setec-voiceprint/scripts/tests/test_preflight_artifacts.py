"""Synthetic artifact and calibration contracts."""

from __future__ import annotations

import json
from pathlib import Path
import random
import subprocess
import sys
import time

import pytest

import preprocessing
from setec.preflight.artifacts import run_calibrate, run_census
from setec.preflight.artifacts_core import (
    ARTIFACT_TYPES, CEILINGS, DETECTOR_SHA256, detect_artifacts,
    load_artifact_detail, load_artifact_receipt, load_calibration_receipt,
)
from setec.preflight.common import (
    Refusal, WorkBudget, canonical_json, load_manifest, plain_hash,
    record_set_sha256,
)


def _manifest(root: Path, texts: list[bytes], *, stratum: str = "cell") -> Path:
    root.mkdir()
    rows = []
    for index, data in enumerate(texts):
        name = f"row{index}.txt"
        (root / name).write_bytes(data)
        rows.append({"id": f"r{index}", "group_id": f"g{index}",
                     "stratum": stratum, "path": name,
                     "span": {"source_path": name,
                              "source_bytes_sha256": plain_hash(data),
                              "start_byte": 0, "end_byte": len(data)}})
    manifest = root / "packet.jsonl"
    manifest.write_bytes(b"".join(canonical_json(row) for row in rows))
    return manifest


def _policy(root: Path, *, disposition: str = "review") -> Path:
    path = root / "policy.json"
    path.write_bytes(canonical_json({
        "schema": "setec-preflight-artifact-policy/1",
        "dispositions": {name: ("review" if name == "verse" and disposition == "refuse"
                                else disposition) for name in
                         ("markup", "apparatus", "verse", "encoding_normalization")},
        "coordination_strata": ["cell"],
    }))
    return path


@pytest.mark.parametrize("name,text,line,paragraph", [
    ("markup_html", "lead\n\n<p>hello</p>", 3, 2),
    ("markup_css", ".note { color: red }", 1, 1),
    ("markup_script", "<script>hello</script>", 1, 1),
    ("navigation_boilerplate", "Home", 1, 1),
    ("markdown_apparatus", "# Heading", 1, 1),
    ("tei_xml_apparatus", "<TEI>", 1, 1),
    ("footnote_definition", "[^1]: note", 1, 1),
    ("page_header", "Page 12", 1, 1),
    ("line_number", "Before\n12\nAfter", 2, 1),
    ("ocr_hyphenation", "well-\nknown", 1, 1),
    ("running_head", "Running Head\n\nA\n\nRunning Head\n\nB\n\nRunning Head", 1, 1),
    ("truncation_marker", "[truncated]", 1, 1),
    ("unbalanced_fence", "```python\ncode", 1, 1),
    ("verse_likely", "red blue green four\nfive six seven eight\nnine ten eleven twelve", 1, 1),
    ("replacement_character", "a\ufffdb", 1, 1),
    ("private_use_character", "a\ue000b", 1, 1),
])
def test_each_detector_positive(name, text, line, paragraph):
    observations = detect_artifacts(text, WorkBudget(CEILINGS))
    assert (name, line, paragraph) in {(item.artifact_type, item.line, item.paragraph)
                                       for item in observations}


def test_census_private_detail_and_coordination_receipt(tmp_path):
    manifest = _manifest(tmp_path / "packet", [b"<p>secret canary</p>", b"plain prose"])
    policy = _policy(tmp_path)
    out = tmp_path / "out"
    committed, statuses = run_census(manifest, policy, out)
    assert statuses == {"intake": "passed", "artifact_census": "needs_human_review"}
    detail_raw = (out / "detail.json").read_bytes()
    receipt_raw = (out / "receipt.json").read_bytes()
    assert committed == receipt_raw
    detail = load_artifact_detail(out / "detail.json", plain_hash(detail_raw))
    receipt = load_artifact_receipt(out / "receipt.json", plain_hash(receipt_raw))
    assert receipt["detail_sha256"] == plain_hash(detail_raw)
    assert receipt["record_set_sha256"] == record_set_sha256(load_manifest(manifest).records)
    assert receipt["detector_sha256"] == DETECTOR_SHA256
    assert "secret canary" not in receipt_raw.decode()
    assert detail["observations"][0]["line"] == 1
    assert receipt["stratum_counts"] is None
    assert receipt["stratum_outcome_counts"] is None


def _labels(root: Path, rows: list[dict]) -> Path:
    path = root / "labels.json"
    path.write_bytes(canonical_json({"schema": "setec-preflight-artifact-labels/1",
                                     "register_cells": ["cell"], "labels": rows}))
    return path


def _label(index: int, label: str, variation: str | None = None) -> dict:
    return {"id": f"r{index}", "label": label, "register_cell": "cell",
            "variation_class": variation}


def test_calibration_pass_and_intake_refusal(tmp_path):
    manifest = _manifest(tmp_path / "packet", [b"<p>artifact</p>", b"\x07",
                                                b"dialect prose", b"archaic spelling",
                                                b"purposeful roughness", b"unusual syntax"])
    labels = _labels(tmp_path, [_label(0, "artifact"), _label(1, "artifact"),
                                _label(2, "legitimate_variation", "dialect"),
                                _label(3, "legitimate_variation", "archaic_spelling"),
                                _label(4, "legitimate_variation", "purposeful_roughness"),
                                _label(5, "legitimate_variation", "unusual_valid_syntax")])
    policy = _policy(tmp_path)
    out = tmp_path / "calibrated"
    committed, statuses = run_calibrate(manifest, plain_hash(manifest.read_bytes()), labels,
                                        plain_hash(labels.read_bytes()), policy, out)
    assert statuses == {"calibration": "passed"}
    raw = (out / "receipt.json").read_bytes()
    assert committed == raw
    receipt = load_calibration_receipt(out / "receipt.json", plain_hash(raw))
    assert receipt["reason_counts"]["ok"] == 1
    assert receipt["cell_counts"]["cell"]["artifact"]["intake_refusal"] == 1
    assert b"r0" not in raw and b"artifact</p>" not in raw
    with pytest.raises(Refusal, match="calibration_binding"):
        run_calibrate(manifest, "0" * 64, labels, plain_hash(labels.read_bytes()),
                      policy, tmp_path / "wrong")


def test_preprocessing_alias_and_closed_table():
    assert preprocessing.is_css_rule_block is preprocessing._looks_like_css_block
    assert len(ARTIFACT_TYPES) == 16


@pytest.mark.parametrize("text,name", [
    ("Published on {date:%Y-%m-%d} by the editor.", "markup_css"),
    ("Status {key: value} is clear.", "markup_css"),
    ("one = two", "markup_script"),
    ("only= this prose", "markup_script"),
    ("The quoted note [^1] is inline.", "footnote_definition"),
    ("```\ninside\n```", "unbalanced_fence"),
    ("well-\nKnown", "ocr_hyphenation"),
])
def test_legitimate_detector_near_misses(text, name):
    assert name not in {item.artifact_type for item in
                        detect_artifacts(text, WorkBudget(CEILINGS))}


@pytest.mark.parametrize("data", [b"\x07", "\u0085".encode(), b"\xef\xbb\xbftext",
                                          b"\0", b"\xff"])
def test_intake_refusals_stay_out_of_census(tmp_path, data):
    manifest = _manifest(tmp_path / "packet", [data])
    with pytest.raises(Refusal, match="input_contract"):
        run_census(manifest, _policy(tmp_path), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_calibration_reloader_rejects_forged_pass(tmp_path):
    manifest = _manifest(tmp_path / "packet", [b"ordinary artifact row",
                                                b"dialect prose", b"archaic spelling",
                                                b"purposeful roughness", b"unusual syntax"])
    labels = _labels(tmp_path, [_label(0, "artifact"),
                                _label(1, "legitimate_variation", "dialect"),
                                _label(2, "legitimate_variation", "archaic_spelling"),
                                _label(3, "legitimate_variation", "purposeful_roughness"),
                                _label(4, "legitimate_variation", "unusual_valid_syntax")])
    out = tmp_path / "out"
    run_calibrate(manifest, plain_hash(manifest.read_bytes()), labels,
                  plain_hash(labels.read_bytes()), _policy(tmp_path), out)
    receipt = json.loads((out / "receipt.json").read_bytes())
    assert receipt["calibration_status"] == "failed"
    receipt["calibration_status"] = "passed"
    receipt["reason_counts"]["ok"] = 1
    receipt["reason_counts"]["artifact_missed"] = 0
    forged = canonical_json(receipt)
    path = tmp_path / "forged.json"
    path.write_bytes(forged)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_calibration_receipt(path, plain_hash(forged))


@pytest.mark.parametrize("text", ["<ſcript>prose</ſcript>", "<a onkK=1>",
                                         "<a onlı=1>"])
def test_script_ascii_case_only(text):
    assert "markup_script" not in {item.artifact_type for item in
                                   detect_artifacts(text, WorkBudget(CEILINGS))}


def test_census_reloader_rejects_contradictory_aggregates(tmp_path):
    manifest = _manifest(tmp_path / "packet", [b"<p>x</p>"])
    out = tmp_path / "out"
    run_census(manifest, _policy(tmp_path), out)
    receipt = json.loads((out / "receipt.json").read_bytes())
    receipt["artifact_counts"]["markup_html"] = {"observations": 0, "records": 0}
    receipt["category_counts"]["markup"] = 0
    raw = canonical_json(receipt)
    forged = tmp_path / "forged.json"
    forged.write_bytes(raw)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_artifact_receipt(forged, plain_hash(raw))


def test_descriptor_binds_compiled_patterns(monkeypatch):
    import re
    import setec.preflight.artifacts_core as core

    original = core._descriptor()
    monkeypatch.setattr(core, "ASCII_LETTER", re.compile("."))
    assert core._descriptor() != original


@pytest.mark.parametrize("block,expected", [
    (".note { color: red }", True),
    ("body { margin: 0; color: blue }", True),
    ("#nav { display: none }", True),
    (".box { font-size: 14px; }", True),
    (".box { --custom: 1 }", False),
    ("a:hover { color: blue }", True),
    (".box { padding: 4px; border: 0 }", True),
    ("input[type=text] { width: 4px }", True),
    (".box { -webkit-transform: rotate(0) }", True),
    ("p { line-height: 2 }", True),
    ("Published on {date:%Y-%m-%d}", False),
    ("Status {server: prod}", False),
    ("Issue #123 {status: open}", False),
    ("C++ {mode: fast}", False),
    ("Option [x] {enabled: true}", False),
    ("{date}", False),
    ("A long prose sentence {key: value}", False),
    ("Status {server: prod} is healthy.", False),
    ("Published on {date:%Y-%m-%d} by the desk.", False),
    ("Deploy to {server: prod} after smoke tests.", False),
])
def test_css_gate_pinned_decisions(block, expected):
    assert preprocessing.is_css_rule_block(block) is expected


@pytest.mark.parametrize("name,text", [
    ("markup_html", "<a no closing angle"),
    ("markup_css", "{date}"),
    ("markup_script", "one = two"),
    ("navigation_boilerplate", "homecoming"),
    ("markdown_apparatus", "#hashtag in prose"),
    ("tei_xml_apparatus", "<TEIsomething>"),
    ("footnote_definition", "An inline [^1] reference"),
    ("page_header", "Page 1234567"),
    ("line_number", "Before\n1234567\nAfter"),
    ("ocr_hyphenation", "well-\nKnown"),
    ("running_head", "Head\n\nOther\n\nHead"),
    ("truncation_marker", "[truncated?]"),
    ("unbalanced_fence", "```\ninside\n```"),
    ("verse_likely", "one two three four\nfive six seven eight"),
    ("replacement_character", "ordinary prose"),
    ("private_use_character", "ordinary prose"),
])
def test_each_detector_near_miss(name, text):
    assert name not in {item.artifact_type for item in
                        detect_artifacts(text, WorkBudget(CEILINGS))}


def test_running_head_and_verse_integer_boundaries():
    def fires(text, name):
        return name in {item.artifact_type for item in
                        detect_artifacts(text, WorkBudget(CEILINGS))}

    assert fires("abc\n\nx\n\nabc\n\ny\n\nabc", "running_head")
    assert not fires("ab\n\nx\n\nab\n\ny\n\nab", "running_head")
    eighty = "H" * 80
    eighty_one = "H" * 81
    assert fires(f"{eighty}\n\nx\n\n{eighty}\n\ny\n\n{eighty}", "running_head")
    assert not fires(f"{eighty_one}\n\nx\n\n{eighty_one}\n\ny\n\n{eighty_one}",
                     "running_head")
    line = "one two three four"
    assert fires("\n".join([line] * 3), "verse_likely")
    assert fires("\n".join([line] * 40), "verse_likely")
    assert not fires("\n".join([line] * 41), "verse_likely")
    assert not fires("one two three\nfour five six\nseven eight nine ten eleven",
                     "verse_likely")
    assert fires("one two three four\nfive six seven eight\nnine ten eleven twelve",
                 "verse_likely")
    four = ["one two three four"] * 4
    long_line = " ".join(f"word{i}" for i in range(13))
    assert fires("\n".join([*four, long_line]), "verse_likely")
    assert not fires("\n".join([*four[:3], long_line, long_line]), "verse_likely")


def test_html_prefilter_agrees_with_reused_pattern_on_seeded_lines():
    from setec.preflight.artifacts_core import HTML_TAG, _tag_segments

    rng = random.Random(91811)
    alphabet = '<>/a =:-"\t\u00a0'
    for _ in range(10_000):
        line = "".join(rng.choices(alphabet, k=rng.randrange(32)))
        assert bool(_tag_segments(line)) == bool(HTML_TAG.search(line))


@pytest.mark.parametrize("kind", ["unclosed_tag", "spaced_selector", "unclosed_brace"])
def test_adversarial_single_line_linear_runtime(kind):
    suffix = {"unclosed_tag": lambda: "<a" + " " * (4 * 1024 * 1024 - 2),
              "spaced_selector": lambda: "a" + " " * (4 * 1024 * 1024 - 1),
              "unclosed_brace": lambda: ".note " + " " * (4 * 1024 * 1024 - 7) + "{"}[kind]()
    start = time.monotonic()
    detect_artifacts(suffix, WorkBudget(CEILINGS))
    assert time.monotonic() - start < 15


def test_each_artifact_work_counter_limit_and_one_over():
    text = ".note { color: red }\n\nRunning head\n\nRunning head\n\nRunning head"
    measured = WorkBudget(CEILINGS)
    detect_artifacts(text, measured)
    assert all(value > 0 for value in measured.used.values())
    detect_artifacts(text, WorkBudget(measured.used))
    for name, value in measured.used.items():
        ceilings = dict(CEILINGS)
        ceilings[name] = value - 1
        with pytest.raises(Refusal, match="work_limit"):
            detect_artifacts(text, WorkBudget(ceilings))


@pytest.mark.parametrize("disposition,legitimate_text,expected_status,reason", [
    ("annotate", b"dialect prose", "failed", "artifact_missed"),
    ("review", b"<p>dialect</p>", "passed", "legitimate_review_only"),
    ("refuse", b"<p>dialect</p>", "failed", "legitimate_hard_refusal"),
    ("review", b"\x07", "failed", "legitimate_hard_refusal"),
])
def test_calibration_both_directions(tmp_path, disposition, legitimate_text,
                                     expected_status, reason):
    manifest = _manifest(tmp_path / "packet", [b"<p>artifact</p>", legitimate_text,
                                                b"archaic prose", b"purposeful fragments",
                                                b"unusual syntax"])
    labels = _labels(tmp_path, [_label(0, "artifact"),
                                _label(1, "legitimate_variation", "dialect"),
                                _label(2, "legitimate_variation", "archaic_spelling"),
                                _label(3, "legitimate_variation", "purposeful_roughness"),
                                _label(4, "legitimate_variation", "unusual_valid_syntax")])
    policy = _policy(tmp_path, disposition=disposition)
    out = tmp_path / "out"
    run_calibrate(manifest, plain_hash(manifest.read_bytes()), labels,
                  plain_hash(labels.read_bytes()), policy, out)
    receipt = json.loads((out / "receipt.json").read_bytes())
    assert receipt["calibration_status"] == expected_status
    assert receipt["reason_counts"][reason] == 1


def test_calibration_empty_direction_and_missing_controls(tmp_path):
    manifest = _manifest(tmp_path / "packet", [b"ordinary prose"])
    labels = _labels(tmp_path, [_label(0, "legitimate_variation", "other")])
    out = tmp_path / "out"
    run_calibrate(manifest, plain_hash(manifest.read_bytes()), labels,
                  plain_hash(labels.read_bytes()), _policy(tmp_path), out)
    reasons = json.loads((out / "receipt.json").read_bytes())["reason_counts"]
    assert reasons["direction_empty"] == 1
    assert reasons["control_class_empty"] == 4


@pytest.mark.parametrize("mutation", [
    lambda rows: rows.append(dict(rows[0])),
    lambda rows: rows.pop(),
    lambda rows: rows[0].update(id="extra"),
    lambda rows: rows[0].update(variation_class="unknown"),
    lambda rows: rows[0].update(register_cell="not_allowed"),
    lambda rows: rows[0].update(label="artifact", variation_class="dialect"),
])
def test_label_contract_rejects_bad_coverage_and_shapes(tmp_path, mutation):
    manifest = _manifest(tmp_path / "packet", [b"plain prose"])
    labels = _labels(tmp_path, [_label(0, "legitimate_variation", "dialect")])
    data = json.loads(labels.read_bytes())
    mutation(data["labels"])
    labels.write_bytes(canonical_json(data))
    with pytest.raises(Refusal, match="labels_contract"):
        run_calibrate(manifest, plain_hash(manifest.read_bytes()), labels,
                      plain_hash(labels.read_bytes()), _policy(tmp_path), tmp_path / "out")


@pytest.mark.parametrize("reviewed,total,expected", [(1, 5, False), (5, 10, True)])
def test_stratum_outcome_small_cell_suppression(tmp_path, reviewed, total, expected):
    manifest = _manifest(tmp_path / "packet",
                         [b"<p>artifact</p>"] * reviewed +
                         [b"ordinary prose"] * (total - reviewed))
    out = tmp_path / "out"
    run_census(manifest, _policy(tmp_path), out)
    receipt = json.loads((out / "receipt.json").read_bytes())
    assert receipt["stratum_counts"] == {"cell": total}
    assert (receipt["stratum_outcome_counts"] is not None) is expected


@pytest.mark.parametrize("artifact,mutation", [
    ("detail", "wrong_hash"), ("detail", "added"), ("detail", "missing"),
    ("detail", "noncanonical"), ("receipt", "wrong_hash"),
    ("receipt", "added"), ("receipt", "missing"), ("receipt", "noncanonical"),
])
def test_census_reloader_strict_mutations(tmp_path, artifact, mutation):
    manifest = _manifest(tmp_path / "packet", [b"<p>x</p>"])
    out = tmp_path / "out"
    run_census(manifest, _policy(tmp_path), out)
    raw = (out / f"{artifact}.json").read_bytes()
    if mutation == "wrong_hash":
        candidate = raw
        expected_hash = "0" * 64
    elif mutation == "noncanonical":
        candidate = raw[:-1] + b" \n"
        expected_hash = plain_hash(candidate)
    else:
        value = json.loads(raw)
        if mutation == "added":
            value["extra"] = 1
        else:
            del value["tool"]
        candidate = canonical_json(value)
        expected_hash = plain_hash(candidate)
    path = tmp_path / "mutation.json"
    path.write_bytes(candidate)
    with pytest.raises(Refusal, match="detail_contract" if artifact == "detail"
                       else "receipt_contract"):
        if artifact == "detail":
            load_artifact_detail(path, expected_hash)
        else:
            load_artifact_receipt(path, expected_hash)


def test_two_row_calibration_does_not_claim_control_coverage(tmp_path):
    manifest = _manifest(tmp_path / "packet", [b"<p>artifact</p>", b"valid other prose"])
    labels = _labels(tmp_path, [_label(0, "artifact"),
                                _label(1, "legitimate_variation", "other")])
    out = tmp_path / "out"
    run_calibrate(manifest, plain_hash(manifest.read_bytes()), labels,
                  plain_hash(labels.read_bytes()), _policy(tmp_path), out)
    receipt = json.loads((out / "receipt.json").read_bytes())
    assert receipt["calibration_status"] == "failed"
    assert receipt["reason_counts"]["control_class_empty"] == 4
    assert receipt["reason_counts"]["direction_empty"] == 0


def test_verse_refuse_policy_rejected(tmp_path):
    policy = _policy(tmp_path)
    value = json.loads(policy.read_bytes())
    value["dispositions"]["verse"] = "refuse"
    policy.write_bytes(canonical_json(value))
    manifest = _manifest(tmp_path / "packet", [b"ordinary prose"])
    with pytest.raises(Refusal, match="policy_contract"):
        run_census(manifest, policy, tmp_path / "out")


def test_detector_identity_stable_across_fresh_processes(tmp_path):
    script = ("from setec.preflight.artifacts_core import DETECTOR_SHA256; "
              "print(DETECTOR_SHA256)")
    scripts = str(Path(__file__).parents[1])
    import os
    env = os.environ.copy()
    env["PYTHONPATH"] = scripts
    outputs = [subprocess.run([sys.executable, "-c", script], env=env,
                              capture_output=True, check=True).stdout for _ in range(2)]
    assert [item.strip() for item in outputs] == [DETECTOR_SHA256.encode()] * 2


def test_renaming_id_changes_record_set_identity(tmp_path):
    manifest = _manifest(tmp_path / "packet", [b"ordinary prose"])
    first = record_set_sha256(load_manifest(manifest).records)
    row = json.loads(manifest.read_bytes())
    row["id"] = "renamed"
    manifest.write_bytes(canonical_json(row))
    assert record_set_sha256(load_manifest(manifest).records) != first


def test_census_collision_idempotence_and_stream_canary(tmp_path, capsys, monkeypatch):
    import setec.preflight.artifacts as command
    canary = "sealedcanary731"
    root = tmp_path / "packet"
    manifest = _manifest(root, [f"<p>{canary}</p>".encode()], stratum=canary)
    row = json.loads(manifest.read_bytes())
    row["id"] = canary
    row["group_id"] = canary
    name = canary + ".txt"
    (root / "row0.txt").rename(root / name)
    row["path"] = name
    row["span"]["source_path"] = name
    manifest.write_bytes(canonical_json(row))
    policy = _policy(tmp_path)
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    assert command.main(["census", "--manifest", str(manifest), "--policy", str(policy),
                         "--out-bundle", str(out1)]) == 0
    streams = capsys.readouterr()
    # Slice 5 inherits slice 1 section 4.7: stdout is the committed receipt.
    assert streams.out.encode() == (out1 / "receipt.json").read_bytes()
    assert streams.err.splitlines() == ["intake passed", "artifact_census needs_human_review"]
    assert canary not in streams.out and canary not in streams.err
    assert canary not in (out1 / "receipt.json").read_text()
    assert command.main(["census", "--manifest", str(manifest), "--policy", str(policy),
                         "--out-bundle", str(out2)]) == 0
    assert (out1 / "receipt.json").read_bytes() == (out2 / "receipt.json").read_bytes()
    assert capsys.readouterr().out.encode() == (out2 / "receipt.json").read_bytes()
    assert command.main(["census", "--manifest", str(manifest), "--policy", str(policy),
                         "--out-bundle", str(out1)]) == 2
    streams = capsys.readouterr()
    assert streams.out == "" and streams.err.endswith("output_collision\n")
    monkeypatch.setattr(command, "run_census", lambda *_: 1 / 0)
    assert command.main(["census", "--manifest", str(manifest), "--policy", str(policy),
                         "--out-bundle", str(tmp_path / "unused")]) == 2
    streams = capsys.readouterr()
    assert streams.out == "" and streams.err == "internal_refusal\n"


@pytest.mark.parametrize("name", ARTIFACT_TYPES)
def test_each_detector_four_mib_single_line_bound(name):
    size = 4 * 1024 * 1024
    seeds = {
        "markup_html": ("<a", ""), "markup_css": ("a", "{"),
        "markup_script": ("<script", ""), "navigation_boilerplate": ("home", ""),
        "markdown_apparatus": ("#", ""), "tei_xml_apparatus": ("<TEI", ""),
        "footnote_definition": ("[^", ""), "page_header": ("page ", ""),
        "line_number": ("1", ""), "ocr_hyphenation": ("a", "-"),
        "running_head": ("head", ""), "truncation_marker": ("[", ""),
        "unbalanced_fence": ("```", ""), "verse_likely": ("word ", ""),
        "replacement_character": ("a", "\ufffd"),
        "private_use_character": ("a", "\ue000"),
    }
    prefix, suffix = seeds[name]
    fill = " " if name in {"markup_html", "markup_css", "markup_script",
                             "tei_xml_apparatus", "page_header"} else "a"
    text = prefix + fill * (size - len(prefix) - len(suffix)) + suffix
    start = time.monotonic()
    detect_artifacts(text, WorkBudget(CEILINGS))
    assert time.monotonic() - start < 15


@pytest.mark.parametrize("mutation", ["wrong_hash", "added", "missing",
                                        "renamed", "noncanonical"])
def test_calibration_receipt_strict_mutations(tmp_path, mutation):
    manifest = _manifest(tmp_path / "packet", [b"<p>artifact</p>", b"dialect prose",
                                                b"archaic prose", b"rough prose",
                                                b"unusual syntax"])
    labels = _labels(tmp_path, [_label(0, "artifact"),
                                _label(1, "legitimate_variation", "dialect"),
                                _label(2, "legitimate_variation", "archaic_spelling"),
                                _label(3, "legitimate_variation", "purposeful_roughness"),
                                _label(4, "legitimate_variation", "unusual_valid_syntax")])
    out = tmp_path / "out"
    run_calibrate(manifest, plain_hash(manifest.read_bytes()), labels,
                  plain_hash(labels.read_bytes()), _policy(tmp_path), out)
    raw = (out / "receipt.json").read_bytes()
    if mutation == "wrong_hash":
        candidate, expected_hash = raw, "0" * 64
    elif mutation == "noncanonical":
        candidate = raw[:-1] + b" \n"
        expected_hash = plain_hash(candidate)
    else:
        value = json.loads(raw)
        if mutation == "added":
            value["extra"] = 1
        elif mutation == "missing":
            del value["row_count"]
        else:
            value["rows_total"] = value.pop("row_count")
        candidate = canonical_json(value)
        expected_hash = plain_hash(candidate)
    path = tmp_path / "mutated-receipt.json"
    path.write_bytes(candidate)
    with pytest.raises(Refusal, match="receipt_contract"):
        load_calibration_receipt(path, expected_hash)


@pytest.mark.parametrize("name,inside,outside", [
    ("markup_html", "<a>", "<a"),
    ("markup_css", ".note {\n" + "color: red;\n" * 48 + "}",
     ".note {\n" + "color: red;\n" * 49 + "}"),
    ("markup_script", "<SCRIPT>", "<ſcript>"),
    ("navigation_boilerplate", "Home", "Homecoming"),
    ("markdown_apparatus", "# Heading", "#Heading"),
    ("tei_xml_apparatus", "<TEI>", "<TEIsomething>"),
    ("footnote_definition", "[^1]: note", "inline [^1] note"),
    ("page_header", "Page 123456", "Page 1234567"),
    ("line_number", "Before\n123456\nAfter", "Before\n1234567\nAfter"),
    ("ocr_hyphenation", "well-\nknown", "well-\nKnown"),
    ("running_head", "abc\n\nx\n\nabc\n\ny\n\nabc",
     "ab\n\nx\n\nab\n\ny\n\nab"),
    ("truncation_marker", "[truncated]", "[truncated?]"),
    ("unbalanced_fence", "```python\ncode", "```\ncode\n```"),
    ("verse_likely", "one two three four\n" * 39 + "one two three four",
     "one two three four\n" * 40 + "one two three four"),
    ("replacement_character", "a\ufffdb", "ordinary prose"),
    ("private_use_character", "a\uf8ffb", "a\uf900b"),
])
def test_each_detector_at_boundary_and_one_outside(name, inside, outside):
    def names(text):
        return {item.artifact_type for item in detect_artifacts(text, WorkBudget(CEILINGS))}
    assert name in names(inside)
    assert name not in names(outside)


def test_calibrate_streams_committed_receipt(tmp_path, capsys):
    import setec.preflight.artifacts as command
    manifest = _manifest(tmp_path / "packet", [b"<p>artifact</p>", b"dialect prose"])
    labels = _labels(tmp_path, [_label(0, "artifact"),
                                _label(1, "legitimate_variation", "dialect")])
    out = tmp_path / "out"
    assert command.main(["calibrate", "--manifest", str(manifest), "--policy",
                         str(_policy(tmp_path)), "--out-bundle", str(out),
                         "--expect-manifest-sha256", plain_hash(manifest.read_bytes()),
                         "--labels", str(labels),
                         "--expect-labels-sha256", plain_hash(labels.read_bytes())]) == 0
    streams = capsys.readouterr()
    assert streams.out.encode() == (out / "receipt.json").read_bytes()
    assert streams.err == "calibration failed\n"


def test_refusal_order_follows_master_order(tmp_path, capsys):
    # Slice 1 section 4.6, first match wins. A missing manifest directory is
    # path_confinement (exit 2), not output_unavailable, even when the output
    # parent is missing too.
    import setec.preflight.artifacts as command
    policy = _policy(tmp_path)
    missing = tmp_path / "absent" / "packet.jsonl"
    assert command.main(["census", "--manifest", str(missing), "--policy", str(policy),
                         "--out-bundle", str(tmp_path / "absent-out" / "out")]) == 2
    assert capsys.readouterr().err == "path_confinement\n"
    # Input and policy contracts outrank an existing output directory.
    manifest = _manifest(tmp_path / "packet", [b"plain prose"])
    taken = tmp_path / "taken"
    taken.mkdir()
    bad_policy = tmp_path / "bad"
    bad_policy.mkdir()
    (bad_policy / "policy.json").write_bytes(b"{}")
    with pytest.raises(Refusal, match="policy_contract"):
        run_census(manifest, bad_policy / "policy.json", taken)
    # labels_contract outranks calibration_binding.
    labels = _labels(tmp_path, [_label(0, "artifact"), _label(9, "artifact")])
    with pytest.raises(Refusal, match="labels_contract"):
        run_calibrate(manifest, "0" * 64, labels, "0" * 64, policy, tmp_path / "cal")
