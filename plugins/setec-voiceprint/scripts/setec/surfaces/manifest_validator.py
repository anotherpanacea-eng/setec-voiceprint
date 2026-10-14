#!/usr/bin/env python3
"""
manifest_validator.py
Schema and integrity checks for ``corpus_manifest.jsonl``.

The manifest is the control plane for the validation spine: tools that
take a manifest filter ('use=baseline, register=blog_essay, ...') trust
that the manifest is well-formed before composing results. A single AI-
assisted entry mistakenly tagged ``ai_status: pre_ai_human`` can teach a
voiceprint pipeline that smoothing is part of the writer's voice; a
``use: validation`` entry tagged ``split: baseline`` collapses the
hold-out split into the training data; a missing-on-disk path produces
silent shrinkage of every downstream comparison.

This validator runs first. Everything downstream is allowed to assume
the manifest passed validation.

Exit codes:
  0   no errors (and no warnings, OR warnings allowed without --strict)
  1   errors present, or --strict and warnings present
  2   usage error, or --check-conflict-copies found a conflict/incomplete scan

Usage:
  python3 manifest_validator.py corpus_manifest.jsonl
  python3 manifest_validator.py corpus_manifest.jsonl --json
  python3 manifest_validator.py corpus_manifest.jsonl --strict
  python3 manifest_validator.py corpus_manifest.jsonl --check-conflict-copies
  python3 -u manifest_validator.py corpus_manifest.jsonl --progress-every 1000
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import stat
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator, TextIO

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore
import shingle_dedup_io  # type: ignore
from register_taxonomy import (  # type: ignore
    PROFILE_ONLY_REGISTERS,
    validate_registry_closure,
)
from register_sweep import (  # type: ignore
    BadInput,
    BIDI_CONTROLS,
    INT64_MAX,
    InternalError,
    MAX_PATH_BYTES,
    MAX_PERSONA_BYTES,
    MAX_USE_MEMBERS,
)
from setec.core.manifest_core import (  # noqa: F401 -- re-exported, same objects
    ALLOWED_AI_STATUS,
    ALLOWED_REGISTER,
    RETIRED_REGISTERS,
    ALLOWED_SPLIT,
    ALLOWED_PRIVACY,
    ALLOWED_USE,
    ALLOWED_CORPUS_ROLE,
    ALLOWED_REGISTER_MATCH,
    ALLOWED_TOPIC_MATCH,
    ALLOWED_CONSENT_STATUS,
    ALLOWED_ERA,
    ALLOWED_EDITING_STATUS,
    ALLOWED_LANGUAGE_STATUS,
    ALLOWED_SOURCE_FAMILY,
    REQUIRED_FIELDS,
    TRIPWIRE_BROAD_FIELD_THRESHOLD,
    TRIPWIRE_VERSION_FIELDS,
    TRIPWIRE_KNOWN_NESTED_FIELDS,
    KNOWN_FIELDS,
    Issue,
    _relative_candidates,
    resolve_path,
    ManifestParseError,
    _strict_row_pairs_hook,
    _reject_non_finite_constant,
    parse_manifest_row,
    decode_manifest_bytes,
    FORBIDDEN_ROW_BREAKS,
    _manifest_data_lines,
    _has,
    validate_entry,
    _validate_progress_options,
    _emit_progress,
    validate_manifest,
)

# Task-surface tag. The validator is a validation-spine tool, distinct
# from the smoothing-diagnosis and voice-coherence scripts. (manifest_core
# carries the same values for the envelopes validate_manifest builds.)
TASK_SURFACE = "validation"
TOOL_NAME = "manifest_validator"

SCRIPT_VERSION = "1.0"


# ---------- Schema ----------

# Every allowed leaf must have exactly one tier. This import-time tripwire
# makes the drop-in registry, rather than a default branch, authoritative.
validate_registry_closure(ALLOWED_REGISTER)


# ---------- Path resolution ----------


# ---------- Shared strict byte/row parser ----------
#
# One strict parsing layer feeds both the legacy full-row validator
# (``validate_manifest``) and the H2 register-composition-sweep projection
# seam (``project_register_sweep_manifest_bytes``, Spec 73). Sharing this
# layer means a manifest-bytes hardening (BOM, non-UTF-8, duplicate keys at
# any depth, non-finite numeric constants) applies identically to both call
# paths instead of drifting between two hand-written JSON readers.


# ---------- H2 register-composition-sweep projection seam (Spec 73) ----------
#
# ``project_register_sweep_manifest_bytes`` is a closed, source-blind
# projection: it reads exactly six owned keys per row (``path``, ``register``,
# ``use``, ``split``, ``persona``, ``ai_status``) via bracket subscript only,
# never iterates or tests containment on a row mapping, and never reads
# ``source``, ``source_id``, or ``source_family``. Any owned-field error
# refuses the *complete* projection (no partial plan, no warnings) with
# ``register_sweep.BadInput`` -- the one exception type the Spec 73 runner
# needs to catch from this seam. H2-admissible strictness is stricter than
# the warning-tolerant checks in ``validate_entry`` above: an unknown
# ``register``/``use``/``split``/``ai_status`` value that the general
# validator only warns about is not H2-admissible and refuses here.


@dataclasses.dataclass(frozen=True, slots=True)
class H2ProjectedRow:
    """One manifest row's seven owned H2 fields (Spec 73 lines ~661-670)."""

    manifest_ordinal: int
    path: str
    register: str | None
    use: tuple[str, ...]
    split: str | None
    persona: str | None
    ai_status: str


