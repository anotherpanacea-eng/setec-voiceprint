#!/usr/bin/env python3
"""Regression tests for acquire_govinfo_chrg.py.

Strategy mirrors test_acquire_everycrsreport.py: mock the GovInfo API with
`acquisition_core.FixtureFetcher`, mapping the exact request URLs (built via
the module's own URL helpers, so the map can't drift from what discovery
requests) to local fixtures under
``scripts/test_data/acquire_govinfo_chrg_fixture/``:

  * published.json     — 2 hearing packages.
  * granules_pkg1.json — the single whole-hearing granule for package 1.
  * granules_pkg2.json — the single whole-hearing granule for package 2.
  * hearing_pkg1.htm   — a hearing transcript with two prepared statements
                         (Jane Smith, long; Bob Short, short) plus oral
                         testimony / Q&A that must NOT be captured.
  * hearing_pkg2.htm   — a hearing with one prepared statement (Robert Jones)
                         plus a member opening (no heading → not captured).

CHRG packages are single whole-hearing granules, so discovery fetches the
hearing HTM and splits it on the ``Prepared Statement of <Name>`` heading.

Invariants: the date-only /published bound; JSON pagination via nextPage;
api_key URL threading; HTM → clean text; the heading-anchored statement split
(oral Q&A + member statements dropped); witness-name parse; the --min-words
gate (Short dropped); the impostor manifest schema with register
testimony_policy; dedupe; the privacy guard; argparse required flags; and a
manifest-validator integration.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from conftest import read_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

_acq_deps_available = True
_skip_reason = ""
try:
    import bs4  # type: ignore  # noqa: F401
except ImportError as _e:
    _acq_deps_available = False
    _skip_reason = (
        f"acquisition deps missing ({_e}); install with "
        "`pip install -r requirements-acquisition.txt`"
    )

if _acq_deps_available:
    import acquisition_core as ac  # type: ignore
    import acquire_govinfo_chrg as gi  # type: ignore
    import manifest_validator as mv  # type: ignore

if pytest is not None and not _acq_deps_available:
    pytestmark = pytest.mark.skip(reason=_skip_reason)


FIXTURE_DIR = ROOT / "test_data" / "acquire_govinfo_chrg_fixture"
KEY = "TESTKEY"
PKG1 = "CHRG-116hhrg11111"
PKG2 = "CHRG-116hhrg22222"


# ------------------- Helpers -------------------------------------


def make_args(**overrides) -> argparse.Namespace:
    base = dict(
        api_key=KEY,
        collection="CHRG",
        persona="chrg",
        author="",
        impostor_for=["argscope_testimony_policy"],
        register="testimony_policy",
        register_match="high",
        topic_match="medium",
        consent_status="public_record",
        era="pre_chatgpt",
        since="2000-01-01",
        until="2021-12-31",
        max_items=400,
        min_words=150,
        output_dir=None,
        emit_manifest=None,
        out=None,
        rate_limit=0.0,
        user_agent=None,
        dry_run=False,
        allow_empty=False,
        allow_public_output=True,
        allow_non_prose=False,
        strip_rules=None,
        strip_aggressive=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def fixture_url_map() -> dict:
    """Build the URL→fixture map using the module's own URL helpers, so the
    keys are byte-identical to what discovery/extraction request."""
    start = gi._govinfo_date(dt.date(2000, 1, 1))
    end = gi._govinfo_date(dt.date(2021, 12, 31))
    return {
        gi._published_url(start, end, KEY, collection="CHRG"): "published.json",
        gi._granules_url(PKG1, KEY): "granules_pkg1.json",
        gi._granules_url(PKG2, KEY): "granules_pkg2.json",
        # CHRG granuleId == packageId; the hearing HTM is fetched with the key
        # appended, while the stored locator stays credential-free.
        gi._add_query(gi._granule_content_url(PKG1, PKG1), api_key=KEY): "hearing_pkg1.htm",
        gi._add_query(gi._granule_content_url(PKG2, PKG2), api_key=KEY): "hearing_pkg2.htm",
    }


def make_fetcher(url_map: dict | None = None) -> ac.FixtureFetcher:
    return ac.FixtureFetcher(
        url_map=dict(url_map if url_map is not None else fixture_url_map()),
        fixture_dir=FIXTURE_DIR,
        rate_limit_seconds=0.0,
        respect_robots=False,
    )


# ------------------- Unit: URL + parse helpers -------------------


def test_add_query_sets_and_overrides():
    u = gi._add_query("https://x.test/a?b=1", api_key="K")
    assert "b=1" in u and "api_key=K" in u
    # Overriding doesn't duplicate.
    u2 = gi._add_query(u, api_key="K2")
    assert u2.count("api_key=") == 1 and "api_key=K2" in u2


def test_govinfo_date_is_date_only():
    # The /published path rejects the dateTime form; the bound is YYYY-MM-DD.
    assert gi._govinfo_date(dt.date(2018, 1, 2)) == "2018-01-02"


def test_witness_name():
    assert gi._witness_name("Jane Smith, Director, Office of Widgets") == "Jane Smith"
    assert gi._witness_name("Hon. Robert Jones") == "Hon. Robert Jones"
    assert gi._witness_name("") == gi.WITNESS_FALLBACK


def test_split_prepared_statements():
    text = (
        "STATEMENT OF JANE DOE, DIRECTOR\n"
        "    Ms. DOE. Thank you, Mr. Chairman.\n"
        "    [The prepared statement of Ms. Doe follows:]\n"
        "Prepared Statement of Jane Doe, Director, Office of Examples\n"
        "First paragraph of the written statement, which develops an argument\n"
        "at some length about the matter before the committee.\n"
        "    [Questions and answers follow.]\n"
        "    The CHAIRMAN. Thank you, and now Mr. Roe.\n"
        "Prepared Statement of John Roe, Fellow\n"
        "The second witness's written statement body.\n"
        "    [Whereupon the hearing was adjourned.]\n"
    )
    stmts = gi._split_prepared_statements(text).statements
    assert [block.witness for block in stmts] == ["Jane Doe", "John Roe"]
    doe_body = stmts[0].body
    assert "written statement" in doe_body
    # Bounded at the bracket marker; oral header not captured.
    assert "Questions and answers" not in doe_body
    assert "STATEMENT OF JANE DOE" not in doe_body
    # No prepared-statement headings → no statements.
    assert gi._split_prepared_statements("Markup of H.R. 1. The CHAIRMAN. ...").statements == []


def test_oral_turn_closes_written_candidate_before_later_marker():
    """An ordinary speaker turn must not enter an earlier written block."""
    written = "The written analysis explains the synthetic widget program. " * 8
    hearing = (
        "<html><body><pre>Prepared Statement of Ada One\n"
        + written + "\nSenator Vale. Spoken questions begin here.\n"
        + "[The statement follows:]\n"
        + "Prepared Statement of Bea Two\n"
        + written + "\n[Questions and answers follow.]</pre></body></html>"
    )
    url = gi._add_query(gi._granule_content_url(PKG1, PKG1), api_key=KEY)
    mapping = fixture_url_map()
    mapping[url] = ac.FetchResult(url=url, status=200, text=hearing)
    items = list(gi.discover_items(gi.parse_options(make_args()), make_fetcher(mapping)))
    ada = next(item for item in items if item.author == "Ada One")
    assert "Spoken questions" not in ada.body_text
    assert any(item.author == "Bea Two" for item in items)


def test_known_written_brackets_and_separator_keep_exact_offsets():
    text = "\n".join([
        "Prepared Statement of Ada One",
        "The record includes an inline [citation] and a written note.",
        "[1]",
        "[Exhibit A]",
        "[Table 2]",
        "[42 U.S.C. 123]",
        "____",
        "The internal separator above is part of this exhibit.",
        "____",
        "Prepared Statement of Bea Two",
        "Bea's separate written body.",
        "[Questions and answers follow.]",
    ])
    result = gi._split_prepared_statements(text)
    assert result.issues == []
    assert len(result.statements) == 2
    ada = result.statements[0]
    assert ada.boundary_kind == "next-prepared-heading"
    assert text[ada.body_start:ada.body_end] == ada.body
    for marker in ("[citation]", "[1]", "[Exhibit A]", "[Table 2]",
                   "[42 U.S.C. 123]", "____\nThe internal separator"):
        assert marker in ada.body
    assert not ada.body.endswith("____")
    assert "Bea's" not in ada.body


def test_unknown_transition_and_structural_label_refuse_prior():
    written = "This is an otherwise plausible synthetic written paragraph."
    for transition, expected_reason in (
        ("[Proceedings resumed.]", "ambiguous-bracket-transition"),
        ("The STAFF DIRECTOR. Spoken remarks.", "ambiguous-speaker-turn"),
    ):
        text = "\n".join([
            "Prepared Statement of Ada One", written, transition,
            "Prepared Statement of Bea Two", written,
            "[Questions and answers follow.]",
        ])
        result = gi._split_prepared_statements(text)
        assert [(i.heading_ordinal, i.reason) for i in result.issues] == [
            (1, expected_reason)
        ]
        assert [block.witness for block in result.statements] == ["Bea Two"]


def test_structural_turns_quote_cue_oral_heading_and_unbounded_refusals():
    written = "This is a synthetic written paragraph about a widget rule."
    for label in ("The WITNESS.", "The COUNSEL."):
        text = "\n".join([
            "Prepared Statement of Ada One", written,
            label + " Oral answer.", "Prepared Statement of Bea Two",
            written, "[Questions and answers follow.]",
        ])
        result = gi._split_prepared_statements(text)
        assert result.issues == []
        assert result.statements[0].body == written
        assert result.statements[0].boundary_kind == "oral-speaker-turn"
        assert len(result.statements) == 2

    quoted = "\n".join([
        "Prepared Statement of Ada One", written,
        '"Senator Vale. This is quoted in the written text."',
        "[Questions and answers follow.]",
    ])
    result = gi._split_prepared_statements(quoted)
    assert len(result.statements) == 1
    assert '"Senator Vale.' in result.statements[0].body

    paired = "\n".join([
        "Prepared Statement of Ada One", written,
        "STATEMENT OF BEA TWO", "Ms. TWO. Oral remarks.",
    ])
    assert gi._split_prepared_statements(paired).statements[0].body == written
    unpaired = "\n".join([
        "Prepared Statement of Ada One", written, "STATEMENT OF BEA TWO",
    ])
    assert gi._split_prepared_statements(unpaired).issues[0].reason == (
        "unpaired-oral-heading"
    )
    intervening = "\n".join([
        "Prepared Statement of Ada One", written, "STATEMENT OF BEA TWO",
        "[Proceedings resumed.]", "Ms. TWO. Oral remarks.",
    ])
    assert gi._split_prepared_statements(intervening).issues[0].reason == (
        "unpaired-oral-heading"
    )
    assert gi._split_prepared_statements(
        "Prepared Statement of Ada One\n" + written
    ).issues[0].reason == "unbounded-eof"


def _split_synthetic_html(lines):
    """Exercise the same HTML decoding seam used by CHRG discovery."""
    html = "<html><body><pre>" + "\n".join(lines) + "</pre></body></html>"
    decoded, _ = ac.html_to_text(
        html, strip_selectors=gi.DEFAULT_STRIP_SELECTORS,
    )
    return gi._split_prepared_statements(decoded)


@pytest.mark.parametrize("label", (
    "Senator Van Hollen.",
    "Representative De La Cruz.",
    "Director Van Hollen.",
    "Mr. Van Hollen.",
    "Dr. De La Cruz.",
    "Rear Admiral Vale.",
    "Rear Admiral Van Hollen.",
    "Lieutenant Colonel Vale.",
    "Ms. De La Cruz.",
))
def test_compound_speaker_name_closes_before_later_heading(label):
    result = _split_synthetic_html([
        "Prepared Statement of Ada One", "Written block.",
        label + " Oral turn here.",
        "Prepared Statement of Bea Two", "Independent body.",
        "[Questions and answers follow.]",
    ])
    assert result.issues == []
    assert [(block.witness, block.body, block.boundary_kind)
            for block in result.statements] == [
        ("Ada One", "Written block.", "oral-speaker-turn"),
        ("Bea Two", "Independent body.", "procedural-bracket"),
    ]


@pytest.mark.parametrize("heading", (
    "Director of Operations.",
    "Secretary of State.",
    "Director for Widget Programs.",
))
def test_office_title_heading_refuses_instead_of_clean_oral_close(heading):
    result = _split_synthetic_html([
        "Prepared Statement of Ada One", "Written opening.",
        heading, "Written analysis continues.",
        "Prepared Statement of Bea Two", "Independent body.",
        "[Questions and answers follow.]",
    ])
    assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [
        (1, "ambiguous-role-heading")
    ]
    assert [(block.witness, block.body) for block in result.statements] == [
        ("Bea Two", "Independent body.")
    ]

def test_inline_office_title_remains_written_prose():
    result = _split_synthetic_html([
        "Prepared Statement of Ada One",
        "The report mentions the Director of Operations in its analysis.",
        "[Questions and answers follow.]",
    ])
    assert result.issues == []
    assert len(result.statements) == 1
    assert "Director of Operations" in result.statements[0].body
    assert result.statements[0].boundary_kind == "procedural-bracket"

def test_standalone_curly_quote_cue_refuses_ambiguous_speaker():
    result = _split_synthetic_html([
        "Prepared Statement of Ada One", "Analysis starts.",
        "“", "Senator Vale. Quoted source exchange.", "”",
        "Analysis resumes.",
        "Prepared Statement of Bea Two", "Independent body.",
        "[Questions and answers follow.]",
    ])
    assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [
        (1, "ambiguous-quoted-boundary")
    ]
    assert [(block.witness, block.body) for block in result.statements] == [
        ("Bea Two", "Independent body.")
    ]


@pytest.mark.parametrize("bracket", (
    "[Exhibit A admitted.]", "[Table 2 admitted.]",
    "[42 U.S.C. 123 admitted.]",
))
def test_procedural_text_in_content_bracket_refuses_prior(bracket):
    result = _split_synthetic_html([
        "Prepared Statement of Ada One", "Written block.",
        bracket, "Oral transcript resumes.",
        "Prepared Statement of Bea Two", "Independent body.",
        "[Questions and answers follow.]",
    ])
    assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [
        (1, "ambiguous-bracket-transition")
    ]
    assert [(block.witness, block.body) for block in result.statements] == [
        ("Bea Two", "Independent body.")
    ]

@pytest.mark.parametrize("label", (
    "THE STAFF DIRECTOR.", "The Staff Director.",
    "The FIELD OFFICER.",
))
def test_unsupported_structural_label_case_refuses_prior(label):
    result = _split_synthetic_html([
        "Prepared Statement of Ada One", "Written block.",
        label + " Oral question.",
        "Prepared Statement of Bea Two", "Independent body.",
        "[Questions and answers follow.]",
    ])
    assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [
        (1, "ambiguous-speaker-turn")
    ]
    assert [block.witness for block in result.statements] == ["Bea Two"]

@pytest.mark.parametrize("continuation", (
    "the Widget Committee. The synthetic plan continues.",
    "the XYZ. The synthetic acronym remains in the argument.",
    "The Widget Committee. A hard-wrapped name continues.",
))
def test_hard_wrapped_prose_is_not_structural_speaker(continuation):
    result = _split_synthetic_html([
        "Prepared Statement of Ada One",
        "The written synthetic paragraph refers to",
        continuation,
        "Prepared Statement of Bea Two", "Independent body.",
        "[Questions and answers follow.]",
    ])
    assert result.issues == []
    assert len(result.statements) == 2
    assert continuation in result.statements[0].body
    assert result.statements[0].boundary_kind == "next-prepared-heading"
    assert result.statements[1].witness == "Bea Two"


def test_standalone_quote_cue_closes_and_unclosed_cue_refuses():
    closed = _split_synthetic_html([
        "Prepared Statement of Ada One", "Written block.",
        "“", "An ordinary quoted sentence.", "”",
        "Senator Vale. Oral turn after the quotation.",
        "Prepared Statement of Bea Two", "Independent body.",
        "[Questions and answers follow.]",
    ])
    assert closed.issues == []
    assert closed.statements[0].boundary_kind == "oral-speaker-turn"
    assert "An ordinary quoted sentence." in closed.statements[0].body
    assert "Oral turn after" not in closed.statements[0].body
    assert closed.statements[1].witness == "Bea Two"

    unclosed = _split_synthetic_html([
        "Prepared Statement of Ada One", "Written block.", "“",
        "A quotation without a visible close.",
        "Prepared Statement of Bea Two", "Independent body.",
        "[Questions and answers follow.]",
    ])
    assert [(issue.heading_ordinal, issue.reason) for issue in unclosed.issues] == [
        (1, "ambiguous-open-quotation")
    ]
    assert [block.witness for block in unclosed.statements] == ["Bea Two"]

def test_compact_content_labels_and_inline_salutation_remain_written():
    result = _split_synthetic_html([
        "Prepared Statement of Ada One",
        "Mr. Chairman, I cite Senator Van Hollen. in this same line.",
        "[Exhibit A-1]", "[Table 2-A]", "[42 U.S.C. § 123(a)]",
        "[Questions and answers follow.]",
    ])
    assert result.issues == []
    assert len(result.statements) == 1
    body = result.statements[0].body
    for retained in ("Mr. Chairman,", "Senator Van Hollen.", "[Exhibit A-1]",
                     "[Table 2-A]", "[42 U.S.C. § 123(a)]"):
        assert retained in body
    assert result.statements[0].boundary_kind == "procedural-bracket"

def test_overlong_candidate_refused_with_and_without_close():
    oversized = "X" * (gi.MAX_STATEMENT_CHARS + 1)
    for suffix in ("", "\n[Questions and answers follow.]"):
        result = gi._split_prepared_statements(
            "Prepared Statement of Ada One\n" + oversized + suffix
        )
        assert not result.statements
        assert result.issues[0].reason == "overlong-statement"


def test_iter_pages_follows_nextpage():
    """_iter_pages follows nextPage, re-applying the api_key, and yields
    items across pages."""
    start = "https://api.govinfo.gov/published/A/B?api_key=TESTKEY"
    next_raw = "https://api.govinfo.gov/published/A/B?offsetMark=NEXT&collection=CHRG"
    next_keyed = gi._add_query(next_raw, api_key=KEY)
    p1 = ac.FetchResult(
        url=start, status=200, final_url=start,
        text=json.dumps({"packages": [{"packageId": "P1"}], "nextPage": next_raw}),
    )
    p2 = ac.FetchResult(
        url=next_keyed, status=200, final_url=next_keyed,
        text=json.dumps({"packages": [{"packageId": "P2"}], "nextPage": None}),
    )
    fetcher = ac.FixtureFetcher(
        url_map={start: p1, next_keyed: p2},
        rate_limit_seconds=0.0, respect_robots=False,
    )
    items = list(gi._iter_pages(start, fetcher, KEY, "packages"))
    assert [i["packageId"] for i in items] == ["P1", "P2"]


# ------------------- Discovery -----------------------------------


def test_discover_splits_statements():
    """Discovery fetches each hearing HTM and yields one item per prepared
    statement; oral Q&A and member statements are not captured."""
    options = gi.parse_options(make_args())
    items = list(gi.discover_items(options, make_fetcher()))
    # Smith + Short from pkg1, Jones from pkg2.
    assert {it.author for it in items} == {"Jane Smith", "Bob Short", "Robert Jones"}
    assert len(items) == 3
    smith = [it for it in items if it.author == "Jane Smith"][0]
    assert smith.package_id == PKG1
    assert smith.date == dt.date(2019, 5, 10)
    assert smith.title == "Prepared statement of Jane Smith"
    assert "widget" in smith.body_text.lower()
    # Stored locator is credential-free.
    assert smith.locator == gi._granule_content_url(PKG1, PKG1)
    assert "api_key" not in smith.locator
    # The member opening in pkg2 (no heading) is not captured.
    assert not any("Member" in it.author for it in items)


def test_extract_one_returns_discovered_body():
    """extract_one returns the body parsed during discovery (no per-item
    fetch) and resolves the witness as author."""
    options = gi.parse_options(make_args())
    item = gi.ItemMeta(
        locator=gi._granule_content_url(PKG1, PKG1),
        title="Prepared statement of Jane Smith",
        date=dt.date(2019, 5, 10),
        author="Jane Smith",
        body_text="Chairman, thank you for the chance to testify on widgets.",
    )
    body, title, author, date = gi.extract_one(item, options, make_fetcher())
    assert body == item.body_text
    assert title == item.title
    assert author == "Jane Smith"
    # An empty statement is skipped.
    assert gi.extract_one(gi.ItemMeta(locator="x"), options, make_fetcher())[0] == ""


# ------------------- End-to-end ----------------------------------


def test_end_to_end(tmp_path):
    """Smith + Jones acquired; Short dropped (below floor); oral Q&A + member
    statement not captured. Manifest carries register testimony_policy and the
    parsed witness as author."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "impostors" / \
        "testimony_policy" / "chrg"
    manifest_path = output_dir / "draft_manifest.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    rc = gi.run(args, fetcher=make_fetcher())
    assert rc == 0

    txt_files = sorted(output_dir.glob("*.txt"))
    assert len(txt_files) == 2, \
        f"Expected 2 acquired statements, got {[f.name for f in txt_files]}"
    for txt in txt_files:
        assert ac.html_text_is_clean(txt.read_text(encoding="utf-8"))

    entries = read_manifest(manifest_path)
    assert len(entries) == 2
    authors = {e["author"] for e in entries}
    assert authors == {"Jane Smith", "Robert Jones"}
    for e in entries:
        assert e["corpus_role"] == "impostor"
        assert e["register"] == "testimony_policy"
        assert e["consent_status"] == "public_record"
        assert e["era"] == "pre_chatgpt"
        assert e["impostor_for"] == ["argscope_testimony_policy"]
        assert e["acquired_via"].startswith("acquire_govinfo_chrg_")
        assert e["content_hash"].startswith("sha256:")
        assert e["persona"] == "chrg"
        assert e["language_status"] == "unknown"
    assert len({e["content_hash"] for e in entries}) == 2
    for meta_file in output_dir.glob("*.meta.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert len(meta["source_text_sha256"]) == 64
        assert meta["boundary_kind"] == "procedural-bracket"
        assert meta["heading_start"] < meta["heading_end"] < meta["body_start"] < meta["body_end"]
        assert meta["role_review_status"] == "pending"
        assert meta["rights_review_status"] == "pending"
        assert meta["extraction_completeness_status"] == "pending_source_review"
        fixture = "hearing_pkg1.htm" if PKG1 in meta["source_url"] else "hearing_pkg2.htm"
        decoded, _ = ac.html_to_text(
            (FIXTURE_DIR / fixture).read_text(encoding="utf-8"),
            strip_selectors=gi.DEFAULT_STRIP_SELECTORS,
        )
        assert meta["source_text_sha256"] == hashlib.sha256(
            decoded.encode("utf-8")
        ).hexdigest()
        assert decoded[meta["body_start"]:meta["body_end"]].strip()


def test_run_logs_boundary_refusal_and_keeps_later_heading(tmp_path):
    written = "The written synthetic widget analysis has a concrete conclusion. " * 8
    hearing = "\n".join([
        "<html><body><pre>Prepared Statement of Ada One",
        written,
        "The STAFF DIRECTOR. Spoken remarks begin.",
        "Prepared Statement of Bea Two",
        written,
        "[Questions and answers follow.]</pre></body></html>",
    ])
    url = gi._add_query(gi._granule_content_url(PKG1, PKG1), api_key=KEY)
    mapping = fixture_url_map()
    mapping[url] = ac.FetchResult(url=url, status=200, text=hearing)
    output_dir = tmp_path / "ai-prose-baselines-private" / "ambiguous"
    manifest = output_dir / "draft.jsonl"
    report_path = output_dir / "summary.json"
    args = make_args(
        output_dir=str(output_dir), emit_manifest=str(manifest),
        out=str(report_path), min_words=1,
    )
    assert gi.run(args, fetcher=make_fetcher(mapping)) == 0
    entries = read_manifest(manifest)
    assert "Ada One" not in {entry["author"] for entry in entries}
    assert "Bea Two" in {entry["author"] for entry in entries}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    skips = [s for s in report["skip_log"]
             if s["reason"] == "ambiguous-speaker-turn"]
    assert len(skips) == 1
    assert skips[0]["url"] == gi._granule_content_url(PKG1, PKG1)
    assert "api_key" not in skips[0]["url"]
    assert report["skipped_parse_error"] >= 1


def test_all_unbounded_candidates_fail_zero_output_with_skip_log(tmp_path):
    hearing = (
        "<html><body><pre>Prepared Statement of Ada One\n"
        "A synthetic written argument that has no verified ending.\n"
        "</pre></body></html>"
    )
    mapping = fixture_url_map()
    for package in (PKG1, PKG2):
        url = gi._add_query(
            gi._granule_content_url(package, package), api_key=KEY
        )
        mapping[url] = ac.FetchResult(url=url, status=200, text=hearing)
    output_dir = tmp_path / "ai-prose-baselines-private" / "unbounded"
    report_path = output_dir / "summary.json"
    args = make_args(output_dir=str(output_dir), out=str(report_path),
                     min_words=1)
    assert gi.run(args, fetcher=make_fetcher(mapping)) == 1
    assert not list(output_dir.glob("*.txt"))
    assert not list(output_dir.glob("*.meta.json"))
    assert not (output_dir / "draft_manifest.jsonl").exists()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [item["reason"] for item in report["skip_log"]] == [
        "unbounded-eof", "unbounded-eof"
    ]


def test_min_words_gate_high_drops_all(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "hi"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        min_words=100000,
    )
    gi.run(args, fetcher=make_fetcher())
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


def test_short_statement_dropped(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "sh"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    gi.run(args, fetcher=make_fetcher())
    # Bob Short's prepared statement is below the floor → no entry.
    entries = read_manifest(manifest_path)
    assert not any(e["author"] == "Bob Short" for e in entries)


def test_author_override(tmp_path):
    """--author overrides the parsed witness name."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "ov"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(
        output_dir=str(output_dir), emit_manifest=str(manifest_path),
        author="Congressional Testimony Pool",
    )
    gi.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    assert entries and all(
        e["author"] == "Congressional Testimony Pool" for e in entries)


def test_dedupe_within_output_dir(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dd"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    gi.run(args, fetcher=make_fetcher())
    first = len(list(output_dir.glob("*.txt")))
    assert first == 2
    gi.run(args, fetcher=make_fetcher())
    assert len(list(output_dir.glob("*.txt"))) == first
    assert len(read_manifest(manifest_path)) == 2


def test_dry_run_writes_nothing(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dry"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        dry_run=True,
    )
    rc = gi.run(args, fetcher=make_fetcher())
    assert rc == 0
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


# ------------------- api_key threading ---------------------------


def test_api_key_resolution_default_demo(monkeypatch):
    monkeypatch.delenv("GOVINFO_API_KEY", raising=False)
    opts = gi.parse_options(make_args(api_key=None))
    assert opts.api_key == gi.DEMO_KEY


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("GOVINFO_API_KEY", "ENVKEY")
    opts = gi.parse_options(make_args(api_key=None))
    assert opts.api_key == "ENVKEY"


def test_api_key_in_request_urls():
    """Discovery URLs embed the api_key (published, granules, and the hearing
    HTM fetch)."""
    options = gi.parse_options(make_args(api_key="SECRET"))
    fetcher = make_fetcher({})  # empty map → all 404, but record fetched URLs
    list(gi.discover_items(options, fetcher))
    assert fetcher.fetched_urls, "discovery should have requested at least one URL"
    assert all("api_key=SECRET" in u for u in fetcher.fetched_urls)


def test_manifest_source_has_no_api_key(tmp_path):
    """The api_key is added only at the fetch boundary — it must never land
    in the stored manifest ``source`` or the meta sidecar ``source_url``."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "nokey"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    gi.run(args, fetcher=make_fetcher())
    entries = read_manifest(manifest_path)
    assert entries
    for e in entries:
        assert "api_key" not in e.get("source", "")
        assert KEY not in e.get("source", "")
    for meta_file in output_dir.glob("*.meta.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert "api_key" not in meta.get("source_url", "")
        assert KEY not in meta.get("source_url", "")


# ------------------- Privacy guard + argparse --------------------


def test_privacy_guard_refuses_non_private(tmp_path):
    public_dir = tmp_path / "public_oops"
    args = make_args(
        output_dir=str(public_dir),
        emit_manifest=str(public_dir / "draft.jsonl"),
        allow_public_output=False,
    )
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            gi.run(args, fetcher=make_fetcher())
        assert exc.value.code == 2
    else:
        try:
            gi.run(args, fetcher=make_fetcher())
            assert False, "expected SystemExit(2)"
        except SystemExit as e:
            assert e.code == 2


def test_argparse_rejects_missing_required():
    parser = gi.build_arg_parser()
    for argv in (
        ["--register", "testimony_policy", "--consent-status", "public_record"],
        ["--impostor-for", "x", "--consent-status", "public_record"],
        ["--impostor-for", "x", "--register", "testimony_policy"],
    ):
        if pytest is not None:
            with pytest.raises(SystemExit):
                parser.parse_args(argv)
        else:
            try:
                parser.parse_args(argv)
                assert False, f"should reject {argv}"
            except SystemExit:
                pass


def test_cli_help_lists_flags():
    parser = gi.build_arg_parser()
    help_text = parser.format_help()
    for flag in (
        "--api-key", "--collection", "--persona", "--impostor-for",
        "--register", "--consent-status", "--era", "--since", "--until",
        "--max-items", "--min-words", "--dry-run", "--allow-public-output",
    ):
        assert flag in help_text, f"--help missing {flag}"


# ------------------- Manifest-validator integration --------------


def test_emitted_manifest_validates(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "vt"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    gi.run(args, fetcher=make_fetcher())

    baseline_text = output_dir / "fake_baseline.txt"
    baseline_text.write_text("Baseline prose. " * 100, encoding="utf-8")
    baseline_entry = {
        "id": "fake_baseline",
        "path": "fake_baseline.txt",
        "author": "Operator",
        "persona": "argscope_testimony_policy",
        "register": "testimony_policy",
        "ai_status": "pre_ai_human",
        "language_status": "native",
        "use": ["baseline", "voice_profile"],
        "split": "baseline",
        "privacy": "private",
        "corpus_role": "identity_baseline",
        "era": "pre_chatgpt",
    }
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(baseline_entry, sort_keys=True) + "\n")

    report = mv.validate_manifest(manifest_path)
    errors = [i for i in report["issues"] if i.get("severity") == "error"]
    assert errors == [], f"Manifest should validate without errors: {errors}"


def test_zero_output_exit_code(tmp_path):
    """A zero-output run that isn't a dedupe-only rerun fails (rc=1) unless
    --allow-empty; a dedupe-only rerun exits 0."""
    base = tmp_path / "ai-prose-baselines-private"
    # Everything below the floor -> nothing acquired, no dupes -> failure.
    ze = dict(output_dir=str(base / "ze"),
              emit_manifest=str(base / "ze" / "d.jsonl"), min_words=100000)
    assert gi.run(make_args(**ze), fetcher=make_fetcher()) == 1
    assert gi.run(make_args(allow_empty=True, **ze), fetcher=make_fetcher()) == 0
    # Dedupe-only rerun is a valid empty result -> 0.
    od = dict(output_dir=str(base / "do"),
              emit_manifest=str(base / "do" / "d.jsonl"), min_words=150)
    assert gi.run(make_args(**od), fetcher=make_fetcher()) == 0   # first acquires
    assert gi.run(make_args(**od), fetcher=make_fetcher()) == 0   # rerun: all dupe


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
