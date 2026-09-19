#!/usr/bin/env python3
"""Regression tests for acquire_mirrulations.py.

Uses an in-memory ``FixtureObjectStore`` (no network, no boto3) seeded with
S3-style keys: two substantive extracted-text ``.txt`` objects, one short one
(min-words drop), one non-text ``.json`` key (filtered by the text-key
pattern), and one object outside the target prefix.

Invariants: prefix listing; the extracted-text key-pattern filter; the
get->decode->pipeline join; the min-words gate; the impostor schema with
register regulatory_comment; exact-hash dedup; the privacy guard; argparse
(``--prefix`` required); a clean error when boto3 is absent; and a
manifest-validator integration. No third-party deps.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import types
from pathlib import Path
from conftest import read_manifest  # noqa: E402

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

_ok = True
_reason = ""
try:
    import acquisition_core as ac  # type: ignore  # noqa: F401
    import acquire_mirrulations as mr  # type: ignore
    import manifest_validator as mv  # type: ignore
except ImportError as _e:  # pragma: no cover
    _ok = False
    _reason = str(_e)

if pytest is not None and not _ok:
    pytestmark = pytest.mark.skip(reason=_reason)


PREFIX = "derived-data/EPA/EPA-HQ-OAR-2013-0602"

_C1 = (
    "On behalf of the Association, we submit these comments on the proposed "
    "rule. We support the agency's objective but object to the methodology in "
    "Section III, which rests on an emissions baseline that the agency's own "
    "data contradict. The proposed baseline assumes a fleet composition that "
    "had already shifted by the time the analysis was run, and the error "
    "propagates through every downstream estimate. We urge the agency to "
    "recompute the baseline using the current inventory and to publish the "
    "revised figures for comment before finalizing. Beyond the baseline, the "
    "cost-benefit analysis double-counts a category of compliance savings, "
    "treating the same equipment upgrade as both a capital cost avoided and an "
    "operating cost reduced. Correcting that double-count materially changes "
    "the net-benefit conclusion. We also note that the proposed compliance "
    "schedule does not account for the lead time small facilities require to "
    "procure and install the controls, and we recommend a tiered schedule "
    "scaled to facility size. None of these objections is to the rule's "
    "purpose, which we share; each goes to the soundness of the analysis on "
    "which the rule rests, and each is correctable on the existing record."
)
_C2 = (
    "These comments are submitted by the Center in response to the notice of "
    "proposed rulemaking. Our central concern is that the agency has "
    "understated the rule's benefits by excluding a class of effects the "
    "governing statute requires it to consider. The agency's analysis counts "
    "only the directly regulated harms and omits the well-documented "
    "downstream effects, even though the statute directs it to weigh the rule's "
    "full consequences. The omission is not harmless: including the downstream "
    "effects, using the agency's own published valuation, more than doubles the "
    "estimated benefit and reverses the conclusion of the cost-benefit test. We "
    "document the relevant studies in the attached appendix and show that each "
    "meets the agency's stated criteria for inclusion. We further object to the "
    "agency's treatment of uncertainty, which reports a single point estimate "
    "where the record supports a range, and we ask the agency to present the "
    "range and to explain its choice of central value. We support a strong "
    "final rule and offer these comments to ensure it rests on a complete and "
    "defensible analysis that will withstand review."
)
_C3 = (
    "I support this rule. Please finalize it quickly. It will protect public "
    "health and the environment. Thank you for considering my comment."
)

OBJECTS = {
    f"{PREFIX}/derived/comments_extracted_text/c1_extracted.txt": _C1.encode(),
    f"{PREFIX}/derived/comments_extracted_text/c2_extracted.txt": _C2.encode(),
    f"{PREFIX}/derived/comments_extracted_text/c3_extracted.txt": _C3.encode(),
    f"{PREFIX}/text-1/comments/c1.json": b'{"data": {"id": "c1"}}',
    "derived-data/OTHER/OTHER-DOCKET/derived/comments_extracted_text/x_extracted.txt": b"out of scope",
}


STANDARD_DOCKET = "EPA-TEST-2020-0001"
STANDARD_COMMENT = STANDARD_DOCKET + "-0007"
STANDARD_PREFIX = f"derived-data/EPA/{STANDARD_DOCKET}"
STANDARD_TEXT_KEY = (
    f"{STANDARD_PREFIX}/mirrulations/extracted_txt/"
    f"comments_extracted_text/pdftotext/"
    f"{STANDARD_COMMENT}_attachment_1_extracted.txt"
)
STANDARD_METADATA_KEY = (
    f"raw-data/EPA/{STANDARD_DOCKET}/text-{STANDARD_DOCKET}/"
    f"comments/{STANDARD_COMMENT}.json"
)
STANDARD_URL = (
    f"https://downloads.regulations.gov/{STANDARD_COMMENT}/attachment_1.pdf"
)


def metadata_payload(
    *, comment: str = STANDARD_COMMENT, docket: str = STANDARD_DOCKET,
    url: str = STANDARD_URL,
) -> dict:
    return {
        "data": {
            "id": comment,
            "type": "comments",
            "attributes": {
                "docketId": docket,
                "receiveDate": "2014-06-02T12:30:00Z",
                "postedDate": "2014-06-20T09:00:00-04:00",
                "postmarkDate": None,
                "modifyDate": "2015-01-21 14:15:00+00:00",
            },
            "relationships": {
                "attachments": {
                    "data": [{"type": "attachments", "id": "attachment-1"}],
                },
            },
        },
        "included": [{
            "type": "attachments",
            "id": "attachment-1",
            "attributes": {
                "modifyDate": "2014-06-21T08:00:00Z",
                "fileFormats": [{"format": "pdf", "fileUrl": url}],
            },
        }],
    }


def standard_store(
    payload: dict | bytes | None = None, *, text: bytes | None = None,
    outcome: "mr.MetadataRead | None" = None,
) -> "mr.FixtureObjectStore":
    if payload is None:
        payload = metadata_payload()
    metadata_bytes = (
        payload if isinstance(payload, bytes)
        else json.dumps(payload, sort_keys=True).encode("utf-8")
    )
    objects = {
        STANDARD_TEXT_KEY: _C1.encode("utf-8") if text is None else text,
        STANDARD_METADATA_KEY: metadata_bytes,
    }
    overrides = {STANDARD_METADATA_KEY: outcome} if outcome else None
    return mr.FixtureObjectStore(objects, metadata_outcomes=overrides)


def make_args(**overrides) -> argparse.Namespace:
    base = dict(
        prefixes=[PREFIX],
        bucket="mirrulations",
        region="us-east-1",
        metadata_mode="off",
        text_key_pattern=mr.DEFAULT_TEXT_KEY_PATTERN,
        persona="mirrulations",
        author="",
        impostor_for=["argscope_regulatory_comment"],
        register="regulatory_comment",
        register_match="high",
        topic_match="medium",
        consent_status="public_record",
        era="pre_chatgpt",
        max_items=500,
        min_words=150,
        output_dir=None,
        emit_manifest=None,
        out=None,
        dry_run=False,
        allow_empty=False,
        allow_public_output=True,
        allow_non_prose=False,
        strip_rules=None,
        strip_aggressive=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def make_store() -> "mr.FixtureObjectStore":
    return mr.FixtureObjectStore(OBJECTS)


# ------------------- ObjectStore ---------------------------------


def test_fixture_store_prefix_and_get():
    store = make_store()
    keys = list(store.list_keys(PREFIX))
    assert all(k.startswith(PREFIX) for k in keys)
    assert not any(k.startswith("OTHER/") for k in keys)
    data = store.get_bytes(f"{PREFIX}/derived/comments_extracted_text/c1_extracted.txt")
    assert data and b"Association" in data
    assert store.get_bytes("nope") is None


def test_make_s3_store_requires_boto3():
    """Either boto3 is installed (returns an ObjectStore) or absent (clean
    RuntimeError) — never an opaque ImportError."""
    try:
        store = mr.make_s3_store("mirrulations")
    except RuntimeError as e:
        assert "boto3" in str(e)
        return
    assert isinstance(store, mr.ObjectStore)


def test_title_from_key():
    assert mr._title_from_key(
        f"{PREFIX}/derived/comments_extracted_text/c1_extracted.txt"
    ) == "c1_extracted"


# ------------------- Discovery + extraction ----------------------


def test_discover_filters_to_text_keys():
    options = mr.parse_options(make_args())
    items = list(mr.discover_items(options, make_store()))
    keys = {it.locator for it in items}
    # c1/c2/c3 extracted-text .txt match; the .json does not; OTHER/ is out of
    # prefix.
    assert keys == {
        f"{PREFIX}/derived/comments_extracted_text/c1_extracted.txt",
        f"{PREFIX}/derived/comments_extracted_text/c2_extracted.txt",
        f"{PREFIX}/derived/comments_extracted_text/c3_extracted.txt",
    }


def test_extract_one_decodes():
    options = mr.parse_options(make_args())
    item = mr.ItemMeta(
        locator=f"{PREFIX}/derived/comments_extracted_text/c1_extracted.txt",
        title="c1_extracted",
    )
    body, title, author, date = mr.extract_one(item, options, make_store())
    assert "Association" in body
    assert author == mr.DEFAULT_AUTHOR


# ------------------- End-to-end ----------------------------------


def test_end_to_end(tmp_path):
    """c1 + c2 acquired; c3 dropped (short); the .json + out-of-prefix filtered."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "impostors" / \
        "regulatory_comment" / "mirrulations"
    manifest_path = output_dir / "draft_manifest.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    rc = mr.run(args, store=make_store())
    assert rc == 0

    txt_files = sorted(output_dir.glob("*.txt"))
    assert len(txt_files) == 2, \
        f"Expected 2 acquired comments, got {[f.name for f in txt_files]}"

    entries = read_manifest(manifest_path)
    assert len(entries) == 2
    for e in entries:
        assert e["corpus_role"] == "impostor"
        assert e["register"] == "regulatory_comment"
        assert e["consent_status"] == "public_record"
        assert e["impostor_for"] == ["argscope_regulatory_comment"]
        assert e["acquired_via"].startswith("acquire_mirrulations_")
        assert e["persona"] == "mirrulations"
        assert e["source"].startswith(PREFIX)
    assert len({e["content_hash"] for e in entries}) == 2


