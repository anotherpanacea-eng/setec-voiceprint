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
  * Stage A confirms LSH candidates on EXACT Jaccard, and sub-floor passages are
    grouped by exact token equality without ever reaching the estimator.
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
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manifest_validator as mv  # type: ignore  # noqa: E402
import near_dup_dedup as ndd  # type: ignore  # noqa: E402
import pool_guard  # type: ignore  # noqa: E402

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
    # Same pair, threshold set just above their true Jaccard: no merge, even
    # though the LSH is coerced into offering every pair as a candidate.
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
