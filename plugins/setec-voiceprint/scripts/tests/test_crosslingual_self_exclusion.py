"""Self-exclusion regression: a content-duplicate (or same-path copy) of the target planted in the
baseline corpus must be dropped from the baseline BEFORE the distance is computed. Otherwise the
target pools its own char-n-gram profile into its own baseline centroid, deflating `delta`/cosine
toward zero (a false "on-voice" result).

Sibling of the Codex self-exclusion sweep (cross_doc_novelty_profile #274 / originality_audit #278 /
rank_turbulence_audit #280). The fingerprint here is matcher-aligned: crosslingual builds char
n-grams over ``_normalize`` (NFC + whitespace-collapse + strip, punctuation/case PRESERVED), so the
self-exclusion fingerprint is sha256 over ``_normalize`` — two texts equal under it produce identical
char n-grams (matcher-equivalent) and are self-excluded; a punctuation/case variant is a genuinely
different profile to this surface and is correctly KEPT.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import crosslingual_voice_distance as cvd  # noqa: E402


def _text(seed: str, n: int = 600) -> str:
    # >= LENGTH_FLOOR_WORDS words so the surface actually produces a distance.
    return " ".join(f"{seed}{i % 37}" for i in range(n))


TARGET = _text("alpha")
OTHER = _text("bravo")


def test_content_duplicate_at_other_path_is_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "sneaky_copy.txt").write_text(TARGET, encoding="utf-8")  # a copy of the target
    fp = cvd._content_fingerprint(TARGET)
    texts, loaded, words, self_excluded = cvd._load_baseline(str(bdir), target_fingerprint=fp)
    names = {p.name for p in loaded}
    assert "sneaky_copy.txt" not in names  # the target's own copy is dropped
    assert "genuine.txt" in names          # the genuinely-different doc is kept
    assert self_excluded == 1


def test_target_inside_baseline_dir_excluded_by_path(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    tgt = bdir / "target.txt"
    tgt.write_text(TARGET, encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    texts, loaded, words, self_excluded = cvd._load_baseline(
        str(bdir), target_resolved=tgt.resolve(), target_fingerprint=cvd._content_fingerprint(TARGET))
    names = {p.name for p in loaded}
    assert "target.txt" not in names
    assert "genuine.txt" in names
    assert self_excluded == 1


def test_whitespace_variant_is_matcher_equivalent_and_excluded(tmp_path):
    # _normalize collapses runs of whitespace; a whitespace-only variant yields identical char
    # n-grams (matcher-equivalent) -> must be self-excluded (fail-closed against a reformatted copy).
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "ws_copy.txt").write_text(TARGET.replace(" ", "   \n "), encoding="utf-8")
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    texts, loaded, words, self_excluded = cvd._load_baseline(
        str(bdir), target_fingerprint=cvd._content_fingerprint(TARGET))
    names = {p.name for p in loaded}
    assert "ws_copy.txt" not in names
    assert self_excluded == 1


def test_distinct_docs_not_over_excluded(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "a.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "b.txt").write_text(_text("charlie"), encoding="utf-8")
    texts, loaded, words, self_excluded = cvd._load_baseline(
        str(bdir), target_fingerprint=cvd._content_fingerprint(TARGET))
    assert self_excluded == 0
    assert len(loaded) == 2


def test_end_to_end_warns_on_self_exclusion(tmp_path):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "genuine.txt").write_text(OTHER, encoding="utf-8")
    (bdir / "copy.txt").write_text(TARGET, encoding="utf-8")
    tgt = tmp_path / "target.txt"
    tgt.write_text(TARGET, encoding="utf-8")
    out = tmp_path / "out.json"
    rc = cvd.main([str(tgt), "--baseline-dir", str(bdir), "--lang", "en", "--json", "--out", str(out)])
    assert rc == 0
    import json
    payload = json.loads(out.read_text())
    warns = payload.get("warnings") or []
    assert any("self-exclusion" in w.lower() for w in warns)


def test_existing_signature_returns_four_tuple(tmp_path):
    # backward-compat: no target params -> nothing excluded, still a valid load.
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "good.txt").write_text(OTHER, encoding="utf-8")
    texts, loaded, words, self_excluded = cvd._load_baseline(str(bdir))
    assert len(texts) == 1 and words > 0 and self_excluded == 0
