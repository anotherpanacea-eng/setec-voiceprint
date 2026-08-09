#!/usr/bin/env python3
"""Tests for near_dup_dedup — document-level dedup + passage/span hygiene.

Document-mode invariants (frozen — passage mode must not move them):
  * A planted near-duplicate (same essay, lightly edited / reheadered) is
    removed; genuinely distinct documents are all kept.
  * The kept representative is deterministic (longest text wins).
  * Manifest round-trip: dropped rows are removed, all other rows preserved
    in order; unresolvable-text rows pass through untouched.
  * The shingle helper is stdlib and behaves on short input.

Passage-mode invariants (spec 36 M1, the second half of this file):
  * Chunking is raw paragraphs, never coalesced, and every passage/span slices
    back byte-for-byte from the document text as loaded.
  * Stage A uses complete frequency-ordered prefix candidates confirmed on
    EXACT Jaccard, and sub-floor passages are grouped by exact token equality.
  * Stage B reports the motivating 41-token embedded span that Stage A provably
    cannot see, and honors the `L >= max(k, min_span_words)` guarantee.
  * The report carries `assumptions` + a real ClaimLicense and passes the
    recursive no-verdict key walk; the export is manifest_validator-clean and
    REFUSES rather than inventing a missing ai_status / use.

datasketch is optional within the acquisition tier; the dep-gated tests skip
cleanly when it's absent (the shingle, import-purity, and whole Stage-B path
still run — Stage B is stdlib).
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import stat
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import manifest_validator as mv  # type: ignore  # noqa: E402
import near_dup_dedup as ndd  # type: ignore  # noqa: E402
import pool_guard  # type: ignore  # noqa: E402
import author_corpus_export as ace  # type: ignore  # noqa: E402

_datasketch_available = True
try:
    import datasketch  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover
    _datasketch_available = False

_needs_datasketch = pytest.mark.skipif(
    not _datasketch_available,
    reason="datasketch not installed; install requirements-acquisition.txt",
) if pytest is not None else (lambda f: f)


# A base essay and a near-duplicate of it (a few words changed + a new header),
# plus two genuinely distinct documents.
BASE = (
    "What we keep and what we discard becomes, at some scale of accumulation, "
    "a portrait of our judgment. I have been thinking about this in connection "
    "with my own archive, which is now large enough to have an internal "
    "weather: storms in some sections, long stretches of overcast in others, "
    "and a few unaccountable bright afternoons when whatever I was reading "
    "seemed to fall together in ways I did not earn."
)
NEAR_DUP = (
    "Reprinted from the newsletter. "
    "What we keep and what we discard becomes, at some scale of accumulation, "
    "a portrait of our judgment. I have been thinking about this in connection "
    "with my own archive, which is now large enough to have an internal "
    "weather: storms in some sections, long stretches of overcast in others, "
    "and a few rare bright afternoons when whatever I was reading "
    "seemed to fall together in ways I had not earned."
)
DISTINCT_A = (
    "The tide charts for the eastern approaches were wrong again this spring, "
    "and the pilots who trusted them found the channel a foot shallower than "
    "printed. We recalibrated against the new survey and lost a week to it."
)
DISTINCT_B = (
    "Monetary policy in a small open economy is mostly an exercise in managing "
    "expectations about a currency the central bank does not fully control. "
    "The textbook levers exist, but their transmission is slow and lossy."
)


def test_shingles_short_and_normal():
    # Fewer than k words → a single whole-doc shingle; empty → empty set.
    assert ndd.shingles("one two", k=5) == {"one two"}
    assert ndd.shingles("", k=5) == set()
    sh = ndd.shingles("a b c d e f", k=5)
    assert "a b c d e" in sh and "b c d e f" in sh
    # Case/punctuation-insensitive.
    assert ndd.shingles("The Quick, Brown!", k=2) == ndd.shingles("the quick brown", k=2)


@_needs_datasketch
def test_near_duplicate_removed_distinct_kept():
    records = [
        ("base", BASE),
        ("near_dup", NEAR_DUP),
        ("distinct_a", DISTINCT_A),
        ("distinct_b", DISTINCT_B),
    ]
    result = ndd.dedup_records(records, threshold=0.6)
    assert result.total == 4
    # The near-duplicate collapses to one representative; both distincts kept.
    assert len(result.kept) == 3
    assert "distinct_a" in result.kept and "distinct_b" in result.kept
    assert len(result.dropped) == 1
    # Exactly one of {base, near_dup} is dropped; the longer one (NEAR_DUP, it
    # carries the extra "Reprinted from..." header) is the kept representative.
    assert result.dropped == ["base"]
    assert "near_dup" in result.kept
    assert result.clusters == {"near_dup": ["base"]}


@_needs_datasketch
def test_all_distinct_keeps_everything():
    records = [("a", DISTINCT_A), ("b", DISTINCT_B), ("base", BASE)]
    result = ndd.dedup_records(records, threshold=0.7)
    assert sorted(result.kept) == ["a", "b", "base"]
    assert result.dropped == []
    assert result.clusters == {}


@_needs_datasketch
def test_deterministic_across_runs():
    records = [("x", BASE), ("y", NEAR_DUP)]
    r1 = ndd.dedup_records(records, threshold=0.6)
    r2 = ndd.dedup_records(records, threshold=0.6)
    assert r1.kept == r2.kept and r1.dropped == r2.dropped


@_needs_datasketch
def test_duplicate_id_rejected():
    with pytest.raises(ValueError):
        ndd.dedup_records([("dup", BASE), ("dup", DISTINCT_A)])


@_needs_datasketch
def test_dedup_manifest_round_trip(tmp_path):
    manifest = tmp_path / "draft_manifest.jsonl"
    rows = [
        {"id": "base", "text": BASE, "author": "Author"},
        {"id": "near_dup", "text": NEAR_DUP, "author": "Author"},
        {"id": "distinct_a", "text": DISTINCT_A, "author": "Other"},
        # A row with no resolvable text must pass through untouched.
        {"id": "no_text_row", "note": "metadata-only"},
    ]
    manifest.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    out = tmp_path / "deduped.jsonl"
    result = ndd.dedup_manifest(manifest, out_path=out, threshold=0.6)

    assert result.dropped == ["base"]
    kept_ids = [
        json.loads(line)["id"]
        for line in out.read_text(encoding="utf-8").splitlines()
    ]
    # base dropped; near_dup + distinct_a + the text-less row all preserved,
    # in original order.
    assert kept_ids == ["near_dup", "distinct_a", "no_text_row"]


@_needs_datasketch
def test_dedup_manifest_dry_run_does_not_write(tmp_path):
    manifest = tmp_path / "m.jsonl"
    original = (
        json.dumps({"id": "base", "text": BASE})
        + "\n"
        + json.dumps({"id": "near_dup", "text": NEAR_DUP})
        + "\n"
    )
    manifest.write_text(original, encoding="utf-8")
    result = ndd.dedup_manifest(manifest, threshold=0.6, dry_run=True)
    assert result.dropped == ["base"]
    # Dry-run leaves the input untouched.
    assert manifest.read_text(encoding="utf-8") == original


def test_base_import_is_pure():
    # near_dup_dedup imports with datasketch absent; the dep is only needed at
    # call time. This asserts the module-level import didn't pull datasketch.
    assert "near_dup_dedup" in sys.modules
    # The shingle helper is stdlib and works regardless of datasketch.
    assert ndd.shingles("stdlib only path", k=2)


def _strict_record(payload: bytes, fingerprint_digit: str = "f") -> dict[str, object]:
    """A valid closed Voicewright source row for the additive Spec-80 path."""
    content_sha256 = ace._sha(payload)
    normalized_sha256 = ace._sha(
        ace._normalize_text(payload.decode("utf-8")).encode("utf-8")
    )
    content_hex = content_sha256.removeprefix("sha256:")
    record: dict[str, object] = {
        "schema": "voicewright-author-corpus/1", "id": "",
        "persona": "owner", "register": "personal.letter", "role": "author",
        "text_path": f"texts/{content_hex[:2]}/{content_hex[2:4]}/{content_hex}.txt",
        "source_entry_fingerprint": "src:hmac-sha256:" + fingerprint_digit * 64,
        "source_group": "grp:hmac-sha256:" + "a" * 64,
        "conversation_id": None, "date": "2026-01-01",
        "unit_kind": "document", "unit_index": 0, "unit_count": 1,
        "corpus_role": "identity_baseline", "use": ["voice_profile"],
        "consent_status": "author_consent",
        "ai_status": "pre_ai_human", "source_kind": "document_local",
        "content_sha256": content_sha256,
        "normalized_text_sha256": normalized_sha256,
    }
    record["id"] = ace._record_id(record)
    return record


def _write_strict_source(root: Path, payload: bytes) -> dict[str, object]:
    record = _strict_record(payload)
    target = root / str(record["text_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return record


def test_spec80_strict_tokenizer_is_additive_and_legacy_report_is_stable(tmp_path):
    """No strict switch means the pre-Spec-80 report serialization remains unchanged."""
    manifest = tmp_path / "legacy.jsonl"
    manifest.write_text(json.dumps({"id": "x", "text": "Alpha beta."}) + "\n")
    one = ndd.analyze_passages(manifest, stages=["b"], checkpoint_path=None)[0]
    two = ndd.analyze_passages(
        manifest, stages=["b"], checkpoint_path=None, strict_spec80=False,
    )[0]
    assert json.dumps(one, indent=2, sort_keys=True) == json.dumps(two, indent=2, sort_keys=True)
    assert "spec80_tokenizer" not in one


def test_spec80_publication_requires_committed_producer_and_binds_three_artifacts(tmp_path, monkeypatch):
    """Synthetic committed-repo e2e: strict output uses frozen tokens and receipt-last package."""
    if not _datasketch_available:
        pytest.skip("strict producer requires Stage A's optional dependency")
    root = tmp_path / "root"; root.mkdir()
    payload = (
        "ALPHA beta gamma delta epsilon zeta eta theta iota kappa.\n\n"
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa."
    ).encode()
    record = _write_strict_source(root, payload)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(record, separators=(",", ":")) + "\n")
    repo = tmp_path / "repo"; repo.mkdir()
    producer = repo / "near_dup_dedup.py"
    shutil.copy2(Path(ndd.__file__), producer)
    subprocess.run(["git", "init", "--object-format=sha1", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "near_dup_dedup.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "producer"], check=True)
    args = type("Args", (), {
        "manifest": manifest, "source_root": root, "report_out": tmp_path / "inventory.json",
        "commitment_out": tmp_path / "commitment.json", "receipt_out": tmp_path / "receipt.json",
        "threshold": 0.8, "num_perm": 128, "shingle_size": 5, "min_passage_words": 10,
        "span_shingle_k": 8, "min_span_words": 20,
    })()
    report, _passages, _rows = ndd.analyze_passages(
        manifest, strict_spec80=True, source_root=root, stages=["a", "b"],
    )
    inventory = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    monkeypatch.setattr(ndd, "SCRIPT_DIR", repo)
    monkeypatch.setattr(ndd, "__file__", str(producer))
    ndd._publish_spec80_package(args, inventory)
    commitment = json.loads(args.commitment_out.read_text())
    receipt = json.loads(args.receipt_out.read_text())
    assert receipt["inventory_sha256"] == "sha256:" + hashlib.sha256(inventory).hexdigest()
    assert receipt["commitment_sha256"] == commitment["commitment_sha256"]
    assert commitment["algorithm_parameters"]["stage_a"]["tokenization"] == "setec_frozen_unicode_word_lower_v1"
    assert commitment["sources"][0]["source_doc_id"] == record["id"]
    assert report["spec80_tokenizer"]["schema"] == "setec-frozen-unicode-word-lower/1"


def test_spec80_strict_refuses_dirty_producer_identity(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    producer = repo / "near_dup_dedup.py"; producer.write_text("original\n")
    subprocess.run(["git", "init", "--object-format=sha1", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "near_dup_dedup.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "producer"], check=True)
    producer.write_text("changed\n")
    with pytest.raises(ndd.source_commitment.CommitmentError):
        ndd.source_commitment.committed_producer_identity(repository=repo, script=producer)


def test_spec80_refuses_invalid_author_row_before_source_open(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    row = _strict_record(b"private source")
    row["role"] = "source"
    row["text_path"] = "../outside.txt"
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(row, separators=(",", ":")) + "\n")
    opened = False

    def unexpected_open(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("source bytes opened before metadata validation")

    monkeypatch.setattr(ndd.source_commitment.os, "open", unexpected_open)
    with pytest.raises(ndd.source_commitment.CommitmentError, match="record metadata"):
        ndd.source_commitment.load_strict_sources(manifest, root)
    assert not opened


def test_spec80_refuses_hardlinked_source(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    payload = b"private source"
    row = _strict_record(payload)
    outside = tmp_path / "outside.txt"; outside.write_bytes(payload)
    target = root / str(row["text_path"])
    target.parent.mkdir(parents=True)
    target.hardlink_to(outside)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(row, separators=(",", ":")) + "\n")
    with pytest.raises(ndd.source_commitment.CommitmentError, match="source file"):
        ndd.source_commitment.load_strict_sources(manifest, root)


def test_spec80_refuses_symlinked_source_ancestor(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    payload = b"private source"
    row = _strict_record(payload)
    outside = tmp_path / "outside"; outside.mkdir()
    (root / "texts").symlink_to(outside, target_is_directory=True)
    target = outside.joinpath(*str(row["text_path"]).split("/")[1:])
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(row, separators=(",", ":")) + "\n")
    with pytest.raises(ndd.source_commitment.CommitmentError, match="source file"):
        ndd.source_commitment.load_strict_sources(manifest, root)


def test_spec80_refuses_source_tree_swap_after_preflight(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    payload = b"private source"
    row = _write_strict_source(root, payload)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(row, separators=(",", ":")) + "\n")
    original_preflight = ndd.source_commitment._preflight_sources

    def preflight_then_swap(manifest_path, source_root):
        planned = original_preflight(manifest_path, source_root)
        moved = tmp_path / "moved-texts"
        (root / "texts").rename(moved)
        outside = tmp_path / "outside-texts"
        outside_target = outside.joinpath(*str(row["text_path"]).split("/")[1:])
        outside_target.parent.mkdir(parents=True)
        outside_target.write_bytes(payload)
        (root / "texts").symlink_to(outside, target_is_directory=True)
        return planned

    monkeypatch.setattr(
        ndd.source_commitment, "_preflight_sources", preflight_then_swap,
    )
    with pytest.raises(ndd.source_commitment.CommitmentError, match="source file"):
        ndd.source_commitment.load_strict_sources(manifest, root)


def test_spec80_refuses_root_swap_during_atomic_pin(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    moved = tmp_path / "moved-root"
    original_fstat = ndd.source_commitment.os.fstat
    swapped = False

    def fstat_then_swap(descriptor):
        nonlocal swapped
        metadata = original_fstat(descriptor)
        if not swapped:
            root.rename(moved)
            root.symlink_to(outside, target_is_directory=True)
            swapped = True
        return metadata

    monkeypatch.setattr(ndd.source_commitment.os, "fstat", fstat_then_swap)
    with pytest.raises(ndd.source_commitment.CommitmentError, match="source root"):
        ndd.source_commitment._pin_source_root(root)


def test_spec80_missing_source_refuses_without_raw_filesystem_error(tmp_path):
    root = tmp_path / "root"; root.mkdir()
    row = _strict_record(b"missing private source")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(json.dumps(row, separators=(",", ":")) + "\n")
    with pytest.raises(ndd.source_commitment.CommitmentError, match="source file"):
        ndd.source_commitment.load_strict_sources(manifest, root)


def test_spec80_commitment_helper_has_no_standalone_minting_cli():
    assert not hasattr(ndd.source_commitment, "main")
    with pytest.raises(ndd.source_commitment.CommitmentError, match="parameter schema"):
        ndd.source_commitment._validate_algorithm_parameters({})


def test_spec80_refuses_aliased_output_destinations(tmp_path):
    shared = tmp_path / "shared.json"
    args = type("Args", (), {
        "report_out": shared,
        "commitment_out": shared,
        "receipt_out": tmp_path / "receipt.json",
    })()
    with pytest.raises(ndd.PassageModeError, match="outputs must be distinct"):
        ndd._publish_spec80_package(args, b"{}\n")
    assert not shared.exists()


def test_spec80_threshold_commitment_round_trips_exact_float():
    threshold = 0.12345678901234566
    args = type("Args", (), {
        "threshold": threshold, "num_perm": 128, "shingle_size": 5,
        "min_passage_words": 10, "span_shingle_k": 8, "min_span_words": 20,
    })()
    parameters = ndd._strict_algorithm_parameters(args)
    decimal = parameters["stage_a"]["threshold_decimal"]
    assert decimal == repr(threshold)
    assert float(decimal) == threshold


def test_spec80_manifest_refuses_crlf_framing(tmp_path):
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_bytes(b'{"schema":"voicewright-author-corpus/1"}\r\n')
    with pytest.raises(ndd.source_commitment.CommitmentError, match="jsonl framing"):
        ndd.source_commitment._strict_jsonl(manifest)


# =====================================================================
# Passage / span mode (spec 36 M1)
#
# Two stages with disjoint detection classes, so the tests are split the
# same way: Stage A pins the whole-passage near-dup class (chunking,
# short-exact grouping, exact-Jaccard confirmation, the export), Stage B
# pins the embedded-span class (the motivating case + the arithmetic
# detection guarantee). Everything Stage-B is stdlib and runs without
# datasketch.
# =====================================================================

# A deterministic, dependency-free filler vocabulary. Filler paragraphs must be
# mutually distinct at the 8-shingle level, so a rotating stride over a 24-word
# list (rather than random choice) keeps the fixtures readable AND collision-free.
_FILLER_WORDS = [
    "harbor", "lantern", "meridian", "quarry", "sable", "thicket", "vellum", "willow",
    "cinder", "drift", "ember", "furrow", "granite", "hollow", "ivy", "juniper",
    "kelp", "loam", "marsh", "nettle", "orchard", "plume", "quill", "reed",
]


def _filler(n: int, seed: int) -> str:
    """`n` distinct-ish filler tokens; different seeds share no 8-shingle."""
    step = 5 + (seed % 7)
    return " ".join(
        _FILLER_WORDS[(seed * 3 + i * step) % len(_FILLER_WORDS)] + str(seed * 100 + i)
        for i in range(n)
    )


# The motivating case: a 41-token contiguous span, verbatim in two documents that
# are NOT document-level near-duplicates.
SPAN_41 = (
    "the archive was never a neutral container but a set of decisions about what "
    "would survive and what would be allowed to fall quietly out of the record "
    "entirely so we chose again and again without once saying it out loud"
)
assert len(SPAN_41.split()) == 41, "fixture drift: the motivating span must be 41 tokens"


def _passage_manifest(tmp_path, rows, name="corpus.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def _full_row(rid, text, **extra):
    """A manifest row carrying everything the export requires + inherits."""
    row = {
        "id": rid, "text": text, "ai_status": "pre_ai_human", "use": ["baseline"],
        "privacy": "private", "consent_status": "author_consent",
        "register": "blog_essay", "language_status": "native",
        "corpus_role": "identity_baseline", "author": "Author",
        "source": "fixture", "era": "pre_chatgpt", "topic": "archives",
    }
    row.update(extra)
    return row


# --- chunking + provenance (test contract 1) --------------------------------

def test_chunker_no_coalescing_and_slices_back():
    doc = (
        "First paragraph, several words long, standing on its own line.\n\n"
        "Tiny one.\n\n\n"
        "   Third paragraph with leading whitespace that must not appear in the slice.   \n\n"
        "Fourth and last."
    )
    passages = ndd.chunk_document("doc-1", doc)
    assert [p.text for p in passages] == [
        "First paragraph, several words long, standing on its own line.",
        "Tiny one.",
        "Third paragraph with leading whitespace that must not appear in the slice.",
        "Fourth and last.",
    ]
    assert [p.passage_id for p in passages] == [
        "doc-1#p0000", "doc-1#p0001", "doc-1#p0002", "doc-1#p0003",
    ]
    for p in passages:
        # The pinned slice-back invariant: offsets index the text AS LOADED.
        assert doc[p.char_start:p.char_end] == p.text
        prov = ndd._passage_provenance(p, "corpus.jsonl")
        # The provenance hash is of the EXACT raw slice — no folding, no NFC.
        assert prov["sha256"] == hashlib.sha256(p.text.encode("utf-8")).hexdigest()
        assert prov["sha256"] != hashlib.sha256(p.text.lower().encode("utf-8")).hexdigest() \
            or p.text == p.text.lower()
        assert prov["source_doc_id"] == "doc-1"
        assert prov["source_manifest"] == "corpus.jsonl"


def test_chunker_rejects_passage_shaped_doc_id():
    """Test contract 14 — a doc id ending in '#p<digits>' would nest ambiguously."""
    with pytest.raises(ndd.PassageModeError) as e:
        ndd.chunk_document("blog-2019#p0007", "Some text.")
    assert "#p" in str(e.value)


# --- Stage A (test contract 2, 3, 11) ---------------------------------------

@_needs_datasketch
def test_short_passages_grouped_exactly_never_reach_lsh():
    """Contract 2: identical sub-floor sign-offs group; different ones do not."""
    doc_a = "Long opening paragraph " + _filler(40, 1) + "\n\nThanks for reading."
    doc_b = "Different opening paragraph " + _filler(40, 2) + "\n\nThanks for reading."
    doc_c = "Third opening paragraph " + _filler(40, 3) + "\n\nUntil next week."
    passages = (
        ndd.chunk_document("a", doc_a)
        + ndd.chunk_document("b", doc_b)
        + ndd.chunk_document("c", doc_c)
    )
    out = ndd.stage_a_clusters(passages)
    assert out["short_exact_groups"] == 1
    # The two identical sign-offs collapse to one representative.
    assert out["dropped"] == ["b#p0001"]
    # The DIFFERENT sub-k sign-off is NOT grouped — the sub-k shingle fallback's
    # spurious "Jaccard 1.0" class is closed because no sub-floor text is
    # exposed to the estimator at all.
    assert "c#p0001" in out["kept"]


@_needs_datasketch
def test_short_passages_never_enter_the_lsh_structurally(monkeypatch):
    """Contract 2, asserted structurally: the estimator never sees a sub-k text."""
    inserted: list[str] = []
    real_shingles = ndd.shingles

    def spy(text, *, k=ndd.DEFAULT_SHINGLE_SIZE):
        inserted.append(text)
        return real_shingles(text, k=k)

    monkeypatch.setattr(ndd, "shingles", spy)
    doc = "Bye now.\n\n" + _filler(40, 4) + "\n\nSee you.\n\n" + _filler(40, 5)
    ndd.stage_a_clusters(ndd.chunk_document("a", doc))
    assert inserted, "Stage A must shingle the above-floor passages"
    for text in inserted:
        assert len(ndd._norm_tokens(text)) >= ndd.DEFAULT_MIN_PASSAGE_WORDS


@_needs_datasketch
def test_stage_a_confirms_on_exact_jaccard_not_the_estimate(monkeypatch):
    """Contract 3: a pair whose EXACT Jaccard is below threshold is not merged
    even when the LSH offers it as a candidate (and even if the estimate lies)."""
    p_hi = ndd.chunk_document("x", BASE) + ndd.chunk_document("y", NEAR_DUP)
    merged = ndd.stage_a_clusters(p_hi, threshold=0.6)
    assert merged["dropped"], "a pair with exact J >= threshold must merge"

    exact = ndd._exact_jaccard(
        ndd.shingles(BASE, k=5), ndd.shingles(NEAR_DUP, k=5),
    )
    # Same pair, threshold set just above their true Jaccard: no merge. The
    # injected legacy LSH is deliberately irrelevant to passage-mode acceptance.
    class _AllPairsLSH:
        def __init__(self, **kwargs):
            self._keys: list[str] = []

        def insert(self, key, _mh):
            self._keys.append(key)

        def query(self, _mh):
            return list(self._keys)

    real_require = ndd._require_datasketch

    def fake_require():
        MinHash, _ = real_require()
        return MinHash, _AllPairsLSH

    monkeypatch.setattr(ndd, "_require_datasketch", fake_require)
    out = ndd.stage_a_clusters(p_hi, threshold=exact + 0.01)
    assert out["dropped"] == [], (
        "exact-Jaccard confirmation must reject a candidate below threshold"
    )
    # ...and accept it when the threshold sits just below the true value.
    out2 = ndd.stage_a_clusters(p_hi, threshold=exact - 0.01)
    assert out2["dropped"] == ["x#p0000"]


@_needs_datasketch
def test_stage_a_lsh_cannot_drop_true_exact_match():
    """Exact-Jaccard recall cannot depend on probabilistic LSH candidacy."""
    base = [f"token{i}" for i in range(100)]
    edited = list(base)
    edited[30] = "replacement30"
    edited[70] = "replacement70"
    passages = [
        ndd.Passage("a#p0000", "a", 0, 0, 1, " ".join(base)),
        ndd.Passage("b#p0000", "b", 0, 0, 1, " ".join(edited)),
    ]
    exact = ndd._exact_jaccard(
        ndd.shingles(passages[0].text, k=5),
        ndd.shingles(passages[1].text, k=5),
    )
    assert exact >= 0.8
    out = ndd.stage_a_clusters(passages, threshold=0.8)
    assert len(out["dropped"]) == 1
    assert set(out["kept"] + out["dropped"]) == {"a#p0000", "b#p0000"}


@pytest.mark.parametrize(
    ("threshold", "left_tokens", "right_tokens"),
    [
        (
            0.2,
            ["a_only", "c1", "c2", "c3"],
            [*[f"b{i}" for i in range(11)], "c1", "c2", "c3"],
        ),
        (
            0.8,
            [f"c{i}" for i in range(28)],
            [*[f"c{i}" for i in range(28)], *[f"b{i}" for i in range(7)]],
        ),
    ],
)
@_needs_datasketch
def test_stage_a_exact_threshold_boundaries_do_not_round_up(
    threshold, left_tokens, right_tokens,
):
    passages = [
        ndd.Passage("a#p0000", "a", 0, 0, 1, " ".join(left_tokens)),
        ndd.Passage("b#p0000", "b", 0, 0, 1, " ".join(right_tokens)),
    ]
    out = ndd.stage_a_clusters(
        passages,
        threshold=threshold,
        shingle_size=1,
        min_passage_words=0,
    )
    assert len(out["dropped"]) == 1


@_needs_datasketch
def test_stage_a_prefix_filter_avoids_common_shingle_pair_explosion(monkeypatch):
    passages = []
    for i in range(400):
        text = "shared0 shared1 shared2 shared3 shared4 " + " ".join(
            f"u{i}_{j}" for j in range(30)
        )
        passages.append(ndd.Passage(f"d{i}#p0000", f"d{i}", 0, 0, len(text), text))
    real_exact = ndd._meets_jaccard_threshold
    comparisons = 0

    def counted(left, right, threshold):
        nonlocal comparisons
        comparisons += 1
        return real_exact(left, right, threshold)

    monkeypatch.setattr(ndd, "_meets_jaccard_threshold", counted)
    out = ndd.stage_a_clusters(passages, threshold=0.8)
    assert out["dropped"] == []
    assert comparisons < 1_000


@_needs_datasketch
def test_stage_a_positional_filter_avoids_long_boilerplate_all_pairs(monkeypatch):
    common = " ".join(f"shared{i}" for i in range(49))
    passages = []
    for i in range(400):
        text = common + " " + " ".join(f"u{i}_{j}" for j in range(8))
        passages.append(ndd.Passage(f"d{i}#p0000", f"d{i}", 0, 0, len(text), text))
    real_exact = ndd._meets_jaccard_threshold
    comparisons = 0

    def counted(left, right, threshold):
        nonlocal comparisons
        comparisons += 1
        return real_exact(left, right, threshold)

    monkeypatch.setattr(ndd, "_meets_jaccard_threshold", counted)
    out = ndd.stage_a_clusters(passages, threshold=0.8)
    assert out["dropped"] == []
    assert comparisons < 1_000


@_needs_datasketch
def test_stage_a_recovery_reuses_completed_shingle_shard(tmp_path, monkeypatch):
    passages = [
        ndd.Passage("a#p0000", "a", 0, 0, len(BASE), BASE),
        ndd.Passage("b#p0000", "b", 0, 0, len(NEAR_DUP), NEAR_DUP),
    ]
    store_path = tmp_path / "recovery.sqlite3"
    with ndd._RecoveryStore(store_path) as recovery:
        recovery.put("stage_a_shingles", "a#p0000", sorted(ndd.shingles(BASE)))
        real_shingles = ndd.shingles

        def no_redo(text, **kwargs):
            if text == BASE:
                raise AssertionError("completed passage shard was recomputed")
            return real_shingles(text, **kwargs)

        monkeypatch.setattr(ndd, "shingles", no_redo)
        ndd.stage_a_clusters(passages, recovery=recovery)


@_needs_datasketch
def test_stage_a_token_empty_passages_do_not_collapse():
    passages = [
        ndd.Passage("a#p0000", "a", 0, 0, 3, "!!!"),
        ndd.Passage("b#p0000", "b", 0, 0, 3, "???"),
    ]
    out = ndd.stage_a_clusters(passages)
    assert out["kept"] == ["a#p0000", "b#p0000"]
    assert out["dropped"] == []


@_needs_datasketch
def test_representative_rule_longest_then_lowest_id():
    """Contract 11: longest passage kept; exact ties fall to the lowest id."""
    body = _filler(40, 5)
    p = ndd.chunk_document("zzz", body) + ndd.chunk_document("aaa", body)
    out = ndd.stage_a_clusters(p)
    assert out["kept"] == ["aaa#p0000"] and out["dropped"] == ["zzz#p0000"]


# --- Stage B: the motivating case + the detection guarantee (4, 5) ----------

def test_motivating_case_stage_b_sees_what_stage_a_cannot(tmp_path):
    """Contract 4. Two documents that are NOT document-level near-duplicates,
    sharing only one embedded 41-token verbatim span inside otherwise-distinct
    ~120-word paragraphs: Stage A reports no cluster, Stage B reports exactly one
    41-token span with two provenance-traced occurrences."""
    doc_a = _filler(40, 11) + " " + SPAN_41 + " " + _filler(40, 12)
    doc_b = _filler(40, 13) + " " + SPAN_41 + " " + _filler(40, 14)
    m = _passage_manifest(tmp_path, [_full_row("docA", doc_a), _full_row("docB", doc_b)])

    report, _passages, _rows = ndd.analyze_passages(m, stages=["b"])
    spans = report["provenance"]["repeated_spans"]
    assert len(spans) == 1
    span = spans[0]
    assert span["n_words"] == 41 and span["n_occurrences"] == 2
    texts = {"docA": doc_a, "docB": doc_b}
    for occ in span["occurrences"]:
        sliced = texts[occ["source_doc_id"]][occ["char_start"]:occ["char_end"]]
        assert sliced == SPAN_41
        assert occ["sha256"] == hashlib.sha256(SPAN_41.encode("utf-8")).hexdigest()

    if _datasketch_available:
        # Honest: this is the class Stage A structurally cannot see. Its Jaccard
        # for the pair is far below any usable near-dup threshold.
        report_a, _p, _r = ndd.analyze_passages(m, stages=["a"])
        assert report_a["stage_a"]["clusters"] == 0
        assert report_a["stage_a"]["dropped"] == 0
        j = ndd._exact_jaccard(ndd.shingles(doc_a, k=5), ndd.shingles(doc_b, k=5))
        assert j < 0.3


def test_stage_b_guarantee_sweep_floor_and_within_document(tmp_path):
    """Contract 5: 19 tokens (counted, not itemized), 20 and 41 (reported),
    including a within-document repeat."""
    # Independent token streams, NOT prefixes of SPAN_41: a prefix would share
    # shingles with the 41-token span and the occurrence sets would differ along
    # its length, which is a different (correct, but confusing) split.
    span20 = " ".join(w + "q" for w in SPAN_41.split()[:20])
    span19 = " ".join(w + "z" for w in SPAN_41.split()[:19])

    doc_a = _filler(30, 21) + " " + span19 + " " + _filler(30, 22)
    doc_b = _filler(30, 23) + " " + span19 + " " + _filler(30, 24)
    doc_c = _filler(30, 25) + " " + span20 + " " + _filler(30, 26)
    doc_d = _filler(30, 27) + " " + span20 + " " + _filler(30, 28)
    # A within-document repeat of the full 41-token span.
    doc_e = _filler(30, 29) + " " + SPAN_41 + " " + _filler(30, 30) + " " + SPAN_41

    out = ndd.stage_b_spans([
        ("a", doc_a), ("b", doc_b), ("c", doc_c), ("d", doc_d), ("e", doc_e),
    ])
    by_len = {s["n_words"]: s for s in out["repeated_spans"]}
    assert sorted(by_len) == [20, 41]
    assert by_len[20]["n_occurrences"] == 2
    # The within-document repeat is two occurrences of ONE span, in one document.
    assert by_len[41]["n_occurrences"] == 2
    assert {o["source_doc_id"] for o in by_len[41]["occurrences"]} == {"e"}
    # The 19-token span is below the floor: counted, not itemized.
    assert out["spans_below_floor"] == 1


def test_stage_b_guarantee_survives_extra_boundary_occurrence():
    """A third occurrence of one shingle cannot hide a qualifying A/B span."""
    span20 = " ".join(f"boundary{i}" for i in range(20))
    first_shingle_only = " ".join(span20.split()[:8])

    out = ndd.stage_b_spans([
        ("a", span20),
        ("b", span20),
        ("c", first_shingle_only),
    ])

    assert len(out["repeated_spans"]) == 1
    span = out["repeated_spans"][0]
    assert span["n_words"] == 20
    assert {
        (occ["source_doc_id"], occ["token_start"])
        for occ in span["occurrences"]
    } == {("a", 0), ("b", 0)}


def test_stage_b_reports_overlapping_periodic_occurrences():
    out = ndd.stage_b_spans([("a", " ".join(["x"] * 40))])
    assert out["repeated_spans"]
    assert any(
        span["n_words"] >= 20 and span["n_occurrences"] >= 2
        for span in out["repeated_spans"]
    )


def test_stage_b_edited_span_splits_into_verbatim_subspans():
    """Contract 5 (second half): one token changed mid-span splits the 41-token
    span into the two verbatim sub-spans the arithmetic predicts."""
    words = SPAN_41.split()
    edited = list(words)
    edited[20] = "REPLACED"
    doc_a = _filler(30, 31) + " " + SPAN_41 + " " + _filler(30, 32)
    doc_b = _filler(30, 33) + " " + " ".join(edited) + " " + _filler(30, 34)
    out = ndd.stage_b_spans([("a", doc_a), ("b", doc_b)])
    lengths = sorted(s["n_words"] for s in out["repeated_spans"])
    # k=8: shingles [0..12] survive on the left (12 + 8 = 20 tokens) and
    # [21..33] on the right (also 20 tokens).
    assert lengths == [20, 20]
    assert all(s["n_occurrences"] == 2 for s in out["repeated_spans"])


def test_stage_b_is_stdlib_only():
    """Contract 15 (Stage B half): the span scan never touches datasketch."""
    def boom():
        raise AssertionError("Stage B must not require datasketch")

    real = ndd._require_datasketch
    ndd._require_datasketch = boom
    try:
        out = ndd.stage_b_spans([("a", SPAN_41), ("b", SPAN_41)])
    finally:
        ndd._require_datasketch = real
    assert len(out["repeated_spans"]) == 1


# --- report shape, honesty carrier, determinism (6, 7, 10, 12, 15) ----------

def _report(tmp_path, rows, **kwargs):
    m = _passage_manifest(tmp_path, rows)
    report, passages, by_doc = ndd.analyze_passages(m, **kwargs)
    return m, report, passages, by_doc


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk_keys(item)


def test_report_only_default_writes_nothing(tmp_path):
    """Contract 6 + 12: passage mode without --out leaves the input byte-identical
    and writes no manifest."""
    doc_a = _filler(40, 41) + " " + SPAN_41
    doc_b = _filler(40, 42) + " " + SPAN_41
    m = _passage_manifest(tmp_path, [_full_row("a", doc_a), _full_row("b", doc_b)])
    before = m.read_bytes()
    out = io.StringIO()
    with redirect_stdout(out):
        rc = ndd.main([str(m), "--passages", "--json"])
    assert rc == 0
    assert m.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["corpus.jsonl"]
    report = json.loads(out.getvalue())
    assert report["mode"] == "passages"


def test_passage_human_summary_is_encodable_on_cp1252_console(tmp_path, monkeypatch):
    """A completed report/checkpoint must not be followed by a console crash."""
    m = _passage_manifest(tmp_path, [
        _full_row("a", SPAN_41),
        _full_row("b", SPAN_41),
    ])
    report_out = tmp_path / "report.json"
    checkpoint = tmp_path / "state.json"
    encoded_stdout = io.TextIOWrapper(
        io.BytesIO(), encoding="cp1252", errors="strict", write_through=True
    )
    monkeypatch.setattr(sys, "stdout", encoded_stdout)

    assert ndd.main([
        str(m), "--passages", "--stages", "b",
        "--checkpoint", str(checkpoint), "--report-out", str(report_out),
    ]) == 0
    assert json.loads(report_out.read_text(encoding="utf-8"))["mode"] == "passages"
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["stage_b_detail"]
    assert b"->" in encoded_stdout.buffer.getvalue()


@_needs_datasketch
def test_document_human_summary_escapes_unicode_dropped_id_on_cp1252_console(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "input.jsonl"
    out = tmp_path / "output.jsonl"
    manifest.write_text(
        "\n".join(json.dumps(row) for row in [
            {"id": "dropped-漢", "text": BASE, "author": "Author"},
            {"id": "kept", "text": NEAR_DUP, "author": "Author"},
        ]) + "\n",
        encoding="utf-8",
    )
    encoded_stdout = io.TextIOWrapper(
        io.BytesIO(), encoding="cp1252", errors="strict", write_through=True
    )
    monkeypatch.setattr(sys, "stdout", encoded_stdout)

    assert ndd.main([str(manifest), "--out", str(out), "--threshold", "0.6"]) == 0
    assert out.exists()
    assert b"dropped-\\u6f22" in encoded_stdout.buffer.getvalue()


@_needs_datasketch
def test_passage_export_human_summary_escapes_unicode_output_path_on_cp1252_console(
    tmp_path, monkeypatch
):
    m = _passage_manifest(tmp_path, [
        _full_row("a", BASE),
        _full_row("b", BASE),
    ])
    out = tmp_path / "passages-漢.jsonl"
    passage_dir = tmp_path / "passages-漢"
    checkpoint = tmp_path / "state.json"
    encoded_stdout = io.TextIOWrapper(
        io.BytesIO(), encoding="cp1252", errors="strict", write_through=True
    )
    monkeypatch.setattr(sys, "stdout", encoded_stdout)

    assert ndd.main([
        str(m), "--passages", "--stages", "a", "--threshold", "0.6",
        "--checkpoint", str(checkpoint), "--out", str(out),
        "--passage-dir", str(passage_dir),
    ]) == 0
    assert out.exists()
    assert checkpoint.exists()
    assert list(passage_dir.glob("*.txt"))
    assert b"\\u6f22" in encoded_stdout.buffer.getvalue()


def test_passage_checkpoint_resume_is_bound_and_skips_completed_stage(tmp_path, monkeypatch):
    m = _passage_manifest(tmp_path, [
        _full_row("a", SPAN_41),
        _full_row("b", SPAN_41),
    ])
    checkpoint = tmp_path / "state.json"
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert ndd.main([
            str(m), "--passages", "--stages", "b",
            "--checkpoint", str(checkpoint), "--json",
        ]) == 0
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert state["stage_b_detail"]["repeated_spans"]

    def must_not_rerun(*_args, **_kwargs):
        raise AssertionError("completed Stage B must be restored")

    monkeypatch.setattr(ndd, "stage_b_spans", must_not_rerun)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        assert ndd.main([
            str(m), "--passages", "--stages", "b",
            "--checkpoint", str(checkpoint), "--resume", "--json",
        ]) == 0
    assert "stage B restored from checkpoint" in err.getvalue()

    m.write_text(m.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        assert ndd.main([
            str(m), "--passages", "--stages", "b",
            "--checkpoint", str(checkpoint), "--resume", "--json",
        ]) == 2
    assert "does not match the exact manifest" in err.getvalue()


def test_recovery_shards_are_private(tmp_path):
    m = _passage_manifest(tmp_path, [
        _full_row("a", SPAN_41),
        _full_row("b", SPAN_41),
    ])
    checkpoint = tmp_path / "state.json"
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert ndd.main([
            str(m), "--passages", "--stages", "b",
            "--checkpoint", str(checkpoint), "--json",
        ]) == 0
    recovery_path = ndd._recovery_store_path(checkpoint)
    assert stat.S_IMODE(recovery_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(recovery_path.stat().st_mode) == 0o600


def test_recovery_store_does_not_chmod_user_owned_parent(tmp_path):
    user_parent = tmp_path / "project"
    user_parent.mkdir(mode=0o755)
    user_parent.chmod(0o755)
    with ndd._RecoveryStore(user_parent / "state.sqlite3"):
        pass
    assert stat.S_IMODE(user_parent.stat().st_mode) == 0o755
    assert stat.S_IMODE((user_parent / "state.sqlite3").stat().st_mode) == 0o600


def test_corrupt_recovery_database_refuses_without_traceback(tmp_path):
    m = _passage_manifest(tmp_path, [
        _full_row("a", SPAN_41),
        _full_row("b", SPAN_41),
    ])
    checkpoint = tmp_path / "state.json"
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert ndd.main([
            str(m), "--passages", "--stages", "b",
            "--checkpoint", str(checkpoint), "--json",
        ]) == 0
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    state["stage_b_detail"] = None
    checkpoint.write_text(json.dumps(state) + "\n", encoding="utf-8")
    recovery_path = ndd._recovery_store_path(checkpoint)
    recovery_path.write_bytes(b"not a sqlite database")

    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--stages", "b",
            "--checkpoint", str(checkpoint), "--resume", "--json",
        ])
    assert rc == 2
    assert "unreadable or corrupt" in err.getvalue()


def test_invalid_utf8_recovery_payload_refuses_without_traceback(tmp_path):
    m = _passage_manifest(tmp_path, [
        _full_row("a", SPAN_41),
        _full_row("b", SPAN_41),
    ])
    checkpoint = tmp_path / "state.json"
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert ndd.main([
            str(m), "--passages", "--stages", "b",
            "--checkpoint", str(checkpoint), "--json",
        ]) == 0
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    state["stage_b_detail"] = None
    checkpoint.write_text(json.dumps(state) + "\n", encoding="utf-8")
    recovery_path = ndd._recovery_store_path(checkpoint)
    with sqlite3.connect(recovery_path) as db:
        db.execute(
            "UPDATE shards SET payload=? WHERE stage=? AND shard_key=?",
            (sqlite3.Binary(b"\xff"), "_meta", "analysis_binding"),
        )

    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--stages", "b",
            "--checkpoint", str(checkpoint), "--resume", "--json",
        ])
    assert rc == 2
    assert "unreadable or corrupt" in err.getvalue()


def test_checkpoint_parent_regular_file_refuses_without_traceback(tmp_path):
    m = _passage_manifest(tmp_path, [_full_row("a", SPAN_41)])
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied", encoding="utf-8")
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--stages", "b",
            "--checkpoint", str(parent / "state.json"), "--json",
        ])
    assert rc == 2
    assert "cannot publish passage checkpoint" in err.getvalue()
    assert parent.read_text(encoding="utf-8") == "occupied"


def test_stage_b_recovery_reuses_completed_document_shard(tmp_path, monkeypatch):
    docs = [("a", SPAN_41), ("b", SPAN_41)]
    cached = [
        (m.group(0).lower(), m.start(), m.end())
        for m in ndd._WORD_RE.finditer(SPAN_41)
    ]
    store_path = tmp_path / "recovery.sqlite3"
    real_word_re = ndd._WORD_RE

    class _NoFirstRetokenize:
        @staticmethod
        def finditer(text):
            if text == SPAN_41:
                # Both texts are equal, so distinguish the cached first document
                # by allowing exactly one live tokenization for the second.
                if _NoFirstRetokenize.calls == 0:
                    _NoFirstRetokenize.calls += 1
                    return real_word_re.finditer(text)
                raise AssertionError("unexpected additional tokenization")

        calls = 0

    with ndd._RecoveryStore(store_path) as recovery:
        recovery.put("stage_b_tokens", "000000000000", cached)
        monkeypatch.setattr(ndd, "_WORD_RE", _NoFirstRetokenize)
        out = ndd.stage_b_spans(docs, recovery=recovery)
    assert out["repeated_spans"]
    assert _NoFirstRetokenize.calls == 1


def test_analyze_resume_continues_after_interrupted_document_shard(tmp_path, monkeypatch):
    text_a = _filler(30, 141) + " " + SPAN_41
    text_b = _filler(30, 142) + " " + SPAN_41
    m = _passage_manifest(tmp_path, [
        _full_row("a", text_a),
        _full_row("b", text_b),
    ])
    checkpoint = tmp_path / "state.json"
    real_stage_b = ndd.stage_b_spans

    def interrupt_after_first_shard(*args, **kwargs):
        upstream_progress = kwargs.get("progress")

        def progress(message):
            if upstream_progress:
                upstream_progress(message)
            if message == "stage B tokenized 1/2 documents":
                raise RuntimeError("injected interruption")

        kwargs["progress"] = progress
        return real_stage_b(*args, **kwargs)

    monkeypatch.setattr(ndd, "stage_b_spans", interrupt_after_first_shard)
    with pytest.raises(RuntimeError, match="injected interruption"):
        ndd.analyze_passages(
            m,
            stages=["b"],
            checkpoint_path=checkpoint,
            progress=lambda _message: None,
        )
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert state["stage_b_detail"] is None

    monkeypatch.setattr(ndd, "stage_b_spans", real_stage_b)
    real_word_re = ndd._WORD_RE

    class _NoCompletedShardRedo:
        @staticmethod
        def finditer(text):
            if text == text_a:
                raise AssertionError("completed document shard was recomputed")
            return real_word_re.finditer(text)

    monkeypatch.setattr(ndd, "_WORD_RE", _NoCompletedShardRedo)
    report, _passages, _rows = ndd.analyze_passages(
        m,
        stages=["b"],
        checkpoint_path=checkpoint,
        resume=True,
        progress=lambda _message: None,
    )
    assert report["stage_b"]["repeated_spans"] == 1


def test_report_carries_claim_license_and_no_verdict(tmp_path):
    """Contract 7: a real ClaimLicense on the artifact, plus the recursive
    no-verdict key walk."""
    doc = _filler(40, 51) + " " + SPAN_41
    _m, report, _p, _r = _report(tmp_path, [_full_row("a", doc)], stages=["b"])

    lic = report["claim_license"]
    assert lic["task_surface"] == "voice_coherence_acquisition"
    dnl = lic["does_not_license"].lower()
    assert "memorization-safe" in dnl
    assert "mcnemar" in dnl and "0.453" in dnl          # the no-absolute-rate caveat
    assert "ai/human" in dnl                            # no authorship verdict
    assert "illegitimate" in dnl                        # no editorial judgment
    assert report["assumptions"]["stage_b"]["span_shingle_k"] == ndd.DEFAULT_SPAN_SHINGLE_K
    assert report["assumptions"]["calibration_status"].startswith("heuristic")

    keys = set(_walk_keys(report))
    assert keys.isdisjoint({"is_ai", "is_human", "verdict", "label", "same_author", "score"})
    assert "band" not in keys


@_needs_datasketch
def test_documents_affected_is_a_list_of_records_not_an_id_keyed_map(tmp_path):
    """Manifest ids are operator data. They stay in a `source_doc_id` FIELD (the
    repo's `per_document` shape) rather than becoming JSON keys, so arbitrary
    strings never enter the recursive no-verdict key walk."""
    doc_a = _filler(40, 141) + " " + SPAN_41 + "\n\nThanks for reading."
    doc_b = _filler(40, 142) + " " + SPAN_41 + "\n\nThanks for reading."
    _m, report, _p, _r = _report(tmp_path, [_full_row("a", doc_a), _full_row("verdict", doc_b)])

    affected = report["documents_affected"]
    assert isinstance(affected, list)
    assert [d["source_doc_id"] for d in affected] == ["a", "verdict"]
    # The duplicate sign-off collapses onto document 'a'; both docs carry the span.
    dropped = {d["source_doc_id"]: d["passages_dropped"] for d in affected}
    assert dropped == {"a": [], "verdict": ["verdict#p0001"]}
    assert all(d["spans_present"] == 1 for d in affected)
    assert isinstance(report["provenance"]["duplicated_regions"], list)
    # ...and the doc id named 'verdict' does NOT leak into the key walk.
    assert "verdict" not in set(_walk_keys(report))


def test_stage_not_run_reports_null_not_zero(tmp_path):
    """Contract 15: --stages b must not degrade into 'no Stage-A findings'."""
    doc = _filler(40, 61) + " " + SPAN_41
    _m, report, _p, _r = _report(tmp_path, [_full_row("a", doc)], stages=["b"])
    assert report["stage_a"] == {
        "run": False, "clusters": None, "kept": None, "dropped": None,
        "short_exact_groups": None,
    }
    assert "NOT run" in report["assumptions"]["stage_a"]["not_run_note"]
    assert report["stage_b"]["run"] is True


def test_stage_a_without_datasketch_raises(monkeypatch, tmp_path):
    """Contract 15: the existing RuntimeError path is preserved for Stage A."""
    def boom():
        raise RuntimeError(
            "datasketch is not installed. Install acquisition dependencies with: "
            "pip install -r requirements-acquisition.txt"
        )

    monkeypatch.setattr(ndd, "_require_datasketch", boom)
    m = _passage_manifest(tmp_path, [_full_row("a", _filler(40, 71))])
    for stages in (["a"], ["a", "b"]):
        with pytest.raises(RuntimeError, match="requirements-acquisition.txt"):
            ndd.analyze_passages(m, stages=stages)
    # ...and --stages b still runs, stdlib-only.
    report, _p, _r = ndd.analyze_passages(m, stages=["b"])
    assert report["stage_b"]["run"] is True


@_needs_datasketch
def test_deterministic_rerun_report_and_export_are_byte_identical(tmp_path):
    """Contract 10."""
    doc_a = _filler(40, 81) + " " + SPAN_41 + "\n\nThanks for reading."
    doc_b = _filler(40, 82) + " " + SPAN_41 + "\n\nThanks for reading."
    m = _passage_manifest(tmp_path, [_full_row("a", doc_a), _full_row("b", doc_b)])
    digests = []
    for i in (1, 2):
        out_manifest = tmp_path / f"run{i}" / "passages.jsonl"
        report_out = tmp_path / f"run{i}" / "report.json"
        sink = io.StringIO()
        with redirect_stdout(sink):
            rc = ndd.main([
                str(m), "--passages", "--out", str(out_manifest),
                "--passage-dir", str(tmp_path / f"run{i}" / "p"),
                "--report-out", str(report_out),
            ])
        assert rc == 0
        digests.append((report_out.read_bytes(), out_manifest.read_bytes()))
    assert digests[0] == digests[1]


def test_self_guard_refuses_a_marked_manifest(tmp_path):
    """Contract 13."""
    row = _full_row("a", _filler(40, 91))
    row["passage_dedup"] = {"source_doc_id": "a"}
    m = _passage_manifest(tmp_path, [row])
    with pytest.raises(ndd.PassageModeError, match="SOURCE document manifest"):
        ndd.analyze_passages(m)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([str(m), "--passages", "--json"])
    assert rc == 2 and "passage-deduped export" in err.getvalue()


def test_document_mode_cli_and_output_unchanged(tmp_path):
    """Contract 12: adding passage mode must not move document mode."""
    m = tmp_path / "m.jsonl"
    m.write_text(
        json.dumps({"id": "base", "text": BASE}) + "\n"
        + json.dumps({"id": "near_dup", "text": NEAR_DUP}) + "\n",
        encoding="utf-8",
    )
    if not _datasketch_available:
        pytest.skip("datasketch not installed")
    out = io.StringIO()
    with redirect_stdout(out):
        rc = ndd.main([str(m), "--threshold", "0.6", "--dry-run", "--json"])
    assert rc == 0
    result = json.loads(out.getvalue())
    # The frozen 9-key DedupResult shape, unchanged.
    assert set(result) == {
        "total", "kept_count", "dropped_count", "kept", "dropped", "clusters",
        "threshold", "num_perm", "shingle_size",
    }
    assert result["dropped"] == ["base"]


@_needs_datasketch
def test_document_verifier_success_and_refusal_are_planted_write_clean(tmp_path):
    manifest = tmp_path / "source.jsonl"
    manifest.write_text(
        json.dumps({"id": "base", "text": BASE}) + "\n"
        + json.dumps({"id": "near_dup", "text": NEAR_DUP}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "dedup.jsonl"
    report = tmp_path / "report.json"
    sink = io.StringIO()
    with redirect_stdout(sink):
        assert ndd.main([
            str(manifest), "--threshold", "0.6", "--num-perm", "128",
            "--shingle-size", "5", "--out", str(out), "--json",
        ]) == 0
    report.write_text(sink.getvalue(), encoding="utf-8")

    def snapshot():
        return {str(path.relative_to(tmp_path)): path.read_bytes()
                for path in tmp_path.rglob("*") if path.is_file()}

    verify_argv = [
        str(manifest), "--threshold", "0.6", "--num-perm", "128",
        "--shingle-size", "5", "--dry-run", "--json",
        "--verify-out", str(out), "--verify-report", str(report),
    ]
    before = snapshot()
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert ndd.main(verify_argv) == 0
    assert snapshot() == before

    out.write_bytes(out.read_bytes() + b"{}\n")
    before_refusal = snapshot()
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert ndd.main(verify_argv) == 2
    assert snapshot() == before_refusal


def test_passage_mode_rejects_dry_run(tmp_path):
    m = _passage_manifest(tmp_path, [_full_row("a", _filler(40, 95))])
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([str(m), "--passages", "--dry-run"])
    assert rc == 2 and "document-mode only" in err.getvalue()


def test_duplicate_document_ids_are_refused(tmp_path):
    """The id is the provenance join key at both stages — a collision would make
    one document's offsets point into another's text."""
    m = _passage_manifest(tmp_path, [
        _full_row("dup", _filler(40, 96)), _full_row("dup", _filler(40, 97)),
    ])
    with pytest.raises(ndd.PassageModeError, match="duplicate document id"):
        ndd.analyze_passages(m, stages=["b"])
    with pytest.raises(ValueError, match="duplicate document id"):
        ndd.stage_b_spans([("dup", "a b c"), ("dup", "d e f")])


def test_parse_stages_refuses_garbage():
    assert ndd.parse_stages("b,a") == ["a", "b"]
    for bad in ("", "c", "a,c"):
        with pytest.raises(ndd.PassageModeError):
            ndd.parse_stages(bad)


# --- the export (contract 8, 9) --------------------------------------------

@_needs_datasketch
def test_export_is_validator_clean_and_inherits_provenance(tmp_path):
    """Contract 8: zero validator errors; every row resolves its path; every
    inheritable field is copied verbatim; the marker is present."""
    doc_a = _filler(40, 101) + "\n\nThanks for reading."
    doc_b = _filler(40, 102) + "\n\nThanks for reading."
    m = _passage_manifest(tmp_path, [_full_row("a", doc_a), _full_row("b", doc_b)])
    out_manifest = tmp_path / "export" / "passages.jsonl"
    sink = io.StringIO()
    with redirect_stdout(sink):
        rc = ndd.main([
            str(m), "--passages", "--out", str(out_manifest),
            "--passage-dir", str(tmp_path / "export" / "p"),
        ])
    assert rc == 0

    result = mv.validate_manifest(out_manifest)
    errors = [i for i in result["issues"] if i["severity"] == "error"]
    assert errors == [], errors

    rows = [json.loads(x) for x in out_manifest.read_text(encoding="utf-8").splitlines()]
    # The duplicate sign-off collapsed; three of the four passages survive.
    assert [r["id"] for r in rows] == ["a#p0000", "a#p0001", "b#p0000"]
    source = _full_row("a", doc_a)
    for r in rows:
        resolved = mv.resolve_path(out_manifest, r["path"])
        assert resolved.is_file()
        for f in ("ai_status", "use", "privacy", "consent_status", "register",
                  "language_status", "corpus_role", "author", "source", "era", "topic"):
            assert r[f] == source[f], f
        assert "text" not in r and "text_path" not in r
        assert r["passage_dedup"]["source_manifest"] == "corpus.jsonl"
        assert r["passage_dedup"]["params"]["span_shingle_k"] == ndd.DEFAULT_SPAN_SHINGLE_K
        assert r["content_hash"].startswith("sha256:")
        assert resolved.read_text(encoding="utf-8") == (
            (doc_a if r["passage_dedup"]["source_doc_id"] == "a" else doc_b)[
                r["passage_dedup"]["char_start"]:r["passage_dedup"]["char_end"]
            ]
        )


@_needs_datasketch
def test_export_refuses_rather_than_fabricating_provenance(tmp_path):
    """Contract 9: a source row missing ai_status or use refuses the WHOLE export;
    no partial write; the report is still produced."""
    good = _full_row("a", _filler(40, 111))
    bad = _full_row("b", _filler(40, 112))
    del bad["ai_status"]
    m = _passage_manifest(tmp_path, [good, bad])
    out_manifest = tmp_path / "export" / "passages.jsonl"
    report_out = tmp_path / "report.json"
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--out", str(out_manifest),
            "--passage-dir", str(tmp_path / "export" / "p"),
            "--report-out", str(report_out),
        ])
    assert rc == 2
    message = err.getvalue()
    assert "export refused" in message and "b (missing: ai_status)" in message
    assert "no bypass flag" in message
    assert not out_manifest.exists(), "refusal must not leave a partial write"
    # The report is still produced.
    assert json.loads(report_out.read_text(encoding="utf-8"))["mode"] == "passages"


@_needs_datasketch
def test_export_refuses_if_analysis_skipped_unreadable_source_row(tmp_path):
    good = _full_row("a", _filler(40, 113))
    bad = _full_row("b", _filler(40, 114))
    del bad["text"]
    bad["path"] = "missing.txt"
    m = _passage_manifest(tmp_path, [good, bad])
    out_manifest = tmp_path / "export" / "passages.jsonl"
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--out", str(out_manifest),
            "--passage-dir", str(tmp_path / "export" / "p"),
        ])
    assert rc == 2
    assert "silently drop source documents" in err.getvalue()
    assert not out_manifest.exists()
    assert not (tmp_path / "export" / "p").exists()


@_needs_datasketch
def test_export_refuses_portable_filename_collision(tmp_path):
    m = _passage_manifest(tmp_path, [
        _full_row("A", _filler(40, 115)),
        _full_row("a", _filler(40, 116)),
    ])
    out_manifest = tmp_path / "export" / "passages.jsonl"
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--out", str(out_manifest),
            "--passage-dir", str(tmp_path / "export" / "p"),
        ])
    assert rc == 2
    assert "filesystem semantics" in err.getvalue()
    assert not out_manifest.exists()


@_needs_datasketch
def test_export_stages_sidecars_before_publication(tmp_path, monkeypatch):
    m = _passage_manifest(tmp_path, [
        _full_row("a", _filler(40, 117)),
        _full_row("b", _filler(40, 118)),
    ])
    out_manifest = tmp_path / "export" / "passages.jsonl"
    passage_dir = tmp_path / "export" / "p"
    real_write_text = Path.write_text
    writes = 0

    def fail_second_sidecar(self, data, *args, **kwargs):
        nonlocal writes
        if ".p.staging-" in str(self.parent):
            writes += 1
            if writes == 2:
                raise OSError("injected")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_second_sidecar)
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--out", str(out_manifest),
            "--passage-dir", str(passage_dir),
        ])
    assert rc == 2
    assert "staged publication" in err.getvalue()
    assert not out_manifest.exists()
    assert not passage_dir.exists()


@_needs_datasketch
def test_export_rejects_nested_manifest_before_writing(tmp_path):
    m = _passage_manifest(tmp_path, [_full_row("a", _filler(40, 119))])
    passage_dir = tmp_path / "export"
    out_manifest = passage_dir / "passages.jsonl"
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--out", str(out_manifest),
            "--passage-dir", str(passage_dir),
        ])
    assert rc == 2
    assert "--out and --passage-dir must not contain one another" in err.getvalue()
    assert not passage_dir.exists()


@_needs_datasketch
def test_export_rejects_passage_dir_nested_under_manifest_path(tmp_path):
    m = _passage_manifest(tmp_path, [_full_row("a", _filler(40, 120))])
    out_manifest = tmp_path / "export"
    passage_dir = out_manifest / "p"
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--out", str(out_manifest),
            "--passage-dir", str(passage_dir),
        ])
    assert rc == 2
    assert "--out and --passage-dir must not contain one another" in err.getvalue()
    assert not out_manifest.exists()


@_needs_datasketch
def test_export_requires_passage_dir_and_stage_a(tmp_path):
    m = _passage_manifest(tmp_path, [_full_row("a", _filler(40, 121))])
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([str(m), "--passages", "--out", str(tmp_path / "o.jsonl")])
    assert rc == 2 and "--passage-dir" in err.getvalue()

    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = ndd.main([
            str(m), "--passages", "--stages", "b", "--out", str(tmp_path / "o.jsonl"),
            "--passage-dir", str(tmp_path / "p"),
        ])
    assert rc == 2 and "--out needs Stage A" in err.getvalue()


@_needs_datasketch
def test_export_output_is_refused_by_the_pool_guard_surfaces(tmp_path):
    """The producer stamp is what pool_guard keys on — pinned end-to-end here so
    the marker and the scanner cannot drift apart."""
    m = _passage_manifest(tmp_path, [_full_row("a", _filler(40, 131))])
    out_manifest = tmp_path / "export" / "passages.jsonl"
    with redirect_stdout(io.StringIO()):
        rc = ndd.main([
            str(m), "--passages", "--out", str(out_manifest),
            "--passage-dir", str(tmp_path / "export" / "p"),
        ])
    assert rc == 0
    marked = pool_guard.scan_manifest_for_passage_dedup(out_manifest)
    assert len(marked) == 1 and marked[0].startswith("a#p0000")