def test_min_words_gate_high_drops_all(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "hi"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        min_words=100000,
    )
    mr.run(args, store=make_store())
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


def test_short_comment_dropped(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "sh"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    mr.run(args, store=make_store())
    entries = read_manifest(manifest_path)
    assert not any("c3_extracted" in (e.get("source") or "") for e in entries)


def test_exact_dedupe(tmp_path):
    """A duplicate object (identical form-letter text) is dropped by content
    hash within the output dir."""
    output_dir = tmp_path / "ai-prose-baselines-private" / "dd"
    manifest_path = output_dir / "draft.jsonl"
    # Two keys with IDENTICAL text (a form letter submitted twice).
    objs = {
        f"{PREFIX}/derived/comments_extracted_text/a_extracted.txt": _C1.encode(),
        f"{PREFIX}/derived/comments_extracted_text/b_extracted.txt": _C1.encode(),
    }
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    mr.run(args, store=mr.FixtureObjectStore(objs))
    # Only one of the identical pair is written.
    assert len(list(output_dir.glob("*.txt"))) == 1
    assert len(read_manifest(manifest_path)) == 1


def test_dry_run_writes_nothing(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "dry"
    args = make_args(
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
        dry_run=True,
    )
    rc = mr.run(args, store=make_store())
    assert rc == 0
    assert not output_dir.exists() or not list(output_dir.glob("*.txt"))


# ------------------- Privacy + argparse + validator --------------


def test_privacy_guard_refuses_non_private(tmp_path):
    public_dir = tmp_path / "public_oops"
    args = make_args(
        output_dir=str(public_dir),
        emit_manifest=str(public_dir / "draft.jsonl"),
        allow_public_output=False,
    )
    if pytest is not None:
        with pytest.raises(SystemExit) as exc:
            mr.run(args, store=make_store())
        assert exc.value.code == 2
    else:
        try:
            mr.run(args, store=make_store())
            assert False
        except SystemExit as e:
            assert e.code == 2


def test_argparse_requires_prefix_and_required_flags():
    parser = mr.build_arg_parser()
    for argv in (
        # missing --prefix
        ["--impostor-for", "x", "--register", "regulatory_comment",
         "--consent-status", "public_record"],
        # missing --register
        ["--prefix", PREFIX, "--impostor-for", "x",
         "--consent-status", "public_record"],
        # missing --impostor-for
        ["--prefix", PREFIX, "--register", "regulatory_comment",
         "--consent-status", "public_record"],
    ):
        if pytest is not None:
            with pytest.raises(SystemExit):
                parser.parse_args(argv)
        else:
            try:
                parser.parse_args(argv)
                assert False
            except SystemExit:
                pass


def test_argparse_accepts_repeated_prefix():
    parser = mr.build_arg_parser()
    args = parser.parse_args([
        "--prefix", "A/1", "--prefix", "B/2",
        "--impostor-for", "x", "--register", "regulatory_comment",
        "--consent-status", "public_record",
    ])
    assert args.prefixes == ["A/1", "B/2"]


def test_cli_help_lists_flags():
    help_text = mr.build_arg_parser().format_help()
    for flag in (
        "--prefix", "--bucket", "--text-key-pattern", "--persona",
        "--impostor-for", "--register", "--consent-status", "--min-words",
        "--dry-run", "--allow-public-output", "--metadata-mode",
        "--language-status",
    ):
        assert flag in help_text, f"--help missing {flag}"


def test_emitted_manifest_validates_with_regulatory_comment(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "vt"
    manifest_path = output_dir / "draft.jsonl"
    args = make_args(output_dir=str(output_dir), emit_manifest=str(manifest_path))
    mr.run(args, store=make_store())

    baseline_text = output_dir / "fake_baseline.txt"
    baseline_text.write_text("Baseline prose. " * 100, encoding="utf-8")
    baseline_entry = {
        "id": "fake_baseline", "path": "fake_baseline.txt",
        "author": "Operator", "persona": "argscope_regulatory_comment",
        "register": "regulatory_comment", "ai_status": "pre_ai_human",
        "language_status": "native", "use": ["baseline", "voice_profile"],
        "split": "baseline", "privacy": "private",
        "corpus_role": "identity_baseline", "era": "pre_chatgpt",
    }
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(baseline_entry, sort_keys=True) + "\n")

    report = mv.validate_manifest(manifest_path)
    errors = [i for i in report["issues"] if i.get("severity") == "error"]
    assert errors == [], f"Manifest should validate without errors: {errors}"
    unknown_register = [
        i for i in report["issues"]
        if "register" in i.get("message", "").lower()
        and "regulatory_comment" in i.get("message", "")
    ]
    assert unknown_register == [], \
        f"regulatory_comment should be a known register: {unknown_register}"


def test_zero_output_exit_code(tmp_path):
    """A zero-output run that isn't a dedupe-only rerun fails (rc=1) unless
    --allow-empty; a dedupe-only rerun exits 0."""
    base = tmp_path / "ai-prose-baselines-private"
    # Everything below the floor -> nothing acquired, no dupes -> failure.
    ze = dict(output_dir=str(base / "ze"),
              emit_manifest=str(base / "ze" / "d.jsonl"), min_words=100000)
    assert mr.run(make_args(**ze), store=make_store()) == 1
    assert mr.run(make_args(allow_empty=True, **ze), store=make_store()) == 0
    # Dedupe-only rerun is a valid empty result -> 0.
    od = dict(output_dir=str(base / "do"),
              emit_manifest=str(base / "do" / "d.jsonl"), min_words=150)
    assert mr.run(make_args(**od), store=make_store()) == 0   # first acquires
    assert mr.run(make_args(**od), store=make_store()) == 0   # rerun: all dupe


# ------------------- Opt-in metadata custody ----------------------


def test_standard_mode_emits_bound_source_dates_without_authored_date(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "standard"
    manifest_path = output_dir / "draft.jsonl"
    store = standard_store()
    args = make_args(
        prefixes=[STANDARD_PREFIX], metadata_mode="standard",
        output_dir=str(output_dir), emit_manifest=str(manifest_path),
    )
    assert mr.run(args, store=store) == 0
    assert store.metadata_got_keys == [STANDARD_METADATA_KEY]
    entry = read_manifest(manifest_path)[0]
    sidecar = json.loads(next(output_dir.glob("*.meta.json")).read_bytes())
    assert sidecar["scraper_version"] == "1.2"
    receipt = sidecar["source_metadata"]
    assert "date_written" not in entry
    assert sidecar["date_written"] is None
    assert receipt["schema"] == "setec.mirrulations_source_metadata.v1"
    assert receipt["status"] == "binding_verified"
    assert receipt["text_object_key"] == entry["source"] == STANDARD_TEXT_KEY
    assert receipt["text_object_sha256"] == hashlib.sha256(
        store.objects[STANDARD_TEXT_KEY]
    ).hexdigest()
    assert receipt["text_object_bytes"] == len(store.objects[STANDARD_TEXT_KEY])
    assert receipt["metadata_object_key"] == STANDARD_METADATA_KEY
    assert receipt["metadata_object_sha256"] == hashlib.sha256(
        store.objects[STANDARD_METADATA_KEY]
    ).hexdigest()
    assert receipt["metadata_object_bytes"] == len(store.objects[STANDARD_METADATA_KEY])
    assert receipt["attachment_pdf_file_url"] == STANDARD_URL
    assert receipt["comment_id"] == STANDARD_COMMENT
    assert receipt["attachment_number"] == 1
    assert receipt["reported_dates"]["received"] == {
        "status": "valid", "value": "2014-06-02T12:30:00Z",
    }
    assert receipt["reported_dates"]["posted"]["value"] == (
        "2014-06-20T09:00:00-04:00"
    )
    assert receipt["reported_dates"]["postmark"] == {
        "status": "missing", "value": None,
    }
    assert receipt["reported_dates"]["comment_modified"]["status"] == "valid"
    assert receipt["reported_dates"]["attachment_modified"]["status"] == "valid"
    assert receipt["authored_date_proven"] is False
    assert receipt["historical_text_bytes_proven"] is False
    assert "source_metadata" not in entry
    assert sidecar["content_hash"] == entry["content_hash"]
    assert sidecar["content_hash"] == ac.compute_content_hash(
        next(output_dir.glob("*.txt")).read_bytes().decode("utf-8")
    )


def test_off_mode_keeps_custom_key_and_bucket_without_metadata_reads(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "off"
    store = make_store()
    args = make_args(
        bucket="operator-custom", metadata_mode="off",
        output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
    )
    assert mr.run(args, store=store) == 0
    assert store.metadata_got_keys == []
    assert len(read_manifest(output_dir / "draft.jsonl")) == 2
    assert all(
        "source_metadata" not in json.loads(path.read_bytes())
        for path in output_dir.glob("*.meta.json")
    )
    assert all(
        "date_written" not in row
        for row in read_manifest(output_dir / "draft.jsonl")
    )


def test_standard_custom_bucket_and_invalid_mode_reject_before_fetch(tmp_path):
    store = standard_store()
    output_dir = tmp_path / "ai-prose-baselines-private" / "reject"
    args = make_args(
        metadata_mode="standard", bucket="operator-custom",
        prefixes=[STANDARD_PREFIX], output_dir=str(output_dir),
    )
    with pytest.raises(ValueError, match="mirrulations bucket"):
        mr.run(args, store=store)
    assert store.listed_prefixes == store.got_keys == store.metadata_got_keys == []
    parser = mr.build_arg_parser()
    required = [
        "--prefix", STANDARD_PREFIX, "--impostor-for", "x",
        "--register", "regulatory_comment", "--consent-status", "public_record",
    ]
    assert parser.parse_args(required).metadata_mode == "off"
    with pytest.raises(SystemExit):
        parser.parse_args(required + ["--metadata-mode", "guess"])


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("receiveDate", "2014-06-02", "invalid"),
        ("receiveDate", "2014-06-02T12:30:00", "invalid"),
        ("receiveDate", 20140602, "invalid"),
        ("receiveDate", "2014-02-30T12:30:00Z", "invalid"),
        ("receiveDate", None, "missing"),
        ("receiveDate", "2014-06-02T12:30:00+0000", "valid"),
        ("receiveDate", "2014-06-02T12:30:00+00", "valid"),
        ("receiveDate", "2014-06-02T12:30:00+00:00:00", "valid"),
        ("receiveDate", "2014-06-02T12:30:00Z arbitrary prose", "invalid"),
        ("postedDate", "2014-06-20T09:00:00+02:00", "valid"),
    ],
)
def test_date_fields_are_independent_and_never_infer_authored_date(
    tmp_path, field, value, expected,
):
    payload = metadata_payload()
    payload["data"]["attributes"][field] = value
    output_dir = tmp_path / "ai-prose-baselines-private" / "dates"
    args = make_args(
        prefixes=[STANDARD_PREFIX], metadata_mode="standard",
        output_dir=str(output_dir), emit_manifest=str(output_dir / "draft.jsonl"),
    )
    assert mr.run(args, store=standard_store(payload)) == 0
    receipt = json.loads(next(output_dir.glob("*.meta.json")).read_bytes())[
        "source_metadata"
    ]
    date_name = {"receiveDate": "received", "postedDate": "posted"}[field]
    assert receipt["reported_dates"][date_name]["status"] == expected
    assert receipt["reported_dates"][date_name]["value"] == (
        value if expected == "valid" else None
    )
    assert "date_written" not in read_manifest(output_dir / "draft.jsonl")[0]


def test_reused_item_clears_receipt_on_failed_text_or_metadata_fetch(tmp_path):
    options, item, _summary, _store, _body, _title, _author, _date = (
        _prepared_standard_item(tmp_path)
    )
    assert item.metadata_receipt is not None
    assert mr.extract_one(item, options, mr.FixtureObjectStore({})) == (
        "", "", "", None,
    )
    assert item.metadata_receipt is None
    mr.extract_one(item, options, standard_store())
    assert item.metadata_receipt is not None
    store = standard_store(outcome=mr.MetadataRead("missing"))
    with pytest.raises(mr.MetadataSkip) as exc:
        mr.extract_one(item, options, store)
    assert exc.value.reason == "metadata-missing"
    assert item.metadata_receipt is None


def test_raw_and_replacement_decoded_hash_domains_are_distinct(tmp_path):
    raw = _C1.encode("utf-8") + bytes([255])
    store = standard_store(text=raw)
    options = mr.parse_options(make_args(
        prefixes=[STANDARD_PREFIX], metadata_mode="standard",
        output_dir=str(tmp_path / "ai-prose-baselines-private" / "raw"),
    ))
    item = mr.ItemMeta(locator=STANDARD_TEXT_KEY)
    body, _title, _author, date = mr.extract_one(item, options, store)
    assert date is None and body.endswith("\ufffd")
    receipt = item.metadata_receipt
    assert receipt is not None and receipt.stage == "fetched"
    assert receipt.raw_text_sha256 == hashlib.sha256(raw).hexdigest()
    assert receipt.raw_text_bytes == len(raw)
    assert receipt.decoded_body_sha256 == hashlib.sha256(
        body.encode("utf-8")
    ).hexdigest()
    assert receipt.raw_text_sha256 != receipt.decoded_body_sha256
    assert receipt.source_metadata["text_object_bytes"] == len(raw)

def _bad_metadata_case(case: str):
    payload = metadata_payload()
    outcome = None
    if case == "missing":
        outcome = mr.MetadataRead("missing")
    elif case == "transport":
        outcome = mr.MetadataRead("transport")
    elif case == "too_large":
        outcome = mr.MetadataRead("too_large")
    elif case == "actual_oversize":
        return b"x" * (mr.METADATA_MAX_BYTES + 1), None
    elif case == "invalid_json":
        return b"{", None
    elif case == "invalid_schema":
        payload["data"].pop("attributes")
    elif case == "identity":
        payload["data"]["attributes"]["docketId"] = "OTHER-DOCKET"
    elif case == "attachment":
        payload["included"][0]["attributes"]["fileFormats"][0][
            "fileUrl"
        ] = "https://downloads.regulations.gov/other/attachment_1.pdf"
    elif case == "missing_relationship":
        payload["data"]["relationships"]["attachments"]["data"] = []
    elif case == "duplicate_relationship":
        rel = payload["data"]["relationships"]["attachments"]["data"]
        rel.append(copy.deepcopy(rel[0]))
    elif case == "duplicate_included":
        payload["included"].append(copy.deepcopy(payload["included"][0]))
    elif case == "duplicate_format":
        formats = payload["included"][0]["attributes"]["fileFormats"]
        formats.append(copy.deepcopy(formats[0]))
    elif case == "two_related_matches":
        payload["data"]["relationships"]["attachments"]["data"].append({
            "type": "attachments", "id": "attachment-2",
        })
        second = copy.deepcopy(payload["included"][0])
        second["id"] = "attachment-2"
        payload["included"].append(second)
    elif case == "unhashable_included_id":
        payload["included"][0]["id"] = ["not", "an", "id"]
    elif case == "unhashable_relationship_id":
        payload["data"]["relationships"]["attachments"]["data"][0]["id"] = {
            "not": "an id",
        }
    else:
        raise AssertionError(case)
    return payload, outcome


@pytest.mark.parametrize(
    "case,reason,counter",
    [
        ("missing", "metadata-missing", "skipped_network_error"),
        ("transport", "metadata-transport", "skipped_network_error"),
        ("too_large", "metadata-too-large", "skipped_parse_error"),
        ("actual_oversize", "metadata-too-large", "skipped_parse_error"),
        ("invalid_json", "metadata-invalid-json", "skipped_parse_error"),
        ("invalid_schema", "metadata-invalid-schema", "skipped_parse_error"),
        ("identity", "metadata-identity-mismatch", "skipped_parse_error"),
        ("attachment", "metadata-attachment-mismatch", "skipped_parse_error"),
        ("missing_relationship", "metadata-attachment-mismatch", "skipped_parse_error"),
        ("duplicate_relationship", "metadata-attachment-ambiguous", "skipped_parse_error"),
        ("duplicate_included", "metadata-attachment-ambiguous", "skipped_parse_error"),
        ("duplicate_format", "metadata-attachment-ambiguous", "skipped_parse_error"),
        ("two_related_matches", "metadata-attachment-ambiguous", "skipped_parse_error"),
        ("unhashable_included_id", "metadata-invalid-schema", "skipped_parse_error"),
        ("unhashable_relationship_id", "metadata-invalid-schema", "skipped_parse_error"),
    ],
)
def test_standard_metadata_failures_skip_without_partial_output(
    tmp_path, case, reason, counter,
):
    payload, outcome = _bad_metadata_case(case)
    store = standard_store(payload, outcome=outcome)
    output_dir = tmp_path / "ai-prose-baselines-private" / case
    summary_path = tmp_path / "summary.json"
    args = make_args(
        prefixes=[STANDARD_PREFIX], metadata_mode="standard",
        output_dir=str(output_dir), emit_manifest=str(output_dir / "draft.jsonl"),
        out=str(summary_path),
    )
    assert mr.run(args, store=store) == 1
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert [row["reason"] for row in summary["skip_log"]] == [reason]
    assert summary[counter] == 1
    assert not list(output_dir.glob("*.txt"))
    assert not list(output_dir.glob("*.meta.json"))
    assert not (output_dir / "draft.jsonl").exists()
    assert store.metadata_got_keys == [STANDARD_METADATA_KEY]


@pytest.mark.parametrize(
    "name",
    [
        "epa", "EPA/../EPA", "EPA%2F", "EPA?x", "bad.engine",
        "EPA-TEST-2020-0001-x", "EPA-TEST-2020-0001-7_attachment_01",
    ],
)
def test_standard_key_grammar_rejects_unreviewed_forms(name):
    if name == "EPA-TEST-2020-0001-7_attachment_01":
        key = STANDARD_TEXT_KEY.replace(
            f"{STANDARD_COMMENT}_attachment_1", name,
        )
    elif name == "EPA-TEST-2020-0001-x":
        key = STANDARD_TEXT_KEY.replace(STANDARD_COMMENT, name)
    elif name == "bad.engine":
        key = STANDARD_TEXT_KEY.replace("/pdftotext/", "/bad.engine/")
    else:
        key = STANDARD_TEXT_KEY.replace("/EPA/", f"/{name}/")
    with pytest.raises(mr.MetadataSkip) as exc:
        mr._standard_metadata_key(key)
    assert exc.value.reason == "metadata-unsupported-key"


def test_unsupported_key_skips_before_metadata_get_and_allow_empty(tmp_path):
    output_dir = tmp_path / "ai-prose-baselines-private" / "unsupported"
    summary_path = tmp_path / "summary.json"
    store = make_store()
    args = make_args(
        metadata_mode="standard",
        output_dir=str(output_dir), emit_manifest=str(output_dir / "draft.jsonl"),
        out=str(summary_path),
    )
    assert mr.run(args, store=store) == 1
    assert store.metadata_got_keys == []
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert {row["reason"] for row in summary["skip_log"]} == {
        "metadata-unsupported-key",
    }
    assert summary["skipped_filtered"] == 3
    assert mr.run(make_args(
        metadata_mode="standard", allow_empty=True,
        output_dir=str(output_dir), emit_manifest=str(output_dir / "draft.jsonl"),
    ), store=make_store()) == 0


def test_bad_metadata_then_valid_item_continues(tmp_path):
    other_comment = STANDARD_DOCKET + "-0008"
    other_text = STANDARD_TEXT_KEY.replace(STANDARD_COMMENT, other_comment)
    other_meta = STANDARD_METADATA_KEY.replace(STANDARD_COMMENT, other_comment)
    other_url = STANDARD_URL.replace(STANDARD_COMMENT, other_comment)
    bad = metadata_payload()
    bad["data"]["attributes"]["docketId"] = "OTHER-DOCKET"
    good = metadata_payload(comment=other_comment, url=other_url)
    store = mr.FixtureObjectStore({
        STANDARD_TEXT_KEY: _C1.encode(),
        STANDARD_METADATA_KEY: json.dumps(bad).encode(),
        other_text: _C2.encode(),
        other_meta: json.dumps(good).encode(),
    })
    output_dir = tmp_path / "ai-prose-baselines-private" / "mixed"
    summary_path = tmp_path / "summary.json"
    args = make_args(
        prefixes=[STANDARD_PREFIX], metadata_mode="standard",
        output_dir=str(output_dir), emit_manifest=str(output_dir / "draft.jsonl"),
        out=str(summary_path),
    )
    assert mr.run(args, store=store) == 0
    assert len(read_manifest(output_dir / "draft.jsonl")) == 1
    assert read_manifest(output_dir / "draft.jsonl")[0]["source"] == other_text
    assert [row["reason"] for row in json.loads(
        summary_path.read_text(encoding="utf-8")
    )["skip_log"]] == ["metadata-identity-mismatch"]

def _prepared_standard_item(tmp_path, *, dry_run=False, min_words=1):
    output_dir = tmp_path / "ai-prose-baselines-private" / "direct"
    options = mr.parse_options(make_args(
        prefixes=[STANDARD_PREFIX], metadata_mode="standard",
        dry_run=dry_run, min_words=min_words, output_dir=str(output_dir),
        emit_manifest=str(output_dir / "draft.jsonl"),
    ))
    item = mr.ItemMeta(locator=STANDARD_TEXT_KEY)
    summary = ac.RunSummary()
    store = standard_store()
    body, title, author, date = mr.extract_one(item, options, store)
    return options, item, summary, store, body, title, author, date


@pytest.mark.parametrize(
    "tamper",
    ["body", "locator", "bucket", "mode", "raw_hash", "date"],
)
def test_fetched_receipt_rejects_mismatched_manual_processing(
    tmp_path, tamper,
):
    options, item, summary, _store, body, title, author, date = (
        _prepared_standard_item(tmp_path)
    )
    if tamper == "body":
        body += " changed"
    elif tamper == "locator":
        item.locator = "other-key"
    elif tamper == "bucket":
        options.bucket = "other-bucket"
    elif tamper == "mode":
        item.metadata_receipt.mode = "off"
    elif tamper == "raw_hash":
        item.metadata_receipt.raw_text_sha256 = "0" * 64
    elif tamper == "date":
        date = __import__("datetime").date(2014, 6, 2)
    with pytest.raises(mr.MetadataSkip) as exc:
        mr.process_one_item(
            item, body, title, author, date,
            options=options, summary=summary,
        )
    assert exc.value.reason == "metadata-custody-mismatch"
    assert item.metadata_receipt is None


@pytest.mark.parametrize("tamper", ["source_url", "cleaned_text", "date_written"])
def test_processed_receipt_rejects_tampered_piece_before_write(
    tmp_path, tamper,
):
    options, item, summary, _store, body, title, author, date = (
        _prepared_standard_item(tmp_path)
    )
    piece = mr.process_one_item(
        item, body, title, author, date, options=options, summary=summary,
    )
    assert piece is not None
    if tamper == "source_url":
        piece.source_url = "other-key"
    elif tamper == "cleaned_text":
        piece.cleaned_text += " changed"
    else:
        piece.date_written = __import__("datetime").date(2014, 6, 2)
    with pytest.raises(mr.MetadataSkip) as exc:
        mr.emit_piece(piece, item=item, options=options, summary=summary)
    assert exc.value.reason == "metadata-custody-mismatch"
    assert item.metadata_receipt is None
    assert not list(options.output_dir.glob("*.txt"))
    assert not list(options.output_dir.glob("*.meta.json"))


@pytest.mark.parametrize("stage", ["process", "emit"])
@pytest.mark.parametrize(
    "tamper",
    ["swapped_inner", "nested_date", "rehashed_identity"],
)
def test_source_metadata_receipt_binds_whole_projection_and_item_identity(
    tmp_path, stage, tamper,
):
    options, item, summary, store, body, title, author, date = (
        _prepared_standard_item(tmp_path)
    )
    other_comment = STANDARD_DOCKET + "-0008"
    other_text_key = STANDARD_TEXT_KEY.replace(STANDARD_COMMENT, other_comment)
    other_metadata_key = STANDARD_METADATA_KEY.replace(
        STANDARD_COMMENT, other_comment,
    )
    other_url = STANDARD_URL.replace(STANDARD_COMMENT, other_comment)
    store.objects[other_text_key] = store.objects[STANDARD_TEXT_KEY]
    store.objects[other_metadata_key] = json.dumps(
        metadata_payload(comment=other_comment, url=other_url),
        sort_keys=True,
    ).encode("utf-8")
    other_item = mr.ItemMeta(locator=other_text_key)
    mr.extract_one(other_item, options, store)
    assert item.metadata_receipt is not None
    assert other_item.metadata_receipt is not None
    piece = None
    if stage == "emit":
        piece = mr.process_one_item(
            item, body, title, author, date,
            options=options, summary=summary,
        )
        assert piece is not None
    if tamper == "swapped_inner":
        item.metadata_receipt.source_metadata = (
            other_item.metadata_receipt.source_metadata
        )
    elif tamper == "nested_date":
        item.metadata_receipt.source_metadata["reported_dates"]["received"][
            "value"
        ] = "2014-06-03T12:30:00Z"
    else:
        item.metadata_receipt.source_metadata["text_object_key"] = other_text_key
        item.metadata_receipt.source_metadata_sha256 = (
            mr._source_metadata_digest(item.metadata_receipt.source_metadata)
        )
    with pytest.raises(mr.MetadataSkip) as exc:
        if stage == "process":
            mr.process_one_item(
                item, body, title, author, date,
                options=options, summary=summary,
            )
        else:
            mr.emit_piece(piece, item=item, options=options, summary=summary)
    assert exc.value.reason == "metadata-custody-mismatch"
    assert item.metadata_receipt is None
    assert summary.acquired == 0
    assert not list(options.output_dir.glob("*.txt"))
    assert not list(options.output_dir.glob("*.meta.json"))


def test_standard_emit_requires_item_and_consumes_dry_run_receipt(tmp_path):
    options, item, summary, _store, body, title, author, date = (
        _prepared_standard_item(tmp_path, dry_run=True)
    )
    piece = mr.process_one_item(
        item, body, title, author, date, options=options, summary=summary,
    )
    assert piece is not None
    with pytest.raises(mr.MetadataSkip, match="metadata-custody-mismatch"):
        mr.emit_piece(piece, options=options, summary=summary)
    assert item.metadata_receipt is not None
    mr.emit_piece(piece, item=item, options=options, summary=summary)
    assert summary.acquired == 1 and item.metadata_receipt is None
    assert not options.output_dir.exists()
    with pytest.raises(mr.MetadataSkip):
        mr.emit_piece(piece, item=item, options=options, summary=summary)


def test_filter_duplicate_and_unexpected_error_clear_receipt(
    tmp_path, monkeypatch,
):
    options, item, summary, store, body, title, author, date = (
        _prepared_standard_item(tmp_path, min_words=100000)
    )
    assert mr.process_one_item(
        item, body, title, author, date, options=options, summary=summary,
    ) is None
    assert item.metadata_receipt is None
    options.min_words = 1
    body, title, author, date = mr.extract_one(item, options, store)
    piece = mr.process_one_item(
        item, body, title, author, date, options=options, summary=summary,
    )
    mr.emit_piece(piece, item=item, options=options, summary=summary)
    assert item.metadata_receipt is None
    body, title, author, date = mr.extract_one(item, options, store)
    assert mr.process_one_item(
        item, body, title, author, date, options=options, summary=summary,
    ) is None
    assert item.metadata_receipt is None
    body, title, author, date = mr.extract_one(item, options, store)
    def fail_preprocessing(*_args, **_kwargs):
        raise RuntimeError("synthetic preprocessing fault")
    monkeypatch.setattr(ac, "preprocess_text", fail_preprocessing)
    with pytest.raises(RuntimeError, match="synthetic"):
        mr.process_one_item(
            item, body, title, author, date, options=options, summary=summary,
        )
    assert item.metadata_receipt is None


def test_write_failure_clears_processed_receipt(tmp_path, monkeypatch):
    options, item, summary, _store, body, title, author, date = (
        _prepared_standard_item(tmp_path)
    )
    piece = mr.process_one_item(
        item, body, title, author, date, options=options, summary=summary,
    )
    def fail_write(*_args, **_kwargs):
        raise OSError("synthetic write failure")
    monkeypatch.setattr(ac, "write_piece", fail_write)
    with pytest.raises(OSError, match="synthetic write"):
        mr.emit_piece(piece, item=item, options=options, summary=summary)
    assert item.metadata_receipt is None


def test_driver_continues_after_emit_custody_mismatch(tmp_path, monkeypatch):
    other_comment = STANDARD_DOCKET + "-0008"
    other_text = STANDARD_TEXT_KEY.replace(STANDARD_COMMENT, other_comment)
    other_meta = STANDARD_METADATA_KEY.replace(STANDARD_COMMENT, other_comment)
    other_url = STANDARD_URL.replace(STANDARD_COMMENT, other_comment)
    store = mr.FixtureObjectStore({
        STANDARD_TEXT_KEY: _C1.encode(),
        STANDARD_METADATA_KEY: json.dumps(metadata_payload()).encode(),
        other_text: _C2.encode(),
        other_meta: json.dumps(
            metadata_payload(comment=other_comment, url=other_url)
        ).encode(),
    })
    real_process = mr.process_one_item
    def tamper_first(item, *args, **kwargs):
        piece = real_process(item, *args, **kwargs)
        if piece is not None and item.locator == STANDARD_TEXT_KEY:
            piece.source_url = "tampered"
        return piece
    monkeypatch.setattr(mr, "process_one_item", tamper_first)
    output_dir = tmp_path / "ai-prose-baselines-private" / "continuation"
    summary_path = tmp_path / "summary.json"
    args = make_args(
        prefixes=[STANDARD_PREFIX], metadata_mode="standard",
        output_dir=str(output_dir), emit_manifest=str(output_dir / "draft.jsonl"),
        out=str(summary_path),
    )
    assert mr.run(args, store=store) == 0
    assert len(read_manifest(output_dir / "draft.jsonl")) == 1
    assert read_manifest(output_dir / "draft.jsonl")[0]["source"] == other_text
    assert [row["reason"] for row in json.loads(
        summary_path.read_text(encoding="utf-8")
    )["skip_log"]] == ["metadata-custody-mismatch"]


def test_off_mode_ignores_stale_metadata_receipt(tmp_path):
    options = mr.parse_options(make_args(
        output_dir=str(tmp_path / "ai-prose-baselines-private" / "off-direct"),
        min_words=1,
    ))
    item = mr.ItemMeta(locator=f"{PREFIX}/derived/comments_extracted_text/c1_extracted.txt")
    item.metadata_receipt = mr.MetadataReceipt(
        stage="processed", locator=item.locator, bucket="mirrulations",
        mode="standard", decoded_body_sha256="stale", raw_text_sha256="stale",
        raw_text_bytes=0, source_metadata={"status": "stale"},
    )
    store = make_store()
    body, title, author, date = mr.extract_one(item, options, store)
    assert item.metadata_receipt is None
    piece = mr.process_one_item(
        item, body, title, author, date,
        options=options, summary=ac.RunSummary(),
    )
    assert piece is not None
    mr.emit_piece(piece, options=options, summary=ac.RunSummary())
    assert "source_metadata" not in json.loads(
        next(options.output_dir.glob("*.meta.json")).read_bytes()
    )

class ShortReadBody:
    def __init__(
        self, data: bytes, *, chunk_size: int = 65536,
        fail_after: int | None = None,
    ):
        self.data = data
        self.chunk_size = chunk_size
        self.fail_after = fail_after
        self.offset = 0
        self.read_sizes = []
        self.closed = 0

    def read(self, size):
        self.read_sizes.append(size)
        if self.fail_after is not None and self.offset >= self.fail_after:
            raise OSError("synthetic stream fault")
        end = min(len(self.data), self.offset + min(size, self.chunk_size))
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def close(self):
        self.closed += 1


@pytest.mark.parametrize(
    "size,chunk_size,expected",
    [
        (17, 3, "ok"),
        (mr.METADATA_MAX_BYTES, 65536, "ok"),
        (mr.METADATA_MAX_BYTES + 1, 65536, "too_large"),
    ],
)
def test_bounded_reader_handles_short_reads_limit_and_overflow(
    size, chunk_size, expected,
):
    body = ShortReadBody(b"x" * size, chunk_size=chunk_size)
    result = mr._read_metadata_body(body)
    assert result.status == expected
    assert (len(result.data) if result.data is not None else None) == (
        size if expected == "ok" else None
    )
    assert body.closed == 1
    assert all(0 < n <= mr.METADATA_MAX_BYTES + 1 for n in body.read_sizes)
    assert sum(min(n, chunk_size) for n in body.read_sizes) >= min(
        size, mr.METADATA_MAX_BYTES + 1,
    )


def test_bounded_reader_closes_on_midstream_failure():
    body = ShortReadBody(b"x" * 100, chunk_size=8, fail_after=16)
    result = mr._read_metadata_body(body)
    assert result.status == "transport"
    assert body.closed == 1


def _fake_s3_factory(monkeypatch, metadata_response):
    calls = []
    class FakeClient:
        def __init__(self, kind):
            self.kind = kind
        def get_object(self, *, Bucket, Key):
            assert Bucket == "mirrulations" and Key == STANDARD_METADATA_KEY
            if isinstance(metadata_response, BaseException):
                raise metadata_response
            return metadata_response
    fake_boto3 = types.ModuleType("boto3")
    def client(_service, *, region_name, config):
        assert _service == "s3" and region_name == "us-east-1"
        kind = "text" if not calls else "metadata"
        calls.append((kind, config))
        return FakeClient(kind)
    fake_boto3.client = client
    fake_botocore = types.ModuleType("botocore")
    fake_botocore.__path__ = []
    fake_botocore.UNSIGNED = "unsigned"
    fake_config = types.ModuleType("botocore.config")
    fake_config.Config = lambda **kwargs: kwargs
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", fake_botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", fake_config)
    return calls


def test_s3_metadata_client_is_separate_lazy_bounded_and_closes(monkeypatch):
    body = ShortReadBody(b"synthetic metadata", chunk_size=4)
    calls = _fake_s3_factory(
        monkeypatch, {"Body": body, "ContentLength": len(body.data)},
    )
    store = mr.make_s3_store()
    assert len(calls) == 1 and calls[0][0] == "text"
    result = store.get_metadata(STANDARD_METADATA_KEY)
    assert result == mr.MetadataRead("ok", body.data)
    assert len(calls) == 2 and calls[1][0] == "metadata"
    config = calls[1][1]
    assert config["connect_timeout"] == 10
    assert config["read_timeout"] == 25
    assert config["retries"] == {"total_max_attempts": 2}
    assert body.closed == 1
    assert len(body.read_sizes) > 1


def test_s3_metadata_client_closes_on_early_oversize(monkeypatch):
    body = ShortReadBody(b"never read")
    _fake_s3_factory(
        monkeypatch,
        {"Body": body, "ContentLength": mr.METADATA_MAX_BYTES + 1},
    )
    store = mr.make_s3_store()
    result = store.get_metadata(STANDARD_METADATA_KEY)
    assert result.status == "too_large"
    assert body.read_sizes == [] and body.closed == 1


@pytest.mark.parametrize("code,expected", [
    ("NoSuchKey", "missing"), ("404", "missing"),
    ("AccessDenied", "transport"),
])
def test_s3_metadata_client_distinguishes_missing_and_transport(
    monkeypatch, code, expected,
):
    class SyntheticClientError(Exception):
        def __init__(self):
            self.response = {"Error": {"Code": code}}
    _fake_s3_factory(monkeypatch, SyntheticClientError())
    store = mr.make_s3_store()
    assert store.get_metadata(STANDARD_METADATA_KEY).status == expected

@pytest.mark.parametrize("metadata_mode", ["off", "standard"])
@pytest.mark.parametrize(
    "status",
    ["unknown", "native", "non_native_advanced",
     "non_native_intermediate", "learner"],
)
def test_language_status_manifest_and_text_conservation(
    tmp_path, metadata_mode, status,
):
    def acquire(name, requested):
        output_dir = tmp_path / "ai-prose-baselines-private" / name
        args = make_args(
            prefixes=[PREFIX] if metadata_mode == "off" else [STANDARD_PREFIX],
            metadata_mode=metadata_mode, output_dir=str(output_dir),
            emit_manifest=str(output_dir / "draft.jsonl"),
            **({"language_status": requested} if requested is not None else {}),
        )
        store = make_store() if metadata_mode == "off" else standard_store()
        assert mr.run(args, store=store) == 0
        entries = sorted(read_manifest(output_dir / "draft.jsonl"), key=lambda e: e["id"])
        texts = {p.name: p.read_bytes() for p in output_dir.glob("*.txt")}
        sidecars = {p.name: json.loads(p.read_text(encoding="utf-8"))
                    for p in output_dir.glob("*.meta.json")}
        return entries, texts, sidecars

    baseline_entries, baseline_texts, baseline_sidecars = acquire("baseline", None)
    selected_entries, selected_texts, selected_sidecars = acquire("selected", status)
    assert len(baseline_entries) == len(selected_entries) == (
        2 if metadata_mode == "off" else 1
    )
    assert baseline_texts == selected_texts
    assert all(e["language_status"] == "unknown" for e in baseline_entries)
    assert all(e["language_status"] == status for e in selected_entries)
    for baseline, selected in zip(baseline_entries, selected_entries):
        assert {k: v for k, v in baseline.items() if k != "language_status"} == {
            k: v for k, v in selected.items() if k != "language_status"
        }
    # Custody sidecars may differ by acquisition time, but the source receipt
    # and cleaned-content hash must remain bound to the same source object.
    for name, baseline in baseline_sidecars.items():
        selected = selected_sidecars[name]
        assert baseline["content_hash"] == selected["content_hash"]
        source_before = baseline.get("source_metadata")
        source_after = selected.get("source_metadata")
        if source_before is None:
            assert source_after is None
        else:
            assert source_after is not None
            assert {k: v for k, v in source_before.items() if k != "retrieved_at"} == {
                k: v for k, v in source_after.items() if k != "retrieved_at"
            }


def test_language_status_omitted_namespace_and_options_default():
    parsed = mr.parse_options(make_args())
    assert parsed.language_status == "unknown"
    assert mr.ProcessOptions(**{
        key: value for key, value in vars(parsed).items()
        if key != "language_status"
    }).language_status == "unknown"
    assert mr.parse_options(make_args(language_status="learner")).language_status == "learner"
    assert mr.build_arg_parser().parse_args([
        "--prefix", PREFIX, "--impostor-for", "x",
        "--register", "regulatory_comment",
        "--consent-status", "public_record",
    ]).language_status == "unknown"


def test_invalid_language_status_cli_rejects_before_run(tmp_path, monkeypatch):
    output_dir = tmp_path / "ai-prose-baselines-private" / "invalid"
    def unexpected_run(*_args, **_kwargs):
        pytest.fail("invalid CLI value must be rejected before acquisition")
    monkeypatch.setattr(mr, "run", unexpected_run)
    with pytest.raises(SystemExit) as exc:
        mr.main([
            "--prefix", PREFIX, "--impostor-for", "x",
            "--register", "regulatory_comment",
            "--consent-status", "public_record", "--output-dir", str(output_dir),
            "--language-status", "unsupported",
        ])
    assert exc.value.code == 2
    assert not output_dir.exists()


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
