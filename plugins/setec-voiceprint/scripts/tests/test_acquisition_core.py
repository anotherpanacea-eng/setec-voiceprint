#!/usr/bin/env python3
"""Tests for acquisition_core helpers (stdlib only — no acquisition network deps,
so these run in core CI where bs4/requests are absent)."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquisition_core as ac  # type: ignore  # noqa: E402


def test_era_from_date_boundaries():
    assert ac.era_from_date(dt.date(2015, 6, 1)) == "pre_chatgpt"
    assert ac.era_from_date(dt.date(2022, 10, 31)) == "pre_chatgpt"
    assert ac.era_from_date(dt.date(2023, 1, 1)) == "pre_ai_widespread"
    assert ac.era_from_date(dt.date(2024, 6, 30)) == "pre_ai_widespread"
    assert ac.era_from_date(dt.date(2024, 7, 1)) == "post_ai_widespread"
    assert ac.era_from_date(None) == "undated"


def test_stable_redaction_map_reuses_lowest_gap_and_persists(tmp_path):
    path = tmp_path / "contact_map.json"
    path.write_text(
        json.dumps({"raw-a": "contact_03", "old": "contact_01"}),
        encoding="utf-8",
    )
    mapping = ac.StableRedactionMap(path, label_prefix="contact")
    mapping.ensure_all(["raw-z", "raw-b"])
    assert mapping.stable_id("raw-a") == "contact_03"
    assert mapping.stable_id("raw-b") == "contact_02"
    assert mapping.stable_id("raw-z") == "contact_04"
    mapping.save()
    reloaded = ac.StableRedactionMap(path, label_prefix="contact")
    assert reloaded.stable_id("raw-b") == "contact_02"
    assert json.loads(path.read_text(encoding="utf-8"))["raw-z"] == "contact_04"


def test_stable_redaction_map_normalizes_and_preserves_high_water(tmp_path):
    path = tmp_path / "recipient_map.json"
    path.write_text(
        json.dumps({"Alice@Example.test": "recipient_03"}),
        encoding="utf-8",
    )
    mapping = ac.StableRedactionMap(
        path,
        label_prefix="recipient",
        normalize_key=lambda value: value.strip().lower(),
        display_names={"alice@example.test": "trusted_alias"},
        reuse_gaps=False,
    )
    assert mapping.display("ALICE@EXAMPLE.TEST") == "trusted_alias"
    assert mapping.stable_id("bob@example.test") == "recipient_04"
    mapping.save()
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "alice@example.test": "recipient_03",
        "bob@example.test": "recipient_04",
    }


def test_stable_redaction_map_rejects_duplicate_normalized_keys(tmp_path):
    path = tmp_path / "recipient_map.json"
    path.write_text(
        json.dumps(
            {
                "Alice@Example.test": "recipient_01",
                "alice@example.test": "recipient_02",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate normalized keys"):
        ac.StableRedactionMap(
            path,
            label_prefix="recipient",
            normalize_key=lambda value: value.strip().lower(),
        )


def _piece(text: str, title: str = "A Long Shared Book Title",
           date: dt.date | None = dt.date(2020, 1, 1)) -> "ac.AcquiredPiece":
    return ac.AcquiredPiece(
        title=title, author="Author", persona="p", register="literary_fiction",
        date_written=date, source_url="u", cleaned_text=text,
        raw_byte_length=len(text.encode()), preprocessing_meta={},
        acquired_via="test", consent_status="fair_use_research", era="undated",
        register_match="high", topic_match="medium", impostor_for=[], notes="",
    )


def test_write_piece_disambiguates_stem_collision(tmp_path):
    # Two different-content pieces with the same title+date → same base stem.
    p1, p2 = _piece("alpha " * 200), _piece("beta " * 200)
    t1, _ = ac.write_piece(p1, output_dir=tmp_path, scraper_version="t")
    t2, _ = ac.write_piece(p2, output_dir=tmp_path, scraper_version="t")
    assert t1 != t2
    assert len(list(tmp_path.glob("*.txt"))) == 2
    assert t1.read_text().startswith("alpha")
    assert t2.read_text().startswith("beta")  # the first file is NOT clobbered


def test_write_piece_preserves_exact_hashed_utf8_bytes(tmp_path):
    text = "first line\nsecond line\n\ngenuine paragraph with caf\u00e9"
    piece = _piece(text)
    text_path, _ = ac.write_piece(
        piece, output_dir=tmp_path, scraper_version="t",
    )
    assert text_path.read_bytes() == text.encode("utf-8")
    assert ac.compute_content_hash(text_path.read_bytes().decode("utf-8")) == (
        piece.content_hash
    )


def test_disambiguated_stem_is_filesystem_safe(tmp_path):
    p1, p2 = _piece("x " * 200), _piece("y " * 200)
    ac.write_piece(p1, output_dir=tmp_path, scraper_version="t")
    t2, _ = ac.write_piece(p2, output_dir=tmp_path, scraper_version="t")
    assert ":" not in t2.name  # Windows-safe; no 'sha256:' colon
    assert t2.stem.endswith(p2.content_hash.split(":")[-1][:8])


def test_compose_impostor_shape_is_the_default(tmp_path):
    # Backward-compat guard: a no-bucket call still emits corpus_role
    # impostor plus all five impostor-only fields, unchanged.
    p = _piece("alpha " * 200)
    tp = tmp_path / "x.txt"
    tp.write_text("alpha")
    e = ac.compose_manifest_entry(
        p, text_path=tp, manifest_relative_to=tmp_path,
    )
    assert e["corpus_role"] == "impostor"
    assert e["use"] == ["voice_impostor"]
    assert e["split"] == "baseline"
    for fld in ("impostor_for", "register_match", "topic_match",
                "consent_status", "era", "acquired_via"):
        assert fld in e


def test_compose_corpus_role_none_omits_role_and_impostor_fields(tmp_path):
    # The test/drift bucket: corpus_role=None drops the role field AND the
    # five impostor-only fields; use/split/ai_status come from kwargs.
    p = _piece("beta " * 200)
    tp = tmp_path / "y.txt"
    tp.write_text("beta")
    e = ac.compose_manifest_entry(
        p, text_path=tp, manifest_relative_to=tmp_path,
        corpus_role=None, use=["test_set"], split="test", ai_status="mixed",
        extra={"notes": {"composite_states": ["ai_assisted"]}},
    )
    assert "corpus_role" not in e
    assert e["use"] == ["test_set"]
    assert e["split"] == "test"
    assert e["ai_status"] == "mixed"
    assert e["notes"] == {"composite_states": ["ai_assisted"]}
    for fld in ("impostor_for", "register_match", "topic_match",
                "consent_status", "era", "acquired_via"):
        assert fld not in e


def test_no_collision_keeps_base_stem(tmp_path):
    p = _piece("z " * 200)
    t, _ = ac.write_piece(p, output_dir=tmp_path, scraper_version="t")
    assert t.stem == p.filename_stem()  # unchanged when there is no clash


def test_content_hash_dedup_recomputes_from_current_bytes(tmp_path):
    """Integrity regression: the dedup gate must re-hash the paired
    .txt's *actual* current bytes, not trust the sidecar's recorded
    ``content_hash``.

    Editing a .txt in place (without touching its .meta.json) leaves a
    stale recorded hash that nothing re-verifies. Re-running
    acquisition on the *original* source recomputes hash(original)=H1,
    which still matches the stale recorded H1 — so a recorded-hash-
    trusting gate would drop the original as "already present" even
    though the corpus no longer holds those bytes. Recompute-not-trust
    catches it.
    """
    original = "the original body text " * 50
    piece = _piece(original)
    txt_path, meta_path = ac.write_piece(
        piece, output_dir=tmp_path, scraper_version="t",
    )
    h1 = piece.content_hash
    # Legitimate case: unchanged bytes → correctly "already present".
    assert ac.content_hash_already_present(h1, tmp_path) == meta_path
    # Edit the .txt in place; leave the .meta.json (and its recorded
    # content_hash) untouched — the stale-sidecar failure mode.
    txt_path.write_text(
        "tampered replacement body " * 50, encoding="utf-8",
    )
    # Re-acquiring the ORIGINAL source recomputes H1. The corpus no
    # longer holds H1's bytes, so the gate must NOT drop it as present.
    assert ac.content_hash_already_present(h1, tmp_path) is None


def test_content_hash_dedup_still_matches_unchanged_bytes(tmp_path):
    """Happy path preserved: a piece whose paired .txt is on disk and
    unmodified is still correctly reported as already present (the
    recompute equals the recorded hash equals the incoming hash)."""
    # Include LF so this catches Windows universal-newline conversion:
    # dedupe must hash the exact stored UTF-8 bytes, just as write_piece does.
    piece = _piece(("stable corpus body\n" * 40) + "final line")
    _, meta_path = ac.write_piece(
        piece, output_dir=tmp_path, scraper_version="t",
    )
    assert (
        ac.content_hash_already_present(piece.content_hash, tmp_path)
        == meta_path
    )


def test_content_hash_dedup_recognizes_legacy_windows_crlf_bytes(tmp_path):
    """Old Windows writes translated hashed LF text to CRLF on disk."""
    logical_text = "legacy first line\nlegacy second line\n"
    piece = _piece(logical_text)
    stem = piece.filename_stem()
    text_path = tmp_path / f"{stem}.txt"
    meta_path = tmp_path / f"{stem}.meta.json"
    text_path.write_bytes(logical_text.replace("\n", "\r\n").encode("utf-8"))
    meta_path.write_text(
        json.dumps({"content_hash": piece.content_hash}), encoding="utf-8",
    )

    assert (
        ac.content_hash_already_present(piece.content_hash, tmp_path) == meta_path
    )


@pytest.mark.parametrize(
    "stored_bytes",
    (
        b"legacy first line\r\nlegacy second line\n",
        b"legacy first line\rlegacy second line",
        b"legacy first line\r\r\nlegacy second line\r\r\n",
    ),
    ids=("mixed-lf-crlf", "lone-cr", "crcrlf"),
)
def test_content_hash_dedup_rejects_noncanonical_legacy_newlines(
    tmp_path, stored_bytes,
):
    logical_text = "legacy first line\nlegacy second line\n"
    piece = _piece(logical_text)
    stem = piece.filename_stem()
    (tmp_path / f"{stem}.txt").write_bytes(stored_bytes)
    (tmp_path / f"{stem}.meta.json").write_text(
        json.dumps({"content_hash": piece.content_hash}), encoding="utf-8",
    )

    assert ac.content_hash_already_present(piece.content_hash, tmp_path) is None


def test_content_hash_dedup_preserves_exact_new_crlf_bytes(tmp_path):
    """New writer hashes and verifies intentional CRLF without normalization."""
    piece = _piece("intentional first line\r\nintentional second line\r\n")
    text_path, meta_path = ac.write_piece(
        piece, output_dir=tmp_path, scraper_version="t",
    )
    assert text_path.read_bytes() == piece.cleaned_text.encode("utf-8")
    assert (
        ac.content_hash_already_present(piece.content_hash, tmp_path) == meta_path
    )


def test_content_hash_dedup_missing_txt_is_not_a_duplicate(tmp_path):
    """If the paired .txt is gone (only the sidecar remains), the
    recorded content is not actually on disk — fail open (re-acquire)
    rather than silently drop the incoming piece as a duplicate."""
    piece = _piece("body that will lose its txt " * 30)
    txt_path, _ = ac.write_piece(
        piece, output_dir=tmp_path, scraper_version="t",
    )
    txt_path.unlink()
    assert (
        ac.content_hash_already_present(piece.content_hash, tmp_path)
        is None
    )
