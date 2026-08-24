#!/usr/bin/env python3
"""Synthetic, network-free tests for the Stack Exchange dump acquirer."""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import acquire_stackexchange as se  # noqa: E402


def _write_xml(path: Path, root_name: str, rows: list[dict]) -> None:
    root = ET.Element(root_name)
    for attrs in rows:
        ET.SubElement(root, "row", {key: str(value) for key, value in attrs.items()})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def make_dump(path: Path, *, missing_license=False, missing_author=False) -> Path:
    path.mkdir()
    _write_xml(path / "Users.xml", "users", [
        {"Id": "7", "DisplayName": "Ada Example"},
    ])
    question = {
        "Id": "10", "PostTypeId": "1", "OwnerUserId": "7",
        "CreationDate": "2021-01-01T10:00:00.000",
        "LastEditDate": "2024-01-02T11:00:00.000",
        "ContentLicense": "CC BY-SA 4.0", "Score": "3",
        "Title": "Synthetic question", "Tags": "|ethics|logic|",
        "Body": "<p>Alpha &amp; beta.</p><script>drop me</script><pre>code sample</pre>",
    }
    if missing_license:
        question.pop("ContentLicense")
    if missing_author:
        question.pop("OwnerUserId")
    _write_xml(path / "Posts.xml", "posts", [
        question,
        {
            "Id": "11", "PostTypeId": "2", "ParentId": "10",
            "OwnerDisplayName": "Deleted User", "CreationDate": "2020-02-03T00:00:00.000",
            "ContentLicense": "CC BY-SA 3.0", "Score": "-1",
            "Body": "<p>Gamma delta epsilon.</p>",
        },
        {
            "Id": "12", "PostTypeId": "4", "OwnerUserId": "7",
            "CreationDate": "2020-02-03T00:00:00.000",
            "ContentLicense": "CC BY-SA 3.0", "Body": "<p>Tag wiki.</p>",
        },
    ])
    return path


def _run_args(dump: Path, out: Path, *extra: str) -> list[str]:
    return ["--dump", str(dump), "--site", "philosophy.stackexchange.com",
            "--out", str(out), "--allow-public-output", *extra]


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_body_text_and_per_row_attribution_license(tmp_path):
    dump = make_dump(tmp_path / "dump")
    users = se.load_users(dump, "philosophy.stackexchange.com")
    counts = {}
    rows = list(se.iter_posts(dump, "philosophy.stackexchange.com", users,
                              post_types=("1", "2"), keep_body_html=False,
                              scan_counts=counts))
    assert counts == {"rows": 3, "post_type": 1}
    assert [row["content_license"] for row in rows] == ["CC BY-SA 4.0", "CC BY-SA 3.0"]
    assert rows[0]["author_display_name"] == "Ada Example"
    assert rows[0]["author_profile_url"].endswith("/users/7")
    assert rows[0]["effective_date"] == "2024-01-02T11:00:00.000"
    assert "drop me" not in rows[0]["text"]
    assert rows[1]["author_deleted"] is True
    assert rows[1]["source_url"].endswith("/questions/10#11")


@pytest.mark.parametrize("case,error", [
    ("missing_license", "ContentLicense"),
    ("missing_author", "attributable author"),
])
def test_missing_licensing_or_attribution_fails_closed(tmp_path, case, error):
    dump = make_dump(tmp_path / "dump", **{case: True})
    users = se.load_users(dump, "philosophy.stackexchange.com")
    with pytest.raises(ValueError, match=error):
        list(se.iter_posts(dump, "philosophy.stackexchange.com", users,
                           post_types=("1", "2"), keep_body_html=False))


def test_cli_validates_host_dates_and_positive_bounds():
    parser = se.build_parser()
    base = ["--dump", ".", "--site", "philosophy.stackexchange.com", "--dry-run"]
    assert parser.parse_args(base).site == "philosophy.stackexchange.com"
    for args in (
        ["--dump", ".", "--site", "https://example.com", "--dry-run"],
        ["--dump", ".", "--site", "a..b", "--dry-run"],
        ["--dump", ".", "--site", "a.-b.com", "--dry-run"],
        base + ["--post-types", "3"],
        base + ["--all-post-types"],
        base + ["--created-before", "2024-99-01"],
        base + ["--max-items", "0"],
        base + ["--progress-every", "-1"],
    ):
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(args)
        assert exc.value.code == 2


