#!/usr/bin/env python3
"""Regression tests for corpus hygiene checking."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from check_corpus import (
    LARGE_MANIFEST_WARN_THRESHOLD,
    check_corpus_paths,
    paths_from_manifest,
    warn_if_large_manifest,
)


FIXTURE_DIR = ROOT / "test_data" / "preprocessing"
CONTAMINATED = FIXTURE_DIR / "css_contaminated_fixture.md"
CLEAN = FIXTURE_DIR / "css_contaminated_fixture_clean.md"


def test_contaminated_css_fixture_fails_default_gate() -> None:
    result = check_corpus_paths([CONTAMINATED])

    assert result["status"] == "fail"
    assert result["n_fail"] == 1
    assert result["dominant_rule"] == "css_rule_block"
    assert result["files"][0]["dominant_rule"] == "css_rule_block"
    assert result["files"][0]["strip_ratio"] >= 0.05


def test_clean_fixture_passes_default_gate() -> None:
    result = check_corpus_paths([CLEAN])

    assert result["status"] == "clean"
    assert result["n_clean"] == 1
    assert result["n_fail"] == 0


def test_manifest_filter_loads_paths_for_checking(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps({
            "id": "contaminated",
            "path": str(CONTAMINATED),
            "ai_status": "pre_ai_human",
            "use": ["baseline"],
            "privacy": "shareable",
            "split": "baseline",
        })
        + "\n"
        + json.dumps({
            "id": "clean",
            "path": str(CLEAN),
            "ai_status": "pre_ai_human",
            "use": ["validation"],
            "privacy": "shareable",
            "split": "test",
        })
        + "\n",
        encoding="utf-8",
    )

    paths = paths_from_manifest(manifest, "use=baseline")
    assert paths == [CONTAMINATED]
    result = check_corpus_paths(paths)
    assert result["status"] == "fail"


# ----- warn_if_large_manifest ----------------------------------


class _CaptureStream:
    """Tiny stand-in for sys.stderr that records writes for
    assertion."""

    def __init__(self) -> None:
        self._chunks: list[str] = []

    def write(self, s: str) -> None:
        self._chunks.append(s)

    def flush(self) -> None:
        return None

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def test_warn_below_threshold_no_output() -> None:
    out = _CaptureStream()
    fired = warn_if_large_manifest(
        n_files=100,
        manifest="path/to/manifest.jsonl",
        threshold=1_000_000,
        out=out,
    )
    assert fired is False
    assert out.text == ""


def test_warn_above_threshold_with_manifest_prints_guidance() -> None:
    out = _CaptureStream()
    fired = warn_if_large_manifest(
        n_files=5_000_000,
        manifest="path/to/raid_manifest.jsonl",
        threshold=1_000_000,
        out=out,
    )
    assert fired is True
    text = out.text
    assert "5,000,000" in text
    # The warning must surface the sharded invocation so the
    # operator can copy/paste; without that, the warning is
    # noise rather than discoverability.
    assert "shard_runner" in text
    assert "--task corpus_hygiene" in text
    assert "path/to/raid_manifest.jsonl" in text
    # And it must point at the runbook for the long-form
    # walkthrough.
    assert "RUNBOOK_corpus_hygiene_sharded.md" in text


def test_warn_above_threshold_without_manifest_no_output() -> None:
    """When the operator passed --path or --dir rather than
    --manifest, the sharded path isn't directly applicable (it
    requires a manifest input). Suppress the warning to avoid
    pointing at an inappropriate alternative."""
    out = _CaptureStream()
    fired = warn_if_large_manifest(
        n_files=5_000_000,
        manifest=None,
        threshold=1_000_000,
        out=out,
    )
    assert fired is False
    assert out.text == ""


def test_warn_default_threshold_is_a_million() -> None:
    """The threshold default tracks the practical crossover
    point: MAGE-scale (~436K) doesn't warrant the sharded
    ceremony; an order of magnitude above that is where the
    trade-off flips. Pinning the constant in a test makes the
    decision explicit and reviewable."""
    assert LARGE_MANIFEST_WARN_THRESHOLD == 1_000_000


def test_warn_quotes_manifest_path_with_spaces() -> None:
    """Codex P2 on PR #52: paths in this workspace commonly
    contain spaces (today's runtime path is under
    ``C:\\Users\\Joshua\\Documents\\Claude Cowork Working
    Folder\\...``). The copy-pasteable recipe in the warning
    must shell-quote the manifest path so the command actually
    works for the operator."""
    out = _CaptureStream()
    path_with_spaces = "/some/path with spaces/manifest.jsonl"
    fired = warn_if_large_manifest(
        n_files=5_000_000,
        manifest=path_with_spaces,
        threshold=1_000_000,
        out=out,
    )
    assert fired is True
    text = out.text
    # The recipe must contain the path wrapped in shell quotes,
    # not the bare unquoted form.
    import shlex
    quoted = shlex.quote(path_with_spaces)
    assert quoted in text, (
        f"expected shlex-quoted path {quoted!r} in warning, "
        f"got:\n{text}"
    )
    # And the bare unquoted path must NOT appear as a standalone
    # token (it might appear as a substring inside the quoted
    # form, which is fine).
    # Specifically, the recipe line should be of the form
    # "--source-manifest '/some/path with spaces/manifest.jsonl' \\"
    # not the broken "--source-manifest /some/path with spaces/..."
    assert (
        f"--source-manifest {quoted}" in text
    ), "warning didn't surface --source-manifest with quoted path"


def test_warn_no_quoting_overhead_for_simple_paths() -> None:
    """shlex.quote is a no-op for paths without shell-special
    characters. Simple paths shouldn't get gratuitous quotes."""
    out = _CaptureStream()
    simple_path = "/tmp/manifest.jsonl"
    fired = warn_if_large_manifest(
        n_files=5_000_000,
        manifest=simple_path,
        threshold=1_000_000,
        out=out,
    )
    assert fired is True
    text = out.text
    # No surrounding quotes added for shell-safe paths.
    assert f"--source-manifest {simple_path}" in text


