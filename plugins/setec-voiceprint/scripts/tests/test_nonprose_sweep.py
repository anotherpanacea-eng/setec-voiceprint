from __future__ import annotations

import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[4]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import nonprose_sweep as N  # noqa: E402


def _write_manifest(path: Path, rows: list[dict[str, object]], newline: bytes = b"\n") -> bytes:
    raw = newline.join(
        json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
        for row in rows
    ) + newline
    path.write_bytes(raw)
    return raw


def _run_main(manifest: Path, report: Path) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    rc = N.main(
        ["--manifest", str(manifest), "--report-out", str(report)],
        stdout=stdout,
        stderr=stderr,
    )
    return rc, stdout.getvalue(), stderr.getvalue()


def _words(count: int, *, token: str = "word") -> str:
    return " ".join([token] * count)


def test_word_pattern_and_physical_lines_are_frozen() -> None:
    assert N._WORD_RE.pattern == r"[^\W_]+(?:['’\-‐‑][^\W_]+)*"
    assert [match.group(0) for match in N._WORD_RE.finditer("one two_three O’NEILL mm-hmm 42")] == [
        "one",
        "two",
        "three",
        "O’NEILL",
        "mm-hmm",
        "42",
    ]
    assert N._physical_lines("a\nb") == ["a", "b"]
    assert N._physical_lines("a\r\nb") == ["a", "b"]
    assert N._physical_lines("a\rb") == ["a", "b"]
    assert N._physical_lines("a\n\n") == ["a", ""]
    assert N._physical_lines("a\u2028b") == ["a\u2028b"]


@pytest.mark.parametrize(
    "line",
    [
        "00:00.000 --> 00:01.000",
        "00:00:00.000 --> 00:00:01.000 align:start",
        "100:00:00.000\t-->\t100:00:01.000 line:10% position:20%",
    ],
)
def test_vtt_timing_positive(line: str) -> None:
    assert N._is_vtt_timing(line)


@pytest.mark.parametrize(
    "line",
    [
        "00:00,000 --> 00:01,000",
        "00:61.000 --> 00:01.000",
        "00:00.000-->00:01.000",
        "prose 00:00.000 --> 00:01.000",
        "00:00.000 --> 00:01.000 unknown:value",
        "00:00.000 --> 00:01.000 align:café",
        "-->",
    ],
)
def test_vtt_timing_negative(line: str) -> None:
    assert not N._is_vtt_timing(line)


def test_vtt_header_cue_identifier_tags_and_payload_partition() -> None:
    result = N.analyze_document(
        "\nWEBVTT\n\ncue-1\n00:00.000 --> 00:01.000 align:start\n"
        "<v PERSON><i>um hello</i>\n\nauthored ending"
    )
    assert result["vtt_structural_hits"] == 2
    assert result["total_analyzable_words"] == 4
    assert result["transcript_words"] == 2
    assert result["authored_residual_words"] == 2
    assert result["disfluency_count"] == 1
    assert result["screen_hits"]["vtt_any"] is True


def test_late_header_and_arrow_prose_do_not_hit() -> None:
    result = N.analyze_document("authored first\nWEBVTT\nprose --> prose")
    assert result["vtt_structural_hits"] == 0
    assert result["screen_hits"]["vtt_any"] is False


@pytest.mark.parametrize("text", ["webvtt", "WEBVTT suffix", "prefix WEBVTT"])
def test_vtt_header_is_exact_and_case_sensitive(text: str) -> None:
    assert N.analyze_document(text)["vtt_structural_hits"] == 0


@pytest.mark.parametrize(
    "line",
    [
        "HOST: hello",
        "speaker: hello",
        "SPEAKER 999: hello",
        "participant 1:\thello",
        "Q: hello",
        "JANE DOE: hello",
        "JEAN-LUC PICARD: hello",
        "D’ARCY JONES: hello",
    ],
)
def test_speaker_labels_positive(line: str) -> None:
    assert N._speaker_payload(line) is not None


@pytest.mark.parametrize(
    "line",
    [
        "NASA: launch",
        "Note: ordinary",
        "JANE  DOE: doubled",
        "JANE\tDOE: tabbed",
        "JANE- DOE: broken",
        "中文 字符: uncased",
        "SPEAKER 0000: too long",
        "HOST:—punctuation",
        "A- B: broken",
        "A--B JONES: broken",
        f"{'É' * 24} {'É' * 24}: too many bytes",
    ],
)
def test_speaker_labels_negative(line: str) -> None:
    assert N._speaker_payload(line) is None


def test_speaker_block_stops_at_blank_and_excludes_label_words() -> None:
    result = N.analyze_document("HOST:\ncontinued words\nmore words\n\nauthored tail")
    assert result["speaker_label_lines"] == 1
    assert result["transcript_words"] == 4
    assert result["authored_residual_words"] == 2
    assert result["total_analyzable_words"] == 6


def test_disfluencies_are_whole_token_and_partition_independent() -> None:
    result = N.analyze_document("um UHH quantum human summary like you know I mean")
    assert result["disfluency_count"] == 2
    assert result["transcript_words"] == 0
    assert result["authored_residual_words"] == result["total_analyzable_words"]


@pytest.mark.parametrize(
    "lexeme",
    ["um", "umm", "uh", "uhh", "erm", "er", "hmm", "mm-hmm", "uh-huh"],
)
def test_closed_disfluency_lexicon_matches_each_whole_token(lexeme: str) -> None:
    assert N.analyze_document(lexeme.upper())["disfluency_count"] == 1


@pytest.mark.parametrize("text", ["mm‐hmm", "mm‑hmm", "uh‐huh", "uh‑huh"])
def test_non_ascii_hyphen_variants_are_not_disfluencies(text: str) -> None:
    assert N.analyze_document(text)["disfluency_count"] == 0


def test_speaker_threshold_exact_and_first_over() -> None:
    clear_lines = ["HOST: words"] * 3 + ["ordinary words"] * 17
    hit_lines = ["HOST: words"] * 4 + ["ordinary words"] * 16
    assert N.analyze_document("\n".join(clear_lines))["screen_hits"]["speaker_labels"] is False
    assert N.analyze_document("\n".join(hit_lines))["screen_hits"]["speaker_labels"] is True


