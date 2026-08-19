"""Focused tests for the Voiceprint #396 Trafilatura research packet."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
MANIFEST = FIXTURES / "manifest.json"
WORKER = ROOT / "workers" / "trafilatura_probe.py"
REPO_ROOT = ROOT.parents[2]
SCRIPTS = REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"

_missing = [
    name
    for name in ("bs4", "feedparser", "trafilatura")
    if importlib.util.find_spec(name) is None
]
if _missing:
    pytestmark = pytest.mark.skip(
        reason="issue-396 locked acquisition dependencies missing: " + ", ".join(_missing)
    )


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _request(*, root: Path = FIXTURES, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    manifest = _manifest()
    return {
        "schema": "setec-dependency-evaluation/1",
        "action": "trafilatura",
        "expected_environment": {
            "python": "3.13.7",
            "trafilatura": "2.1.0",
        },
        "fixture_root": str(root),
        "fixtures": rows if rows is not None else manifest["files"],
        "reruns": 5,
    }


def _run(request: dict[str, Any]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(WORKER)],
        input=(json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


@pytest.fixture(scope="module")
def packet() -> dict[str, Any]:
    completed = _run(_request())
    assert completed.returncode == 0, completed.stdout.decode("utf-8", "replace")
    assert completed.stderr == b""
    return json.loads(completed.stdout)


def test_manifest_hashes_licenses_and_fixture_classes_are_complete():
    manifest = _manifest()
    assert manifest["schema"] == "setec-dependency-fixtures/1"
    assert len(manifest["files"]) == 10
    html_rows = [row for row in manifest["files"] if row["kind"] == "html"]
    feed_rows = [row for row in manifest["files"] if row["kind"] == "feed"]
    scored = [row for row in html_rows if row["score_bearing"]]
    failures = [row for row in html_rows if not row["score_bearing"]]
    assert len(scored) == 6
    assert len(failures) == 2
    assert {row["structure_class"] for row in scored} == {
        "archive-publication-layout",
        "federal-government-article",
        "legal-technical-document",
    }
    assert {row["format"] for row in feed_rows} == {"rss", "atom"}

    for row in manifest["files"]:
        path = FIXTURES / row["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        for field in (
            "source_url",
            "retrieved",
            "attribution",
            "license",
            "license_evidence_url",
            "transformation",
        ):
            assert isinstance(row[field], str) and row[field].strip(), (row["path"], field)
    for row in scored:
        assert row["required_markers"]
        assert row["forbidden_markers"]
        assert isinstance(row["strip_selectors"], list)
        assert "expected_title" in row


def test_manifest_uses_exact_shipped_selector_sets():
    sys.path.insert(0, str(SCRIPTS))
    import acquire_blog  # type: ignore
    import acquire_everycrsreport  # type: ignore
    import acquire_govinfo_chrg  # type: ignore

    rows = {row["path"]: row for row in _manifest()["files"] if row["kind"] == "html"}
    for name in (
        "html/white-house-copyright.html",
        "html/nasa-media-policy.html",
        "html/python-psf-license.html",
        "html/empty.html",
        "html/malformed.html",
    ):
        assert tuple(rows[name]["strip_selectors"]) == acquire_blog.DEFAULT_STRIP_SELECTORS
    assert rows["html/white-house-copyright.html"]["content_selector"] in acquire_blog.DEFAULT_CONTENT_SELECTORS
    assert rows["html/nasa-media-policy.html"]["content_selector"] in acquire_blog.DEFAULT_CONTENT_SELECTORS
    assert rows["html/python-psf-license.html"]["content_selector"] in acquire_blog.DEFAULT_CONTENT_SELECTORS

    crs = rows["html/everycrs-report-layout.html"]
    assert crs["content_selector"] in acquire_everycrsreport.DEFAULT_CONTENT_SELECTORS
    assert tuple(crs["strip_selectors"]) == acquire_everycrsreport.DEFAULT_STRIP_SELECTORS
    for name in ("html/uscode-section-105.html", "html/govinfo-hearing-layout.html"):
        assert rows[name]["content_selector"] is None
        assert tuple(rows[name]["strip_selectors"]) == acquire_govinfo_chrg.DEFAULT_STRIP_SELECTORS


def test_real_worker_attributes_primary_and_full_seam_results(packet):
    assert packet["schema"] == "setec-dependency-evaluation/1"
    assert packet["environment"]["python"] == "3.13.7"
    assert packet["environment"]["trafilatura"] == "2.1.0"
    aggregate = packet["semantic"]["html_aggregate"]
    assert aggregate["all_reruns_deterministic"] is True
    assert aggregate["primary_success_by_structure_class"] == {
        "archive-publication-layout": True,
        "federal-government-article": True,
        "legal-technical-document": True,
    }
    assert aggregate["full_seam_lost_all_required"] == []
    for name in ("fallback", "primary", "full_seam"):
        assert aggregate["paths"][name]["macro_recall"] == 1.0
        assert aggregate["paths"][name]["macro_leakage"] == 0.0

    # This packet intentionally reports rather than hides the current title
    # regression: Trafilatura metadata usually selects H1 while the fallback
    # retains the HTML <title>.  The controller must therefore reject under
    # the v4 decision rule even though text recall is perfect.
    assert aggregate["paths"]["fallback"]["title_retained_count"] == 6
    assert aggregate["paths"]["full_seam"]["title_retained_count"] < 6

    perf = packet["performance"]["html_aggregate"]
    assert perf["fallback_median_seconds"] > 0
    assert perf["full_seam_median_seconds"] > 0
    assert perf["full_seam_to_fallback_ratio"] > 0


def test_empty_and_malformed_inputs_remain_fail_soft(packet):
    aggregate = packet["semantic"]["html_aggregate"]
    assert aggregate["failure_cases_fail_soft"] is True
    rows = {
        row["path"]: row for row in packet["semantic"]["html_fixtures"]
    }
    for name in ("html/empty.html", "html/malformed.html"):
        assert rows[name]["score_bearing"] is False
        for seam in ("fallback", "primary", "full_seam"):
            assert all(run["raised"] is False for run in rows[name]["runs"][seam])


def test_feed_probe_exposes_order_dedup_and_full_contract_gap(packet):
    feeds = {
        row["format"]: row for row in packet["semantic"]["feed_fixtures"]
    }
    assert set(feeds) == {"rss", "atom"}
    for row in feeds.values():
        assert row["expected_items_match"] is True
        assert row["link_comparison"]["set_equal"] is True
        assert row["link_comparison"]["order_equal"] is False
        assert row["replacement_allowed"] is False
        assert row["contract_fields"] == [
            "title",
            "link",
            "date",
            "body_html",
            "is_paid",
            "raw_byte_length",
        ]
    assert feeds["rss"]["link_comparison"]["deduplication_equal"] is False
    assert feeds["atom"]["link_comparison"]["deduplication_equal"] is True


def test_semantic_block_is_stable_across_fresh_worker_processes(packet):
    completed = _run(_request())
    assert completed.returncode == 0
    rerun = json.loads(completed.stdout)
    assert rerun["environment"] == packet["environment"]
    assert rerun["semantic"] == packet["semantic"]
    # Performance is measured but deliberately excluded from the stable block.
    assert "performance" in rerun


def test_worker_refuses_fixture_corruption(tmp_path):
    row = next(row for row in _manifest()["files"] if row["path"].endswith("white-house-copyright.html"))
    copied = tmp_path / row["path"]
    copied.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES / row["path"], copied)
    copied.write_bytes(copied.read_bytes() + b"\ncorrupt")
    completed = _run(_request(root=tmp_path, rows=[row]))
    assert completed.returncode == 2
    error = json.loads(completed.stdout)
    assert error["error"] == "ProtocolError"
    assert "fixture hash mismatch" in error["message"]


def test_worker_refuses_wrong_locked_identity():
    request = _request(rows=[])
    request["expected_environment"]["trafilatura"] = "0.0.0"
    completed = _run(request)
    assert completed.returncode == 2
    error = json.loads(completed.stdout)
    assert error["error"] == "ProtocolError"
    assert "environment mismatch for trafilatura" in error["message"]
