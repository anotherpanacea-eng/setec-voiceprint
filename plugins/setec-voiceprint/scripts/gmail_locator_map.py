#!/usr/bin/env python3
"""gmail_locator_map.py — build a metadata-only companion locator map from
``acquire_gmail_sent.py``'s per-piece ``*.meta.json`` sidecars.

``acquire_gmail_sent.py`` writes the non-reversible private locators
(``author_corpus_entry_locator`` / ``author_corpus_thread_locator`` /
``author_corpus_order_timestamp``) only into each piece's ``.meta.json``
sidecar, not into ``draft_manifest.jsonl``. ``gmail_shadow_reacquisition_gate_v1``
anticipates exactly this: "If the acquirer keeps private locators only in
metadata sidecars, produce a metadata-only companion map before invoking this
gate; do not copy source prose into that map."

This formalizes that fallback into a tested repo utility, stricter than the gate
requires: it enforces a strict one-to-one join (and vice versa) between sidecars
and manifest rows, and refuses (writing nothing) on any coverage gap or locator
collision. It NEVER opens a ``.txt`` body (it reads only sidecar JSON and the
manifest), and it publishes the map atomically with a read-back verification, so
a crash leaves either no map or a complete, verified one — never a partial map.

Prose-free by contract: stdout carries only a single JSON summary line
(counts/hash/path); on a coverage gap the offending stems are printed to STDERR
only, clearly marked NOT prose-free (stems are subject-derived slugs) — never
into the written artifact or any receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquisition_core as ac  # noqa: E402

TOOL_NAME = "gmail_locator_map"
_META_SUFFIX = ".meta.json"


class LocatorMapError(Exception):
    """A structural error in the inputs (bad JSON / non-object row).

    Metadata, not prose — a malformed manifest/sidecar is a stop-and-fix
    condition, never something to fail open on."""


@dataclass
class LocatorMapResult:
    rows: list[dict]
    total_sidecars: int
    total_manifest_rows: int
    gap_counts: dict[str, int]
    offending: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_gaps(self) -> bool:
        return any(self.gap_counts.values())

    @property
    def empty_input(self) -> bool:
        return self.total_sidecars == 0 and self.total_manifest_rows == 0


def _read_manifest_ids(manifest_path: Path) -> set[str]:
    """Return the set of manifest-row ``id`` values (== sidecar stems).

    Refuses (LocatorMapError) on a JSON decode error or a non-object row — this
    is metadata, not something to fail open on.
    """
    ids: set[str] = set()
    if not manifest_path.exists():
        return ids
    for line_number, line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(), start=1,
    ):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LocatorMapError(
                f"manifest line {line_number} is not valid JSON"
            ) from exc
        if not isinstance(row, dict):
            raise LocatorMapError(f"manifest line {line_number} is not an object")
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            raise LocatorMapError(f"manifest line {line_number} lacks a string id")
        # A repeated id is a real defect (two rows claim one stem), not something
        # to silently collapse through a set — the join below would then bind one
        # locator to a row whose twin is invisible. Refuse.
        if rid in ids:
            raise LocatorMapError(
                f"manifest line {line_number} repeats an id already seen"
            )
        ids.add(rid)
    return ids


def _read_sidecars(output_dir: Path) -> dict[str, dict]:
    """Return {stem: sidecar-dict}. Reads ONLY ``*.meta.json`` — never a ``.txt``.

    Refuses (LocatorMapError) on a JSON decode error or a non-object sidecar.
    """
    sidecars: dict[str, dict] = {}
    if not output_dir.exists():
        return sidecars
    for meta_path in sorted(output_dir.glob("*" + _META_SUFFIX)):
        stem = meta_path.name[: -len(_META_SUFFIX)]
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise LocatorMapError(f"sidecar {meta_path.name} is not valid JSON") from exc
        if not isinstance(data, dict):
            raise LocatorMapError(f"sidecar {meta_path.name} is not an object")
        sidecars[stem] = data
    return sidecars


def build_locator_map(output_dir: Path, manifest_path: Path) -> LocatorMapResult:
    """Pure read: join sidecars to manifest rows and compute coverage.

    Enforces a strict one-to-one invariant (and vice versa): every covered stem
    must have an entry locator, no locator may resolve to two ids, no sidecar may
    lack a manifest row, and no manifest row may lack a sidecar.
    """
    manifest_ids = _read_manifest_ids(manifest_path)
    sidecars = _read_sidecars(output_dir)
    sidecar_stems = set(sidecars)

    orphan_sidecars = sorted(sidecar_stems - manifest_ids)
    orphan_manifest_ids = sorted(manifest_ids - sidecar_stems)
    covered = sidecar_stems & manifest_ids

    missing_locator = sorted(
        stem for stem in covered
        if not sidecars[stem].get("author_corpus_entry_locator")
    )
    locator_to_ids: dict[str, list[str]] = defaultdict(list)
    for stem in covered:
        locator = sidecars[stem].get("author_corpus_entry_locator")
        if locator:
            locator_to_ids[locator].append(stem)
    duplicate_locators = {
        locator: sorted(ids)
        for locator, ids in locator_to_ids.items()
        if len(ids) > 1
    }

    gap_counts = {
        "orphan_sidecars": len(orphan_sidecars),
        "orphan_manifest_ids": len(orphan_manifest_ids),
        "missing_locator": len(missing_locator),
        "duplicate_locators": len(duplicate_locators),
    }
    offending = {
        "orphan_sidecars": orphan_sidecars,
        "orphan_manifest_ids": orphan_manifest_ids,
        "missing_locator": missing_locator,
        "duplicate_locators": sorted(duplicate_locators),
    }

    rows: list[dict] = []
    if not any(gap_counts.values()):
        for stem in sorted(covered):
            data = sidecars[stem]
            row = {
                "source_id": stem,
                "private_entry_locator": data["author_corpus_entry_locator"],
            }
            thread = data.get("author_corpus_thread_locator")
            if thread is not None:
                row["private_thread_locator"] = thread
            order_ts = data.get("author_corpus_order_timestamp")
            if order_ts is not None:
                row["private_order_timestamp"] = order_ts
            rows.append(row)

    return LocatorMapResult(
        rows=rows,
        total_sidecars=len(sidecar_stems),
        total_manifest_rows=len(manifest_ids),
        gap_counts=gap_counts,
        offending=offending,
    )


def _serialize_rows(rows: list[dict]) -> str:
    return "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n" for row in rows
    )


def _reread_text(path: Path) -> str:
    """Re-read a just-written file (a monkeypatchable seam for the load-bearing
    read-back verification test)."""
    return path.read_text(encoding="utf-8")


def publish_map(rows: list[dict], map_out: Path) -> str:
    """Atomically AND exclusively publish ``rows`` to ``map_out``; return sha256.

    Write payload -> unique temp -> fsync -> chmod 0600 -> RE-READ and validate
    (hash equality AND structural round-trip) -> exclusive ``os.link`` claim of
    the final name. The final step uses ``os.link`` (not ``os.replace``) so it is
    both atomic and EXCLUSIVE: if any ``map_out`` exists at that instant — a
    foreign destination that appeared AFTER ``run()``'s pre-publish existence
    check — the link raises ``FileExistsError`` and the foreign file is preserved
    untouched, never overwritten. ``os.replace`` would have clobbered it (a
    TOCTOU hole). The link makes the temp and ``map_out`` the same file; the
    ``finally`` clause then drops the temp name, leaving the verified target.

    A kill or exception before the link leaves either no ``map_out`` or a
    leftover uniquely-named ``.tmp`` (cleaned in ``finally``); a kill during/after
    the link leaves a complete, already-verified ``map_out``. Never a truncated,
    unverified, or foreign-clobbering target.
    """
    payload = _serialize_rows(rows)
    expected_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    map_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = map_out.parent / f".{map_out.name}.tmp-{uuid.uuid4().hex}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        reread = _reread_text(tmp)
        if hashlib.sha256(reread.encode("utf-8")).hexdigest() != expected_hash:
            raise LocatorMapError("read-back hash mismatch; refusing to publish")
        reread_rows = [
            json.loads(line) for line in reread.splitlines() if line.strip()
        ]
        if {r["source_id"]: r for r in reread_rows} != {
            r["source_id"]: r for r in rows
        }:
            raise LocatorMapError("read-back structural mismatch; refusing to publish")
        try:
            os.link(tmp, map_out)
        except FileExistsError as exc:
            raise LocatorMapError(
                "destination appeared after the pre-publish existence check; "
                "refusing to overwrite it"
            ) from exc
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return expected_hash


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL_NAME, description=(
        "Build a metadata-only companion locator map from acquire_gmail_sent.py "
        "sidecars for the shadow reacquisition gate."))
    p.add_argument("--output-dir", required=True,
                   help="Directory holding *.meta.json sidecars.")
    p.add_argument("--manifest-path", default=None,
                   help="Manifest JSONL (default <output-dir>/draft_manifest.jsonl).")
    p.add_argument("--map-out", required=True,
                   help="Destination locator-map JSONL (must not already exist).")
    ac.add_allow_empty_arg(p)
    return p


def run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser()
    manifest_path = (
        Path(args.manifest_path).expanduser() if args.manifest_path
        else output_dir / "draft_manifest.jsonl"
    )
    map_out = Path(args.map_out).expanduser()

    # Privacy gate first, before any read/write. The temp file is a sibling of
    # map_out and inherits the same validated-private parent.
    ac.check_output_privacy(
        [map_out, map_out.parent, output_dir, manifest_path],
        allow_public=False, tool=TOOL_NAME,
    )

    if map_out.exists():
        sys.stderr.write(f"{TOOL_NAME}: refusing to overwrite existing {map_out}\n")
        return 1

    try:
        result = build_locator_map(output_dir, manifest_path)
    except LocatorMapError as exc:
        sys.stderr.write(f"{TOOL_NAME}: structural error: {exc}\n")
        return 1

    if result.empty_input and not args.allow_empty:
        sys.stderr.write(
            f"{TOOL_NAME}: no sidecars and no manifest rows. Pass --allow-empty "
            "to write an explicit 0-row map.\n"
        )
        return 1

    if result.has_gaps:
        sys.stderr.write(json.dumps(
            {"locator_map_coverage_gap": result.gap_counts}, sort_keys=True,
        ) + "\n")
        # Offending stems/locators are subject-derived -> NOT prose-free.
        sys.stderr.write(
            "NB not prose-free (subject-derived stems) — console/operator-review "
            "only, never into a receipt/_progress.jsonl:\n"
        )
        sys.stderr.write(json.dumps(result.offending, sort_keys=True) + "\n")
        return 2

    try:
        output_sha = publish_map(result.rows, map_out)
    except (LocatorMapError, OSError) as exc:
        sys.stderr.write(f"{TOOL_NAME}: atomic publish failed: {exc}\n")
        return 1
    sys.stdout.write(json.dumps({
        "rows_written": len(result.rows),
        "output": str(map_out),
        "output_sha256": output_sha,
    }, sort_keys=True) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_arg_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