def test_disfluency_threshold_exact_and_first_over() -> None:
    clear = " ".join(["um"] * 6 + ["word"] * 994)
    hit = " ".join(["um"] * 7 + ["word"] * 993)
    assert N.analyze_document(clear)["screen_hits"]["disfluencies"] is False
    assert N.analyze_document(hit)["screen_hits"]["disfluencies"] is True


def test_short_line_threshold_and_line_eligibility() -> None:
    assert N.analyze_document("\n".join(["short"] * 15))["screen_hits"]["short_lines"] is False
    sixteen = ["short"] * 9 + [_words(6)] * 7
    assert N.analyze_document("\n".join(sixteen))["screen_hits"]["short_lines"] is True
    clear = ["short"] * 11 + [_words(6)] * 9
    hit = ["short"] * 12 + [_words(6)] * 8
    assert N.analyze_document("\n".join(clear))["screen_hits"]["short_lines"] is False
    assert N.analyze_document("\n".join(hit))["screen_hits"]["short_lines"] is True


def test_minimal_strict_threshold_cross_products() -> None:
    speaker = ["HOST: word"] * 2 + ["ordinary words"] * 11
    assert N.analyze_document("\n".join(speaker))["screen_hits"]["speaker_labels"] is True

    disfluency = " ".join(["um"] * 2 + ["word"] * 331)
    assert N.analyze_document(disfluency)["screen_hits"]["disfluencies"] is True

    short = ["short"] * 16 + [_words(6)] * 13
    assert N.analyze_document("\n".join(short))["screen_hits"]["short_lines"] is True


def test_named_partition_fixture_fractions() -> None:
    authored = N.analyze_document("authored words only")
    assert authored["authored_residual_fraction"] == {"numerator": 3, "denominator": 3}
    assert authored["transcript_fraction"] == {"numerator": 0, "denominator": 3}

    vtt = N.analyze_document("WEBVTT\n\n00:00.000 --> 00:01.000\npayload words")
    assert vtt["authored_residual_fraction"] == {"numerator": 0, "denominator": 2}
    assert vtt["transcript_fraction"] == {"numerator": 2, "denominator": 2}

    speaker = N.analyze_document("A: spoken words\ncontinued line")
    assert speaker["authored_residual_fraction"] == {"numerator": 0, "denominator": 4}
    assert speaker["transcript_fraction"] == {"numerator": 4, "denominator": 4}


def test_zero_word_fraction_and_conservation() -> None:
    result = N.analyze_document("WEBVTT\n\n00:00.000 --> 00:01.000")
    assert result["total_analyzable_words"] == 0
    assert result["authored_residual_fraction"] == {"numerator": 0, "denominator": 0}
    assert result["transcript_fraction"] == {"numerator": 0, "denominator": 0}


def test_manifest_parser_closed_projection_and_order() -> None:
    raw = b'{"path":"a.txt","extra":true,"id":"b"}\r{"id":"a","path":"b.txt"}'
    assert N.parse_manifest(raw) == [
        {"id": "b", "parts": ("a.txt",)},
        {"id": "a", "parts": ("b.txt",)},
    ]


def test_manifest_id_is_opaque_and_nonempty_not_visually_nonblank() -> None:
    assert N.parse_manifest(b'{"id":"\\u00a0","path":"a.txt"}\n') == [
        {"id": "\u00a0", "parts": ("a.txt",)}
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"id":"\\ud800","path":"a.txt"}\n',
        b'{"id":"a","path":"\\ud800.txt"}\n',
    ],
)
def test_manifest_surrogates_are_controlled_refusals(raw: bytes) -> None:
    with pytest.raises(N.ControlledFailure):
        N.parse_manifest(raw)


def test_manifest_excessive_json_nesting_is_controlled() -> None:
    nested = b"[" * 10_000 + b"0" + b"]" * 10_000
    raw = b'{"id":"a","path":"a.txt","unused":' + nested + b"}\n"
    with pytest.raises(N.ControlledFailure):
        N.parse_manifest(raw)


@pytest.mark.parametrize("number", [b"1e999999", b"-1e999999"])
def test_manifest_rejects_finite_syntax_that_overflows_to_infinity(number: bytes) -> None:
    raw = b'{"id":"a","path":"a.txt","unused":' + number + b"}\n"
    with pytest.raises(N.ControlledFailure):
        N.parse_manifest(raw)


def test_manifest_line_byte_ceiling_exact_and_one_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b'{"id":"a","path":"a.txt"}\n'
    physical_length = len(raw) - 1
    monkeypatch.setattr(N, "MAX_LINE_BYTES", physical_length)
    assert N.parse_manifest(raw)[0]["id"] == "a"
    monkeypatch.setattr(N, "MAX_LINE_BYTES", physical_length - 1)
    with pytest.raises(N.ControlledFailure):
        N.parse_manifest(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"\xef\xbb\xbf{}",
        b'{"id":"a","id":"b","path":"x"}\n',
        b'{"id":"a","path":"../x"}\n',
        b'{"id":"a","path":"x\\\\y"}\n',
        b'{"id":"a","path":"/x"}\n',
        b'{"id":"a","path":"x:y"}\n',
        b'{"id":"a","path":"x","n":NaN}\n',
        b'{"id":"a","path":"x"}\n{"id":"a","path":"y"}\n',
    ],
)
def test_manifest_parser_refuses_malformed_inputs(raw: bytes) -> None:
    with pytest.raises(N.ControlledFailure):
        N.parse_manifest(raw)