@dataclasses.dataclass(frozen=True, slots=True)
class H2PlannedDocument:
    """One frozen document-plan entry for a projected row's resolved path."""

    manifest_ordinal: int
    candidate_index: int
    absolute_path: Path
    fingerprint: tuple[int, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class RegisterSweepManifestProjection:
    """The complete H2 projection: every row plus its frozen document plan."""

    input_rows: int
    rows: tuple[H2ProjectedRow, ...]
    document_plan: tuple[H2PlannedDocument, ...]


_MISSING = object()


def _row_item(row: Any, key: str) -> Any:
    """Fetch one key by bracket subscript only.

    Never calls ``.get``, ``keys``, ``items``, ``values``, ``__iter__``, or
    ``__contains__`` on ``row``: only ``__getitem__``, and only for the exact
    key named. This is what lets the source-metamorphic invariant test pass
    an instrumented mapping that raises on every access path except a
    ``__getitem__`` of one of the six owned names.
    """
    try:
        return row[key]
    except KeyError:
        return _MISSING


def _valid_h2_string(value: Any, *, max_bytes: int) -> bool:
    """The shared H2 string domain: NFC, nonblank, no leading/trailing
    whitespace, 1..max_bytes UTF-8 bytes, no NUL/C0/C1/unpaired-surrogate/
    bidi-control character. Used for both ``path`` and ``persona``: the CLI
    persona filter (Spec 73 lines ~542-543) and the projected ``persona``
    field share exactly this domain, and ``path`` differs only in its byte
    ceiling.
    """
    if type(value) is not str:
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    if value != value.strip():
        return False
    try:
        encoded_len = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    if not (1 <= encoded_len <= max_bytes):
        return False
    for char in value:
        code = ord(char)
        if code == 0 or code < 0x20 or 0x7F <= code <= 0x9F:
            return False
        if 0xD800 <= code <= 0xDFFF:
            return False
        if char in BIDI_CONTROLS:
            return False
    return True


def _project_h2_row(row: Any, *, manifest_ordinal: int) -> H2ProjectedRow:
    """Project one already-parsed manifest row's seven owned fields.

    ``row`` need not be a ``dict``: only ``__getitem__`` is required, which is
    what the source-metamorphic invariant test exercises directly with a
    hostile mapping. The caller (``project_register_sweep_manifest_bytes``)
    is responsible for having already confirmed the parsed JSON value is an
    object before calling this. Raises ``register_sweep.BadInput`` on any
    owned-field type, domain, or shape violation; there is no partial result.
    """
    if not (0 <= manifest_ordinal <= INT64_MAX):
        raise InternalError()

    raw_path = _row_item(row, "path")
    if raw_path is _MISSING or not _valid_h2_string(raw_path, max_bytes=MAX_PATH_BYTES):
        raise BadInput()

    raw_register = _row_item(row, "register")
    if raw_register is _MISSING or raw_register is None:
        register: str | None = None
    elif type(raw_register) is str and raw_register in ALLOWED_REGISTER:
        register = raw_register
    else:
        raise BadInput()

    raw_use = _row_item(row, "use")
    if raw_use is _MISSING or type(raw_use) is not list:
        raise BadInput()
    if not (1 <= len(raw_use) <= MAX_USE_MEMBERS):
        raise BadInput()
    seen_use: set[str] = set()
    ordered_use: list[str] = []
    for item in raw_use:
        if type(item) is not str or item not in ALLOWED_USE or item in seen_use:
            raise BadInput()
        seen_use.add(item)
        ordered_use.append(item)
    use = tuple(ordered_use)

    raw_split = _row_item(row, "split")
    if raw_split is _MISSING or raw_split is None:
        split: str | None = None
    elif type(raw_split) is str and raw_split in ALLOWED_SPLIT:
        split = raw_split
    else:
        raise BadInput()

    raw_persona = _row_item(row, "persona")
    if raw_persona is _MISSING or raw_persona is None:
        persona: str | None = None
    elif _valid_h2_string(raw_persona, max_bytes=MAX_PERSONA_BYTES):
        persona = raw_persona
    else:
        raise BadInput()

    raw_ai_status = _row_item(row, "ai_status")
    if (
        raw_ai_status is _MISSING
        or type(raw_ai_status) is not str
        or raw_ai_status not in ALLOWED_AI_STATUS
    ):
        raise BadInput()

    return H2ProjectedRow(
        manifest_ordinal=manifest_ordinal,
        path=raw_path,
        register=register,
        use=use,
        split=split,
        persona=persona,
        ai_status=raw_ai_status,
    )


def _h2_candidate_vector(manifest: Path, raw: str) -> tuple[Path, ...]:
    """The frozen document-plan candidate vector for one projected path.

    Mirrors ``resolve_path``'s exact candidate order via the same shared
    ``_relative_candidates`` helper: one candidate (index 0) for an absolute
    path, or the same three-candidate relative vector in the same order. No
    existence probe happens here; ``shingle_dedup_io.bind_regular`` does that
    safety-checked selection.
    """
    p = Path(raw)
    if p.is_absolute():
        return (p,)
    return _relative_candidates(manifest, raw)


def _emit_h2_progress(progress_stream: TextIO, *, phase: str, rows: int, started_at: float) -> None:
    elapsed = time.monotonic() - started_at
    print(
        f"[{TOOL_NAME}] phase={phase} surface=register_sweep_projection "
        f"rows={rows} elapsed_seconds={elapsed:.1f}",
        file=progress_stream, flush=True,
    )


def project_register_sweep_manifest_bytes(
    data: bytes,
    *,
    manifest_path: str | Path,
    progress_every: int = 0,
    progress_stream: TextIO | None = None,
) -> RegisterSweepManifestProjection:
    """Project one manifest's H2-owned fields and plan its frozen document set.

    Consumes exactly the caller's already-bounded-read ``data`` bytes -- the
    manifest is not reopened or reread. Refuses the complete projection
    (raises ``register_sweep.BadInput``) on any BOM/non-UTF-8/malformed-JSON/
    duplicate-key/non-finite byte- or JSON-level problem, any non-object data
    row, any owned-field violation, or any unsafe/absent frozen document-plan
    candidate. There is no partial result: either every parsed-object row
    projects and plans, or nothing is returned.

    This function does **not** apply CLI scope filters and does **not** check
    for cross-row collisions in the resulting document plan: it plans every
    parsed-object row unconditionally, indexed by ``manifest_ordinal``. The
    Spec 73 runner selects the scoped subset (a later increment) and must
    call ``check_document_plan_collisions`` on that subset itself, before
    reading a first document body.
    """
    _validate_progress_options(progress_every, progress_stream)
    started_at = time.monotonic()
    if type(data) is not bytes:
        raise BadInput()
    manifest = Path(manifest_path)

    try:
        text = decode_manifest_bytes(data)
    except ManifestParseError as exc:
        raise BadInput() from exc

    try:
        data_lines = list(_manifest_data_lines(text))
    except ManifestParseError as exc:
        raise BadInput() from exc

    rows: list[H2ProjectedRow] = []
    document_plan: list[H2PlannedDocument] = []
    processed = 0
    for _lineno, line in data_lines:
        try:
            parsed = parse_manifest_row(line)
        except ManifestParseError as exc:
            raise BadInput() from exc
        if type(parsed) is not dict:
            raise BadInput()

        ordinal = processed
        processed += 1
        row = _project_h2_row(parsed, manifest_ordinal=ordinal)
        rows.append(row)

        candidates = _h2_candidate_vector(manifest, row.path)
        try:
            absolute_path, candidate_index, fingerprint = shingle_dedup_io.bind_regular(
                candidates
            )
        except shingle_dedup_io.SecureIOError as exc:
            raise BadInput() from exc
        document_plan.append(
            H2PlannedDocument(
                manifest_ordinal=ordinal,
                candidate_index=candidate_index,
                absolute_path=absolute_path,
                fingerprint=tuple(fingerprint),
            )
        )

        if progress_every and processed % progress_every == 0:
            assert progress_stream is not None
            _emit_h2_progress(progress_stream, phase="scan", rows=processed, started_at=started_at)

    if progress_every:
        assert progress_stream is not None
        _emit_h2_progress(progress_stream, phase="complete", rows=processed, started_at=started_at)

    return RegisterSweepManifestProjection(
        input_rows=processed,
        rows=tuple(rows),
        document_plan=tuple(document_plan),
    )


def _collision_key(absolute_path: Path) -> bytes:
    text = unicodedata.normalize("NFC", str(absolute_path))
    if os.name == "nt":
        text = text.casefold()
    return text.encode("utf-8", errors="surrogatepass")


def check_document_plan_collisions(
    entries: list[H2PlannedDocument] | tuple[H2PlannedDocument, ...],
) -> None:
    """Refuse if any two planned documents in ``entries`` collide.

    Pure and read-only: no filesystem I/O. The caller passes whatever subset
    of the full document plan it has selected -- the Spec 73 runner passes
    the CLI-scoped subset -- and this must be called before that caller reads
    a first document body.

    Two entries collide when they share the same normalized absolute-path
    collision key (NFC absolute-path bytes on POSIX; NFC plus Unicode
    casefold on native Windows) or the same retained file identity: their raw
    fingerprint tuples are equal, which a repeated row or a POSIX hardlink
    alias of one inode necessarily produces (both name the same file at the
    same instant, so every fingerprint field matches). Either collision
    raises ``register_sweep.BadInput``. No unowned source field participates.
    """
    if type(entries) not in (list, tuple):
        raise InternalError()
    seen_keys: set[bytes] = set()
    seen_fingerprints: set[tuple[int, ...]] = set()
    for entry in entries:
        if type(entry) is not H2PlannedDocument:
            raise InternalError()
        key = _collision_key(entry.absolute_path)
        if key in seen_keys:
            raise BadInput()
        seen_keys.add(key)
        fingerprint = tuple(entry.fingerprint)
        if fingerprint in seen_fingerprints:
            raise BadInput()
        seen_fingerprints.add(fingerprint)


# ---------- Per-entry checks ----------


# ---------- Whole-manifest driver ----------


# ---------- Conflict-copy preflight ----------

_CONFLICT_COPY_MARKER = "conflicted copy"


def _relative_scan_path(root: Path, path: str | Path) -> str:
    """Return a root-relative, portable report path.

    This deliberately uses the lexical traversal path rather than resolving
    either side: resolving would follow a link, which is outside the preflight
    boundary. ``os.path.relpath`` also keeps the root itself representable as
    ``.`` when an error prevents opening the first directory.
    """
    relative = os.path.relpath(os.fspath(path), os.fspath(root))
    if relative == ".":
        return "."
    # Convert only the host platform's path separator. On POSIX a literal
    # backslash is a legal basename character; rewriting it would fabricate a
    # different path (including a false-looking `../` traversal) and can
    # collapse two distinct entries into one report value.
    return relative.replace(os.sep, "/")


def _is_windows_reparse_point(entry: os.DirEntry[str]) -> bool:
    """Recognize every Windows reparse point, not only junctions.

    Cloud placeholders and other directory reparse types can report as
    directories without being symlinks or junctions. The Windows file-attribute
    bit is the complete traversal-boundary check. On non-Windows hosts the
    attribute is absent/zero, so this stays a harmless stdlib stat call.
    """
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not reparse_flag:
        return False
    file_attributes = getattr(
        entry.stat(follow_symlinks=False), "st_file_attributes", 0,
    )
    return bool(file_attributes & reparse_flag)


def check_conflict_copies(manifest_path: str | Path) -> dict[str, Any]:
    """Inspect the manifest-parent tree for sync conflict-copy names.

    The scan is name-only and fail-closed. It includes hidden entries, but
    never descends through symlinks or Windows junctions/reparse points.
    ``scan_errors`` intentionally names only the root-relative location and
    exception class so reports cannot leak absolute paths or OS diagnostics.
    """
    root = Path(manifest_path).parent
    matches: list[str] = []
    scan_errors: list[dict[str, str]] = []

    def record_scan_error(path: str | Path, exc: OSError) -> None:
        scan_errors.append({
            "path": _relative_scan_path(root, path),
            "error": type(exc).__name__,
        })

    def visit(directory: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    list(iterator), key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError as exc:
            record_scan_error(directory, exc)
            return

        for entry in entries:
            entry_path = Path(entry.path)
            relative = _relative_scan_path(root, entry.path)
            if _CONFLICT_COPY_MARKER in entry.name.casefold():
                matches.append(relative)

            try:
                # ``DirEntry.is_dir(follow_symlinks=False)`` handles ordinary
                # directory links. ``Path.is_junction`` is the corresponding
                # Windows reparse-point guard (available on supported 3.12).
                is_symlink = entry.is_symlink()
                is_junction = getattr(entry_path, "is_junction", lambda: False)()
                is_reparse_point = _is_windows_reparse_point(entry)
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                record_scan_error(entry.path, exc)
                continue

            if (
                is_directory
                and not is_symlink
                and not is_junction
                and not is_reparse_point
            ):
                visit(entry_path)

    visit(root)
    matches.sort(key=lambda path: (path.casefold(), path))
    scan_errors.sort(key=lambda item: (item["path"].casefold(), item["path"], item["error"]))
    return {
        "checked": True,
        "root": ".",
        "n_matches": len(matches),
        "matches": matches,
        "n_scan_errors": len(scan_errors),
        "scan_errors": scan_errors,
        "validation_ran": False,
    }


def _refused_preflight_result(manifest_path: str | Path,
                              conflict_copy_check: dict[str, Any]) -> dict[str, Any]:
    """Shape a refused result without parsing a manifest."""
    return {
        "task_surface": TASK_SURFACE,
        "manifest_path": str(Path(manifest_path)),
        "n_entries": None,
        "n_errors": None,
        "n_warnings": None,
        "issues": [],
        "tripwires": [],
        "summary": {},
        "conflict_copy_check": conflict_copy_check,
    }


# ---------- Output formatting ----------

def _fmt_counter(counter: dict[str, int]) -> str:
    if not counter:
        return "(none)"
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{k}={v}" for k, v in items)


def render_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    conflict_copy_check = result.get("conflict_copy_check")
    validation_ran = (
        conflict_copy_check is None or conflict_copy_check["validation_ran"]
    )
    lines.append("# Manifest Validation Report")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append(f"**Manifest:** {result['manifest_path']}")
    if validation_ran:
        lines.append(f"**Entries:** {result['n_entries']}")
        lines.append(
            f"**Errors:** {result['n_errors']}    "
            f"**Warnings:** {result['n_warnings']}"
        )
    else:
        lines.append("**Entries:** not run")
        lines.append("**Errors:** not run    **Warnings:** not run")
    lines.append("")

    if conflict_copy_check is not None:
        lines.append("## Conflict-copy preflight")
        lines.append("")
        lines.append(f"- **Root:** `{conflict_copy_check['root']}`")
        lines.append(f"- **Matches:** {conflict_copy_check['n_matches']}")
        lines.append(f"- **Scan errors:** {conflict_copy_check['n_scan_errors']}")
        lines.append("")
        if conflict_copy_check["matches"]:
            lines.append("### Matches")
            lines.append("")
            lines.extend(f"- `{path}`" for path in conflict_copy_check["matches"])
            lines.append("")
        if conflict_copy_check["scan_errors"]:
            lines.append("### Incomplete scan locations")
            lines.append("")
            lines.extend(
                f"- `{item['path']}` ({item['error']})"
                for item in conflict_copy_check["scan_errors"]
            )
            lines.append("")
        if not conflict_copy_check["validation_ran"]:
            lines.append("Manifest validation: NOT RUN (preflight refused)")
            lines.append("")

    summary = result.get("summary", {})
    if summary:
        lines.append("## Summary")
        lines.append("")
        for label, key in (
            ("By register", "by_register"),
            ("By ai_status", "by_ai_status"),
            ("By split", "by_split"),
            ("By use", "by_use"),
            ("By privacy", "by_privacy"),
            ("By persona", "by_persona"),
            ("By language_status", "by_language_status"),
            ("By adversarial_class", "by_adversarial_class"),
            ("By corpus_role", "by_corpus_role"),
            ("By era", "by_era"),
            ("By consent_status", "by_consent_status"),
            ("By register_match", "by_register_match"),
        ):
            lines.append(f"- **{label}:** {_fmt_counter(summary.get(key, {}))}")
        lines.append("")

    issues = result.get("issues", [])
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]

    if errors:
        lines.append("## Errors")
        lines.append("")
        for i in errors:
            ident = f"id={i['id']!r}" if i["id"] else "id=<none>"
            field = f", field={i['field']!r}" if i["field"] else ""
            lines.append(
                f"- line {i['lineno']} ({ident}{field}): {i['message']}"
            )
        lines.append("")

    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for i in warnings:
            ident = f"id={i['id']!r}" if i["id"] else "id=<none>"
            field = f", field={i['field']!r}" if i["field"] else ""
            lines.append(
                f"- line {i['lineno']} ({ident}{field}): {i['message']}"
            )
        lines.append("")

    tripwires_list = result.get("tripwires", []) or []
    if tripwires_list:
        lines.append("## Schema-migration tripwire (Issue #6)")
        lines.append("")
        lines.append(
            "The manifest shape has grown past one of the triggers "
            "Issue #6 named for considering a jsonschema-library "
            "migration. The handcrafted validator still passes; this "
            "is an advisory marker, not a failure."
        )
        lines.append("")
        for t in tripwires_list:
            ident = f"id={t['id']!r}" if t.get("id") else "id=<none>"
            field = f", field={t['field']!r}" if t.get("field") else ""
            lines.append(
                f"- [{t['category']}] line {t['lineno']} "
                f"({ident}{field}): {t['message']}"
            )
        lines.append("")

    if not errors and not warnings and (
        conflict_copy_check is None or conflict_copy_check["validation_ran"]
    ):
        lines.append("Manifest is clean.")
        lines.append("")

    return "\n".join(lines)


# ---------- CLI ----------

def _nonnegative_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a non-negative integer") from exc
    if value < 0:
        raise argparse.ArgumentTypeError("must be a non-negative integer")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a corpus_manifest.jsonl file. Run before "
                    "any manifest-consuming audit so downstream tools "
                    "can trust the manifest.",
    )
    parser.add_argument(
        "manifest",
        help="Path to a JSONL manifest file.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as errors for exit-code purposes.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of the markdown report.",
    )
    parser.add_argument(
        "--out",
        help="Write report to file instead of stdout.",
    )
    parser.add_argument(
        "--check-conflict-copies", action="store_true",
        help="Fail closed when the manifest-parent tree contains a sync conflict copy.",
    )
    parser.add_argument(
        "--progress-every", type=_nonnegative_int, default=1000, metavar="N",
        help="Emit a flushed aggregate stderr heartbeat every N manifest rows (default: 1000; "
             "use 0 to disable).",
    )
    args = parser.parse_args()

    conflict_copy_check: dict[str, Any] | None = None
    if args.check_conflict_copies:
        conflict_copy_check = check_conflict_copies(args.manifest)
        if (
            conflict_copy_check["n_matches"]
            or conflict_copy_check["n_scan_errors"]
        ):
            result = _refused_preflight_result(args.manifest, conflict_copy_check)
        else:
            result = validate_manifest(
                args.manifest,
                progress_every=args.progress_every,
                progress_stream=sys.stderr if args.progress_every else None,
            )
            conflict_copy_check["validation_ran"] = True
            result["conflict_copy_check"] = conflict_copy_check
    else:
        result = validate_manifest(
            args.manifest,
            progress_every=args.progress_every,
            progress_stream=sys.stderr if args.progress_every else None,
        )

    if args.json:
        envelope = build_audit_payload(result, target_path=args.manifest)
        output = json.dumps(envelope, indent=2, default=str)
    else:
        output = render_report(result)

    if args.check_conflict_copies:
        # Flag-mode reports are transport artifacts: make their UTF-8 bytes
        # independent of Windows text-mode newline translation, and give
        # --out exactly the bytes stdout would otherwise receive.
        output_bytes = (output.rstrip("\n") + "\n").encode("utf-8")
        if args.out:
            with open(args.out, "wb") as out_file:
                out_file.write(output_bytes)
            print(f"Written to {args.out}", file=sys.stderr)
        else:
            stdout_buffer = getattr(sys.stdout, "buffer", None)
            if stdout_buffer is not None:
                stdout_buffer.write(output_bytes)
                stdout_buffer.flush()
            else:
                sys.stdout.write(output_bytes.decode("utf-8"))
    elif args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)

    if conflict_copy_check is not None and not conflict_copy_check["validation_ran"]:
        return 2
    if result["n_errors"] > 0:
        return 1
    if args.strict and result["n_warnings"] > 0:
        return 1
    return 0