def test_write_filter_summary_and_resume_are_source_bound(tmp_path):
    dump = make_dump(tmp_path / "dump")
    out = tmp_path / "posts.jsonl"
    summary = tmp_path / "summary.json"
    assert se.main(_run_args(dump, out, "--max-items", "1", "--progress-every", "1",
                             "--summary", str(summary))) == 0
    assert [row["post_id"] for row in _records(out)] == ["10"]
    assert json.loads(summary.read_text(encoding="utf-8"))["posts_scanned"] == 1

    assert se.main(_run_args(dump, out, "--resume", "--progress-every", "1")) == 0
    assert [row["post_id"] for row in _records(out)] == ["10", "11"]
    state = json.loads((tmp_path / "posts.jsonl.resume.json").read_text(encoding="utf-8"))
    assert state["complete"] is True
    assert state["records"] == 2

    # A changed filter cannot append a mixed-contract tail.
    assert se.main(_run_args(dump, out, "--resume", "--min-words", "99")) == 2
    assert [row["post_id"] for row in _records(out)] == ["10", "11"]


def test_resume_repairs_only_an_incomplete_final_line(tmp_path):
    dump = make_dump(tmp_path / "dump")
    out = tmp_path / "posts.jsonl"
    assert se.main(_run_args(dump, out, "--max-items", "1")) == 0
    with out.open("ab") as fh:
        fh.write(b'{"partial":')
    assert se.main(_run_args(dump, out, "--resume")) == 0
    assert [row["post_id"] for row in _records(out)] == ["10", "11"]


def test_resume_refuses_complete_malformed_final_line(tmp_path):
    dump = make_dump(tmp_path / "dump")
    out = tmp_path / "posts.jsonl"
    assert se.main(_run_args(dump, out, "--max-items", "1")) == 0
    with out.open("ab") as fh:
        fh.write(b"not-json\n")
    before = out.read_bytes()
    assert se.main(_run_args(dump, out, "--resume")) == 2
    assert out.read_bytes() == before


def test_resume_refuses_record_with_stale_content_hash(tmp_path):
    dump = make_dump(tmp_path / "dump")
    out = tmp_path / "posts.jsonl"
    assert se.main(_run_args(dump, out, "--max-items", "1")) == 0
    rows = _records(out)
    rows[0]["text"] = "tampered while retaining the old digest"
    out.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    before = out.read_bytes()
    assert se.main(_run_args(dump, out, "--resume")) == 2
    assert out.read_bytes() == before


def test_resume_refuses_metadata_tamper_against_bound_dump(tmp_path):
    dump = make_dump(tmp_path / "dump")
    out = tmp_path / "posts.jsonl"
    assert se.main(_run_args(dump, out, "--max-items", "1")) == 0
    rows = _records(out)
    rows[0]["content_license"] = "CC0-TAMPERED"
    rows[0]["author_profile_url"] = "https://evil.invalid/not-the-author"
    out.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    before = out.read_bytes()
    assert se.main(_run_args(dump, out, "--resume")) == 2
    assert out.read_bytes() == before


def test_resume_refuses_non_object_sidecar_without_traceback(tmp_path):
    dump = make_dump(tmp_path / "dump")
    out = tmp_path / "posts.jsonl"
    assert se.main(_run_args(dump, out, "--max-items", "1")) == 0
    sidecar = tmp_path / "posts.jsonl.resume.json"
    sidecar.write_text("[]\n", encoding="utf-8")
    before = out.read_bytes()
    assert se.main(_run_args(dump, out, "--resume")) == 2
    assert out.read_bytes() == before


def test_zero_output_fails_unless_explicitly_allowed(tmp_path):
    dump = make_dump(tmp_path / "dump")
    out = tmp_path / "empty.jsonl"
    assert se.main(_run_args(dump, out, "--min-words", "999")) == 1
    state = json.loads((tmp_path / "empty.jsonl.resume.json").read_text(encoding="utf-8"))
    assert state["complete"] is False

    allowed = tmp_path / "allowed.jsonl"
    assert se.main(_run_args(dump, allowed, "--min-words", "999", "--allow-empty")) == 0
    assert allowed.read_text(encoding="utf-8") == ""

    assert se.main([
        "--dump", str(dump), "--site", "philosophy.stackexchange.com",
        "--dry-run", "--min-words", "999",
    ]) == 1
    assert se.main([
        "--dump", str(dump), "--site", "philosophy.stackexchange.com",
        "--dry-run", "--min-words", "999", "--allow-empty",
    ]) == 0


def test_public_output_requires_explicit_override(tmp_path):
    dump = make_dump(tmp_path / "dump")
    with pytest.raises(SystemExit) as exc:
        se.main(["--dump", str(dump), "--site", "philosophy.stackexchange.com",
                 "--out", str(tmp_path / "posts.jsonl")])
    assert exc.value.code == 2