def test_cli_report_envelope_seals_and_privacy(tmp_path: Path) -> None:
    document = tmp_path / "private-sentinel.txt"
    prose = (
        "WEBVTT\n\n00:00.000 --> 00:01.000\n"
        "SENTINEL PROSE HOST: um SENTINEL_PAYLOAD"
    )
    document.write_text(prose, encoding="utf-8", newline="")
    manifest = tmp_path / "manifest.jsonl"
    manifest_raw = _write_manifest(
        manifest,
        [{"id": "opaque-1", "path": document.name, "unused": "value"}],
    )
    report = tmp_path / "report.json"
    rc, stdout, stderr = _run_main(manifest, report)
    assert rc == 0
    assert stderr == ""
    assert stdout.endswith("\n") and "\r" not in stdout
    envelope = json.loads(stdout)
    assert set(envelope) == {
        "ai_status",
        "available",
        "baseline",
        "claim_license",
        "claim_license_rendered",
        "results",
        "schema_version",
        "target",
        "task_surface",
        "tool",
        "version",
        "warnings",
    }
    assert envelope["schema_version"] == "1.0"
    assert envelope["task_surface"] == "validation"
    assert envelope["tool"] == "nonprose_sweep"
    assert envelope["version"] == "1.0"
    assert envelope["available"] is True
    assert envelope["baseline"] is None
    assert set(envelope["target"]) == {"path", "words"}
    assert envelope["target"]["path"] is None
    assert envelope["ai_status"] is None
    assert set(envelope["results"]) == {
        "calibration_status",
        "manifest_sha256",
        "method",
        "report_sha256",
        "source_set_sha256",
        "thresholds",
        "totals",
    }
    assert envelope["results"]["thresholds"] == N._THRESHOLDS
    assert envelope["results"]["manifest_sha256"] == N._sha256_tag(manifest_raw)
    assert "opaque-1" not in stdout
    assert str(document) not in stdout
    assert prose not in stdout
    report_raw = report.read_bytes()
    assert report_raw.endswith(b"\n") and b"\r" not in report_raw
    parsed = json.loads(report_raw)
    assert set(parsed) == {
        "calibration_status",
        "documents",
        "manifest_sha256",
        "method",
        "schema",
        "source_set_sha256",
        "thresholds",
        "totals",
    }
    assert set(parsed["totals"]) == {
        "authored_residual_words",
        "disfluency_count",
        "documents",
        "documents_with_any_screen",
        "nonempty_lines",
        "screen_counts",
        "short_lines",
        "speaker_label_lines",
        "total_analyzable_words",
        "transcript_words",
        "vtt_structural_hits",
    }
    assert set(parsed["totals"]["screen_counts"]) == {
        "disfluencies",
        "short_lines",
        "speaker_labels",
        "vtt_any",
    }
    assert set(parsed["documents"][0]) == {
        "authored_residual_fraction",
        "authored_residual_words",
        "disfluency_count",
        "id",
        "nonempty_lines",
        "screen_hits",
        "short_lines",
        "speaker_label_lines",
        "total_analyzable_words",
        "transcript_fraction",
        "transcript_words",
        "vtt_structural_hits",
    }
    assert set(parsed["documents"][0]["screen_hits"]) == {
        "disfluencies",
        "short_lines",
        "speaker_labels",
        "vtt_any",
    }
    assert parsed["schema"] == N.REPORT_SCHEMA
    assert parsed["calibration_status"] == N.CALIBRATION_STATUS
    assert parsed["documents"][0]["id"] == "opaque-1"
    assert prose.encode() not in report_raw
    assert str(document).encode() not in report_raw
    assert envelope["results"]["report_sha256"] == N._sha256_tag(report_raw)
    component = hashlib.sha256(document.read_bytes()).hexdigest()
    preimage = N._canonical_bytes({"content_sha256": component, "id": "opaque-1"})
    assert parsed["source_set_sha256"] == N._sha256_tag(preimage)
    assert component not in report_raw.decode()
    expected_license = N.ClaimLicense(
        task_surface="validation",
        licenses=(
            "A bounded structural corpus-hygiene screen reporting VTT structure, "
            "speaker-label density, disfluency density, short-line density, and a "
            "deterministic authored-residual/transcript word partition."
        ),
        does_not_license=(
            "Corpus disposition, authorship, provenance, quality, genre, fiction or "
            "nonfiction classification, AI/human inference, or training eligibility."
        ),
        comparison_set={
            "documents": parsed["totals"]["documents"],
            "documents_with_any_screen": parsed["totals"]["documents_with_any_screen"],
            "method": N.METHOD_VERSION,
        },
        additional_caveats=[
            "Thresholds are operationally uncalibrated and queue documents only for review.",
            "authored_residual_words is a structural residual, not an authorship inference.",
        ],
        references=["Spec 72"],
    )
    assert envelope["claim_license"] == expected_license.to_dict()
    assert envelope["claim_license_rendered"] == expected_license.render_block().rstrip()
    assert N._has_forbidden_key(parsed) is False
    assert N._has_forbidden_key(envelope["results"]) is False