def _claim_license(result: dict[str, Any]) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "Manifest validation report. Walks every line of the "
            "JSONL manifest, enforces schema rules (required fields, "
            "ID uniqueness, persona/register consistency, path "
            "resolvability, impostor-corpus invariants, privacy "
            "ratchet on voice-cloning-grade entries), and emits a "
            "per-issue inventory with severity classification."
        ),
        does_not_license=(
            "A guarantee that the corpus files themselves are "
            "well-formed or appropriate for the audit downstream "
            "of the manifest. Schema-valid does not mean "
            "content-appropriate; pair with `check_corpus` for "
            "preprocessing-level hygiene and with the audit's own "
            "input checks for analysis-specific validity."
        ),
        comparison_set={
            "manifest_path": result.get("manifest_path"),
            "n_entries": result.get("n_entries"),
            "n_errors": result.get("n_errors"),
            "n_warnings": result.get("n_warnings"),
        },
        additional_caveats=[
            "Required-field rules and ID uniqueness are schema-"
            "level invariants; their violation blocks downstream "
            "audits. Optional-field warnings are advisory; treat as "
            "cues, not blockers, unless --strict is supplied.",
            "Privacy-ratchet enforcement on voice-cloning-grade "
            "entries (voice_profile / idiolect use tags) is "
            "load-bearing — a manifest declaring voice_profile use "
            "with a missing privacy=private field raises a warning "
            "that should not be ignored.",
        ],
    )