# ---- scored-records cache: belt/suspenders/buttons for corpus-scale runs ----

def _clean_files(tmp_path: Path, n: int) -> list[Path]:
    files = []
    for i in range(n):
        p = tmp_path / f"f{i}.txt"
        p.write_text("This is clean ordinary prose with no markup. " * 50,
                     encoding="utf-8")
        files.append(p)
    return files


def test_records_cache_passthrough_matches_uncached(tmp_path: Path) -> None:
    files = _clean_files(tmp_path, 2)
    plain = check_corpus_paths(files)
    cache = tmp_path / "c.json"
    cached = check_corpus_paths(files, cache_path=cache)
    assert cache.exists()
    payload = json.loads(cache.read_text(encoding="utf-8"))
    assert payload["status"] == "complete"
    assert len(payload["records"]) == 2
    # A cached run produces the same result as an uncached one.
    assert plain["files"] == cached["files"]
    assert plain["status"] == cached["status"]


def test_records_cache_resume_reuses_unchanged_files(tmp_path: Path) -> None:
    import check_corpus as cc_mod
    from unittest import mock

    files = _clean_files(tmp_path, 3)
    cache = tmp_path / "c.json"
    r1 = check_corpus_paths(files, cache_path=cache, cache_flush_every=1)
    # Second run with files UNCHANGED: every file is served from the cache, so
    # check_path is never called (proves reuse via the content-fingerprint match,
    # not just a path match).
    with mock.patch.object(cc_mod, "check_path", wraps=cc_mod.check_path) as spy:
        r2 = check_corpus_paths(files, cache_path=cache, cache_flush_every=1)
    assert spy.call_count == 0
    assert r2["n_files"] == 3
    assert r2["n_error"] == 0
    assert [f["path"] for f in r2["files"]] == [f["path"] for f in r1["files"]]


def test_records_cache_rescores_when_file_content_changes(tmp_path: Path) -> None:
    """Codex #212 P1: a cached 'clean' record must NOT be reused after the file's
    content changes — otherwise a hygiene gate could pass newly-contaminated input."""
    p = tmp_path / "f.txt"
    p.write_text(CLEAN.read_text(encoding="utf-8"), encoding="utf-8")
    cache = tmp_path / "c.json"
    r1 = check_corpus_paths([p], cache_path=cache)
    assert r1["status"] == "clean"
    # Overwrite the same path with contaminated content; the resume must re-score
    # (content fingerprint changed), not serve the stale clean record.
    p.write_text(CONTAMINATED.read_text(encoding="utf-8"), encoding="utf-8")
    r2 = check_corpus_paths([p], cache_path=cache)
    assert r2["status"] == "fail"


def test_records_cache_incompatible_meta_recomputes(tmp_path: Path) -> None:
    files = _clean_files(tmp_path, 1)
    cache = tmp_path / "c.json"
    check_corpus_paths(files, cache_path=cache,
                       warn_threshold=0.05, fail_threshold=0.10)
    files[0].unlink()
    # Different thresholds -> compat-meta mismatch -> cache ignored, rescored;
    # the now-missing file becomes an error record (proving no stale reuse).
    r = check_corpus_paths(files, cache_path=cache,
                           warn_threshold=0.20, fail_threshold=0.40)
    assert r["n_error"] == 1


def test_refresh_records_cache_discards_existing(tmp_path: Path) -> None:
    files = _clean_files(tmp_path, 1)
    cache = tmp_path / "c.json"
    check_corpus_paths(files, cache_path=cache)
    files[0].unlink()
    r = check_corpus_paths(files, cache_path=cache, refresh_cache=True)
    assert r["n_error"] == 1  # refresh discarded the cache -> rescored the missing file