def test_cli_is_deterministic_and_sorts_ids_by_utf8(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("authored words", encoding="utf-8")
    (tmp_path / "b.txt").write_text("HOST: transcript", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(
        manifest,
        [{"id": "é", "path": "a.txt"}, {"id": "z", "path": "b.txt"}],
    )
    report1 = tmp_path / "one.json"
    report2 = tmp_path / "two.json"
    first = _run_main(manifest, report1)
    second = _run_main(manifest, report2)
    assert first == second
    assert report1.read_bytes() == report2.read_bytes()
    assert [row["id"] for row in json.loads(report1.read_bytes())["documents"]] == ["z", "é"]


def test_manifest_framing_changes_raw_seal_not_metrics(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("authored words", encoding="utf-8")
    one = tmp_path / "one.jsonl"
    two = tmp_path / "two.jsonl"
    _write_manifest(one, [{"id": "a", "path": "a.txt"}], b"\n")
    _write_manifest(two, [{"path": "a.txt", "id": "a"}], b"\r\n")
    out1 = tmp_path / "one-report.json"
    out2 = tmp_path / "two-report.json"
    assert _run_main(one, out1)[0] == 0
    assert _run_main(two, out2)[0] == 0
    left = json.loads(out1.read_bytes())
    right = json.loads(out2.read_bytes())
    assert left["documents"] == right["documents"]
    assert left["totals"] == right["totals"]
    assert left["source_set_sha256"] == right["source_set_sha256"]
    assert left["manifest_sha256"] != right["manifest_sha256"]


def test_all_manifest_framings_and_row_orders_preserve_projection(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")
    rows = [
        b'{"id":"a","path":"a.txt"}',
        b'{"path":"b.txt","id":"b"}',
    ]
    variants = [
        b"\n".join(rows) + b"\n",
        b"\r\n".join(reversed(rows)) + b"\r\n",
        b"\r".join(rows) + b"\r",
        b"\n".join(reversed(rows)),
    ]
    parsed: list[dict[str, object]] = []
    stdout_values: list[str] = []
    for index, raw in enumerate(variants):
        manifest = tmp_path / f"manifest-{index}.jsonl"
        manifest.write_bytes(raw)
        report = tmp_path / f"report-{index}.json"
        rc, stdout, _stderr = _run_main(manifest, report)
        assert rc == 0
        parsed.append(json.loads(report.read_bytes()))
        stdout_values.append(stdout)
    assert all(item["documents"] == parsed[0]["documents"] for item in parsed)
    assert all(item["totals"] == parsed[0]["totals"] for item in parsed)
    assert all(item["source_set_sha256"] == parsed[0]["source_set_sha256"] for item in parsed)
    assert len({item["manifest_sha256"] for item in parsed}) == len(variants)
    assert len(
        {item["results"]["report_sha256"] for item in map(json.loads, stdout_values)}
    ) == len(variants)


def test_document_newline_change_preserves_metrics_changes_source_seal(tmp_path: Path) -> None:
    document = tmp_path / "a.txt"
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    document.write_bytes(b"one\ntwo\n")
    out1 = tmp_path / "one.json"
    assert _run_main(manifest, out1)[0] == 0
    document.write_bytes(b"one\r\ntwo\r\n")
    out2 = tmp_path / "two.json"
    assert _run_main(manifest, out2)[0] == 0
    left = json.loads(out1.read_bytes())
    right = json.loads(out2.read_bytes())
    assert left["documents"] == right["documents"]
    assert left["source_set_sha256"] != right["source_set_sha256"]


def test_all_document_newline_framings_preserve_metrics_but_change_seals(
    tmp_path: Path,
) -> None:
    document = tmp_path / "a.txt"
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    variants = [b"one\ntwo\n", b"one\r\ntwo\r\n", b"one\rtwo\r", b"one\ntwo"]
    reports: list[dict[str, object]] = []
    envelopes: list[dict[str, object]] = []
    for index, raw in enumerate(variants):
        document.write_bytes(raw)
        report = tmp_path / f"report-{index}.json"
        rc, stdout, _stderr = _run_main(manifest, report)
        assert rc == 0
        reports.append(json.loads(report.read_bytes()))
        envelopes.append(json.loads(stdout))
    assert all(item["documents"] == reports[0]["documents"] for item in reports)
    assert len({item["source_set_sha256"] for item in reports}) == len(variants)
    assert len({item["results"]["report_sha256"] for item in envelopes}) == len(variants)


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_unicode_line_separators_remain_content(separator: str) -> None:
    result = N.analyze_document(f"one{separator}two")
    assert result["nonempty_lines"] == 1
    assert result["total_analyzable_words"] == 2


def test_preexisting_report_is_preserved(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("words", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    report = tmp_path / "report.json"
    report.write_bytes(b"winner")
    rc, stdout, stderr = _run_main(manifest, report)
    assert rc == 3
    assert stdout == ""
    assert stderr == "nonprose_sweep: input, resource, or publication validation failed\n"
    assert report.read_bytes() == b"winner"


def test_report_may_not_name_a_selected_source(tmp_path: Path) -> None:
    document = tmp_path / "a.txt"
    original = b"source bytes"
    document.write_bytes(original)
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    rc, stdout, _stderr = _run_main(manifest, document)
    assert rc == 3
    assert stdout == ""
    assert document.read_bytes() == original


def test_missing_manifest_or_report_parent_is_controlled(tmp_path: Path) -> None:
    missing_manifest = tmp_path / "missing" / "manifest.jsonl"
    rc, stdout, stderr = _run_main(missing_manifest, tmp_path / "report.json")
    assert rc == 3
    assert stdout == ""
    assert stderr == "nonprose_sweep: input, resource, or publication validation failed\n"

    document = tmp_path / "a.txt"
    document.write_text("words", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    rc, stdout, stderr = _run_main(manifest, tmp_path / "missing-output" / "report.json")
    assert rc == 3
    assert stdout == ""
    assert stderr == "nonprose_sweep: input, resource, or publication validation failed\n"


def test_duplicate_source_identity_and_manifest_alias_refuse(tmp_path: Path) -> None:
    document = tmp_path / "a.txt"
    document.write_text("words", encoding="utf-8")
    duplicate = tmp_path / "duplicate.txt"
    os.link(document, duplicate)
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(
        manifest,
        [{"id": "a", "path": "a.txt"}, {"id": "b", "path": "duplicate.txt"}],
    )
    assert _run_main(manifest, tmp_path / "report.json")[0] == 3
    alias_manifest = tmp_path / "alias.jsonl"
    _write_manifest(alias_manifest, [{"id": "m", "path": "alias.jsonl"}])
    assert _run_main(alias_manifest, tmp_path / "alias-report.json")[0] == 3


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor input contract")
def test_symlink_and_nonregular_sources_refuse(tmp_path: Path) -> None:
    regular = tmp_path / "regular.txt"
    regular.write_text("words", encoding="utf-8")
    symlink = tmp_path / "symlink.txt"
    symlink.symlink_to(regular)
    directory = tmp_path / "directory"
    directory.mkdir()
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    for index, name in enumerate((symlink.name, directory.name, fifo.name)):
        manifest = tmp_path / f"manifest-{index}.jsonl"
        _write_manifest(manifest, [{"id": "a", "path": name}])
        report = tmp_path / f"report-{index}.json"
        assert _run_main(manifest, report)[0] == 3
        assert not report.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor input contract")
@pytest.mark.parametrize("replacement", [False, True])
def test_source_mutation_or_replacement_during_read_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: bool
) -> None:
    document = tmp_path / "a.txt"
    document.write_bytes(b"alpha")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    source_reads = 0

    def mutate(stage: str) -> None:
        nonlocal source_reads
        if stage != "source_read":
            return
        source_reads += 1
        if source_reads == 2:
            if replacement:
                os.replace(document, tmp_path / "old.txt")
                document.write_bytes(b"bravo")
            else:
                document.write_bytes(b"bravo")

    monkeypatch.setattr(N, "_FAULT_HOOK", mutate)
    report = tmp_path / "report.json"
    assert _run_main(manifest, report)[0] == 3
    assert not report.exists()


def test_hardlinked_document_allowed_but_hardlinked_manifest_refused(tmp_path: Path) -> None:
    document = tmp_path / "a.txt"
    document.write_text("words", encoding="utf-8")
    os.link(document, tmp_path / "other-name.txt")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    assert _run_main(manifest, tmp_path / "report.json")[0] == 0
    manifest_link = tmp_path / "manifest-link.jsonl"
    os.link(manifest, manifest_link)
    assert _run_main(manifest_link, tmp_path / "second-report.json")[0] == 3


def test_publish_after_effect_failure_never_deletes_by_racy_final_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"

    def fail(stage: str) -> None:
        if stage == "publish_after_effect":
            raise MemoryError("injected")

    monkeypatch.setattr(N, "_FAULT_HOOK", fail)
    with pytest.raises(N.ControlledFailure):
        N._publish_create_new(destination, b"payload\n")
    if os.name == "posix":
        assert destination.read_bytes() == b"payload\n"
    else:
        assert not destination.exists()
    assert not list(tmp_path.glob(".setec-nonprose-*.tmp"))


@pytest.mark.parametrize(
    "stage",
    [
        "temp_created",
        "write",
        "flush",
        "payload_verified",
        "publish_before",
        "publish_after_effect",
        "final_verified",
    ],
)
def test_publication_memoryerror_stage_preserves_safe_residue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    destination = tmp_path / "report.json"

    def fail(current: str) -> None:
        if current == stage:
            raise MemoryError(stage)

    monkeypatch.setattr(N, "_FAULT_HOOK", fail)
    with pytest.raises(N.ControlledFailure):
        N._publish_create_new(destination, b"payload\n")
    if os.name == "posix" and stage in {"publish_after_effect", "final_verified"}:
        assert destination.read_bytes() == b"payload\n"
    else:
        assert not destination.exists()
    temporaries = list(tmp_path.glob(".setec-nonprose-*.tmp"))
    if os.name == "posix" and stage in {
        "temp_created",
        "write",
        "flush",
        "payload_verified",
        "publish_before",
    }:
        assert len(temporaries) == 1
    else:
        assert not temporaries


def test_temporary_name_memoryerror_occurs_before_parent_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"
    pin_called = False

    def fail_uuid() -> object:
        raise MemoryError("injected")

    def record_pin(_path: Path) -> int:
        nonlocal pin_called
        pin_called = True
        raise AssertionError("must not pin")

    monkeypatch.setattr(N.uuid, "uuid4", fail_uuid)
    monkeypatch.setattr(N, "_posix_pin_directory", record_pin)
    with pytest.raises(N.ControlledFailure):
        N._posix_publish_create_new(destination, b"payload\n")
    assert pin_called is False
    assert not destination.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor publication")
def test_posix_create_new_race_preserves_intervening_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"
    winner = b"winner"
    real_rename = N._posix_rename_exclusive_at

    def install_winner_then_rename(parent: int, source: str, final: str) -> None:
        descriptor = os.open(
            final,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent,
        )
        try:
            os.write(descriptor, winner)
        finally:
            os.close(descriptor)
        real_rename(parent, source, final)

    monkeypatch.setattr(N, "_posix_rename_exclusive_at", install_winner_then_rename)
    with pytest.raises(N.ControlledFailure):
        N._posix_publish_create_new(destination, b"owned\n")
    assert destination.read_bytes() == winner
    temporaries = list(tmp_path.glob(".setec-nonprose-*.tmp"))
    assert len(temporaries) == 1
    assert temporaries[0].read_bytes() == b"owned\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor publication")
def test_posix_final_name_replacement_after_verification_refuses_and_preserves_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"
    winner = b"intervening winner"

    def replace_after_verification(stage: str) -> None:
        if stage == "final_verified":
            destination.unlink()
            destination.write_bytes(winner)

    monkeypatch.setattr(N, "_FAULT_HOOK", replace_after_verification)
    with pytest.raises(N.ControlledFailure):
        N._posix_publish_create_new(destination, b"owned\n")
    assert destination.read_bytes() == winner
    assert not list(tmp_path.glob(".setec-nonprose-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor publication")
def test_posix_prepublication_failure_never_unlinks_a_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"
    unlinked: list[str] = []

    def record_unlink(name: str, **_kwargs: object) -> None:
        unlinked.append(name)
        raise AssertionError("POSIX failure handling must not unlink by path")

    def fail(stage: str) -> None:
        if stage == "payload_verified":
            raise MemoryError("injected")

    monkeypatch.setattr(N.os, "unlink", record_unlink)
    monkeypatch.setattr(N, "_FAULT_HOOK", fail)
    with pytest.raises(N.ControlledFailure):
        N._posix_publish_create_new(destination, b"payload\n")
    assert unlinked == []
    assert not destination.exists()
    temporaries = list(tmp_path.glob(".setec-nonprose-*.tmp"))
    assert len(temporaries) == 1
    assert temporaries[0].read_bytes() == b"payload\n"


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor publication")
def test_posix_temporary_source_swap_refuses_after_exclusive_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"
    replacement = b"same-principal temporary replacement"

    def swap_temporary(stage: str) -> None:
        if stage != "publish_before":
            return
        temporary, = tmp_path.glob(".setec-nonprose-*.tmp")
        temporary.unlink()
        temporary.write_bytes(replacement)

    monkeypatch.setattr(N, "_FAULT_HOOK", swap_temporary)
    with pytest.raises(N.ControlledFailure):
        N._posix_publish_create_new(destination, b"owned\n")
    assert destination.read_bytes() == replacement
    assert not list(tmp_path.glob(".setec-nonprose-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor publication")
def test_posix_success_never_uses_overwriting_or_path_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("path mutator must not be used")

    monkeypatch.setattr(N.os, "rename", forbidden)
    monkeypatch.setattr(N.os, "replace", forbidden)
    monkeypatch.setattr(N.os, "unlink", forbidden)
    N._posix_publish_create_new(destination, b"payload\n")
    assert destination.read_bytes() == b"payload\n"
    assert not list(tmp_path.glob(".setec-nonprose-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor publication")
def test_posix_post_rename_directory_fsync_failure_preserves_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "report.json"
    real_fsync = N.os.fsync
    calls = 0

    def fail_directory_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(N.os, "fsync", fail_directory_fsync)
    with pytest.raises(N.ControlledFailure):
        N._posix_publish_create_new(destination, b"payload\n")
    assert calls == 2
    assert destination.read_bytes() == b"payload\n"
    assert not list(tmp_path.glob(".setec-nonprose-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor publication")
@pytest.mark.parametrize("operation", ["write", "fsync", "verify", "rename"])
def test_posix_publication_syscall_failures_are_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    destination = tmp_path / "report.json"
    if operation == "write":
        monkeypatch.setattr(
            N.os, "write", lambda *_args: (_ for _ in ()).throw(OSError("injected"))
        )
    elif operation == "fsync":
        monkeypatch.setattr(
            N.os, "fsync", lambda *_args: (_ for _ in ()).throw(OSError("injected"))
        )
    elif operation == "verify":
        monkeypatch.setattr(
            N,
            "_read_fd_exact",
            lambda *_args: (_ for _ in ()).throw(N.ControlledFailure("injected")),
        )
    else:
        monkeypatch.setattr(
            N,
            "_posix_rename_exclusive_at",
            lambda *_args: (_ for _ in ()).throw(OSError("injected")),
        )

    with pytest.raises(N.ControlledFailure):
        N._posix_publish_create_new(destination, b"payload\n")
    assert not destination.exists()
    temporaries = list(tmp_path.glob(".setec-nonprose-*.tmp"))
    assert len(temporaries) == 1


def _ceiling_fixture(tmp_path: Path, kind: str) -> tuple[Path, str, int]:
    if kind in {"rows", "cumulative"}:
        (tmp_path / "a.txt").write_bytes(b"one")
        (tmp_path / "b.txt").write_bytes(b"two")
        manifest = tmp_path / "manifest.jsonl"
        _write_manifest(
            manifest,
            [{"id": "a", "path": "a.txt"}, {"id": "b", "path": "b.txt"}],
        )
        return (
            manifest,
            "MAX_DOCUMENTS" if kind == "rows" else "MAX_TOTAL_DOCUMENT_BYTES",
            2 if kind == "rows" else 6,
        )
    contents, attribute, exact = {
        "document_bytes": (b"one", "MAX_DOCUMENT_BYTES", 3),
        "physical_lines": (b"a\nb", "MAX_LINES_PER_DOCUMENT", 2),
        "line_bytes": (b"a" * 64, "MAX_LINE_BYTES", 64),
        "words": (b"one two", "MAX_WORDS_PER_DOCUMENT", 2),
    }[kind]
    (tmp_path / "a.txt").write_bytes(contents)
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    return manifest, attribute, exact


@pytest.mark.parametrize(
    "kind",
    ["rows", "document_bytes", "cumulative", "physical_lines", "line_bytes", "words"],
)
def test_resource_ceiling_exact_and_one_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    manifest, attribute, exact = _ceiling_fixture(tmp_path, kind)
    monkeypatch.setattr(N, attribute, exact)
    assert _run_main(manifest, tmp_path / "at-limit.json")[0] == 0
    monkeypatch.setattr(N, attribute, exact - 1)
    over = tmp_path / "over.json"
    assert _run_main(manifest, over)[0] == 3
    assert not over.exists()


def test_manifest_and_report_byte_ceiling_exact_and_one_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.txt").write_bytes(b"one")
    manifest = tmp_path / "manifest.jsonl"
    raw = _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    monkeypatch.setattr(N, "MAX_MANIFEST_BYTES", len(raw))
    baseline = tmp_path / "baseline.json"
    assert _run_main(manifest, baseline)[0] == 0
    exact_report = len(baseline.read_bytes())

    monkeypatch.setattr(N, "MAX_REPORT_BYTES", exact_report)
    assert _run_main(manifest, tmp_path / "report-at-limit.json")[0] == 0
    monkeypatch.setattr(N, "MAX_REPORT_BYTES", exact_report - 1)
    assert _run_main(manifest, tmp_path / "report-over.json")[0] == 3

    monkeypatch.setattr(N, "MAX_REPORT_BYTES", N.MAX_REPORT_BYTES + exact_report)
    monkeypatch.setattr(N, "MAX_MANIFEST_BYTES", len(raw) - 1)
    assert _run_main(manifest, tmp_path / "manifest-over.json")[0] == 3


def test_long_valid_report_component_does_not_constrain_temporary_name(
    tmp_path: Path,
) -> None:
    document = tmp_path / "a.txt"
    document.write_text("words", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    report = tmp_path / ("r" * 225)
    assert _run_main(manifest, report)[0] == 0
    assert report.exists()
    assert not list(tmp_path.glob(".setec-nonprose-*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX capability contract")
def test_posix_missing_no_follow_capability_refuses_before_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reduced = set(os.supports_follow_symlinks)
    reduced.discard(os.stat)
    monkeypatch.setattr(N.os, "supports_follow_symlinks", reduced)
    with pytest.raises(N.ControlledFailure):
        N._posix_require_capabilities()


@pytest.mark.skipif(os.name != "posix", reason="POSIX capability contract")
def test_posix_missing_exclusive_rename_symbol_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(N.sys, "platform", "unsupported-posix")
    with pytest.raises(N.ControlledFailure, match="exclusive-rename"):
        N._posix_require_capabilities()


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor close contract")
def test_source_close_failure_is_controlled_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "a.txt"
    document.write_bytes(b"words")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    real_close = N.os.close
    source_reads = 0
    failed = False

    def arm(stage: str) -> None:
        nonlocal source_reads
        if stage == "source_read":
            source_reads += 1

    def fail_regular_close(descriptor: int) -> None:
        nonlocal failed
        is_regular = False
        try:
            is_regular = os.path.isfile(f"/dev/fd/{descriptor}")
        except OSError:
            pass
        real_close(descriptor)
        if source_reads >= 2 and is_regular and not failed:
            failed = True
            raise OSError("injected close failure")

    monkeypatch.setattr(N, "_FAULT_HOOK", arm)
    monkeypatch.setattr(N.os, "close", fail_regular_close)
    report = tmp_path / "report.json"
    assert _run_main(manifest, report)[0] == 3
    assert not report.exists()


def test_sanitized_usage_and_internal_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    assert N.main(["--unknown", "/private/sentinel"], stdout=stdout, stderr=stderr) == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "nonprose_sweep: invalid arguments\n"

    stdout = io.StringIO()
    stderr = io.StringIO()
    assert N.main(["--man", "m", "--rep", "r"], stdout=stdout, stderr=stderr) == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "nonprose_sweep: invalid arguments\n"

    monkeypatch.setattr(N, "_arguments", lambda _argv: (_ for _ in ()).throw(RuntimeError("secret")))
    stdout = io.StringIO()
    stderr = io.StringIO()
    assert N.main([], stdout=stdout, stderr=stderr) == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "nonprose_sweep: internal failure\n"


def test_success_stdout_failure_is_sanitized(tmp_path: Path) -> None:
    class BrokenBinary:
        def write(self, _raw: bytes) -> int:
            raise OSError("private sink detail")

        def flush(self) -> None:
            raise AssertionError("unreachable")

    class BrokenStdout:
        buffer = BrokenBinary()

    (tmp_path / "a.txt").write_text("words", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    stderr = io.StringIO()
    rc = N.main(
        ["--manifest", str(manifest), "--report-out", str(tmp_path / "report.json")],
        stdout=BrokenStdout(),
        stderr=stderr,
    )
    assert rc == 1
    assert stderr.getvalue() == "nonprose_sweep: internal failure\n"


def test_console_writer_completes_partial_writes_and_rejects_zero() -> None:
    class PartialBinary:
        def __init__(self, width: int) -> None:
            self.width = width
            self.raw = bytearray()

        def write(self, raw: bytes) -> int:
            count = min(self.width, len(raw))
            self.raw.extend(raw[:count])
            return count

        def flush(self) -> None:
            return None

    class Stream:
        def __init__(self, binary: PartialBinary) -> None:
            self.buffer = binary

    partial = PartialBinary(1)
    N._write_bytes(Stream(partial), b"exact\n")
    assert bytes(partial.raw) == b"exact\n"

    zero = PartialBinary(0)
    with pytest.raises(OSError):
        N._write_bytes(Stream(zero), b"refuse\n")


def test_broken_terminal_does_not_escape_main() -> None:
    class BrokenBinary:
        def write(self, _raw: bytes) -> int:
            raise OSError("broken")

    class BrokenStream:
        buffer = BrokenBinary()

    assert N.main(["--unknown"], stdout=io.StringIO(), stderr=BrokenStream()) == 2


def test_optional_posix_apis_and_flags_are_guarded() -> None:
    source = (SCRIPTS / "nonprose_sweep.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "chmod",
        "fchmod",
        "O_BINARY",
        "O_CLOEXEC",
        "O_NOFOLLOW",
        "O_DIRECTORY",
        "O_NONBLOCK",
    }
    direct = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
        and node.attr in forbidden
    }
    assert direct == set()


def test_native_subprocess_stdout_is_binary_lf(tmp_path: Path) -> None:
    document = tmp_path / "unicodé #.txt"
    document.write_text("HOST: hello", encoding="utf-8")
    manifest = tmp_path / "manifest #.jsonl"
    _write_manifest(manifest, [{"id": "opaque", "path": document.name}])
    report = tmp_path / "report #.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "nonprose_sweep.py"),
            "--manifest",
            str(manifest),
            "--report-out",
            str(report),
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert completed.stdout.endswith(b"\n")
    assert b"\r" not in completed.stdout
    assert report.read_bytes().endswith(b"\n")
    assert b"\r" not in report.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="native Windows helper contract")
def test_windows_helper_hardlink_policy(tmp_path: Path) -> None:
    import windows_descriptor_io as W

    source = tmp_path / "source.txt"
    source.write_bytes(b"source")
    alias = tmp_path / "alias.txt"
    os.link(source, alias)
    parent_parent, parent, _name = W.pin_directory(tmp_path, writable_final=True)
    W.close(parent_parent)
    try:
        with pytest.raises(OSError, match="multiple hard links"):
            W.open_file(parent, source.name)
        with pytest.raises(ValueError, match="component limit"):
            W.open_file(parent, "a" * 256)
        handle = W.open_file(parent, source.name, allow_multiple_links=True)
        try:
            with pytest.raises(OSError, match="multiple hard links"):
                W.require_direct(handle, "file")
            assert W.require_direct(handle, "file", allow_multiple_links=True).links == 2
            assert W.read(handle, 6) == b"source"
        finally:
            W.close(handle)
    finally:
        W.close(parent)

    extended = Path("\\\\?\\" + str(tmp_path))
    extended_parent, extended_directory, _name = W.pin_directory(
        extended, writable_final=False
    )
    W.close(extended_directory)
    W.close(extended_parent)


@pytest.mark.skipif(os.name != "nt", reason="native Windows helper contract")
def test_windows_helper_create_share_defaults_and_lockdown(tmp_path: Path) -> None:
    import windows_descriptor_io as W

    parent_parent, parent, _name = W.pin_directory(tmp_path, writable_final=True)
    W.close(parent_parent)
    try:
        default = W.create_file(parent, "default.bin")
        try:
            peer_write = W.open_file(parent, "default.bin", writable=True)
            W.close(peer_write)
            peer_delete = W.open_file(parent, "default.bin", delete_access=True)
            W.close(peer_delete)
        finally:
            W.close(default)
        os.unlink(tmp_path / "default.bin")

        locked = W.create_file(
            parent, "locked.bin", share_write=False, share_delete=False
        )
        try:
            with pytest.raises(OSError) as write_error:
                W.open_file(parent, "locked.bin", writable=True)
            assert write_error.value.winerror in {5, 32}
            with pytest.raises(OSError) as delete_error:
                W.open_file(parent, "locked.bin", delete_access=True)
            assert delete_error.value.winerror in {5, 32}
        finally:
            W.close(locked)
        os.unlink(tmp_path / "locked.bin")
    finally:
        W.close(parent)


@pytest.mark.skipif(os.name != "nt", reason="native Windows helper contract")
def test_windows_helper_post_create_validation_failure_cleans_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import windows_descriptor_io as W

    parent_parent, parent, _name = W.pin_directory(tmp_path, writable_final=True)
    W.close(parent_parent)
    real_require = W.require_direct

    def fail_file(handle: int, kind: str, **kwargs: object) -> object:
        if kind == "file":
            raise OSError("injected")
        return real_require(handle, kind, **kwargs)

    try:
        monkeypatch.setattr(W, "require_direct", fail_file)
        with pytest.raises(OSError, match="injected"):
            W.create_file(parent, "residue.bin")
        assert not (tmp_path / "residue.bin").exists()
        monkeypatch.setattr(W, "require_direct", real_require)
        handle = W.create_file(parent, "residue.bin")
        W.close(handle)
    finally:
        W.close(parent)


@pytest.mark.skipif(os.name != "nt", reason="native Windows helper contract")
def test_windows_output_preflight_memoryerror_is_controlled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import windows_descriptor_io as W

    def fail_open(_parent: int, _name: str) -> int:
        raise MemoryError("injected")

    monkeypatch.setattr(W, "open_node", fail_open)
    with pytest.raises(N.ControlledFailure):
        N._preflight_output_absent(tmp_path / "report.json")


@pytest.mark.skipif(os.name != "nt", reason="native Windows input-root contract")
def test_windows_unc_manifest_root_is_refused_before_backend_open() -> None:
    with pytest.raises(N.ControlledFailure, match="network input root"):
        N._windows_pin_directory(Path(r"\\server\share\corpus"))


@pytest.mark.skipif(os.name != "nt", reason="native Windows CLI contract")
@pytest.mark.parametrize(
    ("separator", "terminal"),
    [(b"\n", True), (b"\r\n", True), (b"\r", True), (b"\n", False)],
)
def test_windows_cli_newline_unicode_and_hash_paths(
    tmp_path: Path, separator: bytes, terminal: bool
) -> None:
    case = tmp_path / f"case-{separator.hex()}-{int(terminal)} #"
    case.mkdir()
    document = case / "unicodé #.txt"
    document_raw = separator.join([b"HOST: hello", b"continued"])
    if terminal:
        document_raw += separator
    document.write_bytes(document_raw)
    manifest = case / "manifest #.jsonl"
    row = json.dumps(
        {"id": "opaque", "path": document.name},
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest.write_bytes(separator.join([b"# comment", b"", row]) + (separator if terminal else b""))
    report = case / "report #.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "nonprose_sweep.py"),
            "--manifest",
            str(manifest),
            "--report-out",
            str(report),
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert completed.stdout.endswith(b"\n") and b"\r" not in completed.stdout
    assert report.read_bytes().endswith(b"\n") and b"\r" not in report.read_bytes()
    metrics = json.loads(report.read_bytes())["documents"][0]
    assert metrics["total_analyzable_words"] == 3
    assert metrics["transcript_words"] == 3


@pytest.mark.skipif(os.name != "nt", reason="native Windows CLI contract")
def test_windows_inputs_and_report_handles_close_before_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "a.txt"
    document.write_text("HOST: words", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    report = tmp_path / "report.json"
    moved_document = tmp_path / "moved-document.txt"
    moved_manifest = tmp_path / "moved-manifest.jsonl"

    def move_inputs(stage: str) -> None:
        if stage == "publish_before":
            os.replace(document, moved_document)
            os.replace(manifest, moved_manifest)

    monkeypatch.setattr(N, "_FAULT_HOOK", move_inputs)
    assert _run_main(manifest, report)[0] == 0
    moved_report = tmp_path / "moved-report.json"
    os.replace(report, moved_report)
    os.unlink(moved_report)


@pytest.mark.skipif(os.name != "nt", reason="native Windows CLI contract")
def test_windows_create_new_race_preserves_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "a.txt"
    document.write_text("words", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": "a.txt"}])
    report = tmp_path / "report.json"
    winner = b"winner"

    def install_winner(stage: str) -> None:
        if stage == "publish_before":
            report.write_bytes(winner)

    monkeypatch.setattr(N, "_FAULT_HOOK", install_winner)
    rc, stdout, stderr = _run_main(manifest, report)
    assert rc == 3
    assert stdout == ""
    assert stderr == "nonprose_sweep: input, resource, or publication validation failed\n"
    assert report.read_bytes() == winner
    assert not list(tmp_path.glob(".setec-nonprose-*.tmp"))
    os.unlink(report)


@pytest.mark.skipif(os.name != "nt", reason="native Windows CLI contract")
def test_windows_cli_invalid_utf8_is_controlled(tmp_path: Path) -> None:
    document = tmp_path / "bad #.txt"
    document.write_bytes(b"\xff")
    manifest = tmp_path / "manifest #.jsonl"
    _write_manifest(manifest, [{"id": "a", "path": document.name}])
    report = tmp_path / "report #.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "nonprose_sweep.py"),
            "--manifest",
            str(manifest),
            "--report-out",
            str(report),
        ],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 3
    assert completed.stdout == b""
    assert completed.stderr == (
        b"nonprose_sweep: input, resource, or publication validation failed\n"
    )
    assert not report.exists()