def build_audit_payload(
    result: dict[str, Any],
    *,
    target_path: Any,
) -> dict[str, Any]:
    """Wrap validate_manifest's result dict in the schema_version 1.0
    envelope per ``internal/SPEC_output_schema_unification.md``.
    """
    results_payload: dict[str, Any] = {}
    for k in (
        "manifest_path", "n_entries", "n_errors", "n_warnings",
        "issues", "tripwires", "summary", "conflict_copy_check",
    ):
        if k in result:
            results_payload[k] = result[k]

    warnings: list[str] = []
    conflict_copy_check = result.get("conflict_copy_check")
    if (
        conflict_copy_check is not None
        and not conflict_copy_check.get("validation_ran", False)
    ):
        warnings.append(
            "Conflict-copy preflight refused manifest validation; see "
            "results.conflict_copy_check."
        )
    if result.get("n_errors", 0):
        warnings.append(
            f"{result.get('n_errors')} manifest error(s); see "
            "results.issues."
        )
    tripwires_list = result.get("tripwires") or []
    if tripwires_list:
        categories = sorted({t.get("category") for t in tripwires_list if t.get("category")})
        warnings.append(
            "Schema-migration tripwire fired (Issue #6): "
            f"{', '.join(categories)}. See results.tripwires."
        )

    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=0,
        baseline=None,
        results=results_payload,
        claim_license=_claim_license(result),
        warnings=warnings,
    )


if __name__ == "__main__":
    sys.exit(main())
