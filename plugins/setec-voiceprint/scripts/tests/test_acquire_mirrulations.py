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
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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


PREFIX = "EPA/EPA-HQ-OAR-2013-0602"

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
    "OTHER/OTHER-DOCKET/derived/comments_extracted_text/x_extracted.txt": b"out of scope",
}


def make_args(**overrides) -> argparse.Namespace:
    base = dict(
        prefixes=[PREFIX],
        bucket="mirrulations",
        region="us-east-1",
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
        allow_public_output=True,
        allow_non_prose=False,
        strip_rules=None,
        strip_aggressive=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def make_store() -> "mr.FixtureObjectStore":
    return mr.FixtureObjectStore(OBJECTS)


def read_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


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
        "--dry-run", "--allow-public-output",
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


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
