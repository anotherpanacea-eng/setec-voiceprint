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

Usage:
  python3 manifest_validator.py corpus_manifest.jsonl
  python3 manifest_validator.py corpus_manifest.jsonl --json
  python3 manifest_validator.py corpus_manifest.jsonl --strict
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

# Task-surface tag. The validator is a validation-spine tool, distinct
# from the smoothing-diagnosis and voice-coherence scripts.
TASK_SURFACE = "validation"


# ---------- Schema ----------

# Fields whose values are checked against an allowed set. Unknown values
# generate warnings, not errors, so users can extend the taxonomy.
ALLOWED_AI_STATUS = {
    "pre_ai_human", "ai_generated", "ai_assisted",
    "ai_edited", "mixed", "unknown",
}
ALLOWED_REGISTER = {
    "literary_fiction", "blog_essay", "academic_philosophy",
    "testimony_policy", "personal", "policy_advocacy",
}
ALLOWED_SPLIT = {"baseline", "train", "test", "holdout"}
ALLOWED_PRIVACY = {"private", "shareable", "public_domain"}
ALLOWED_USE = {
    "baseline", "validation", "voice_validation", "voice_profile",
    "idiolect", "negative_baseline", "exclude",
}
ALLOWED_EDITING_STATUS = {
    "raw_draft", "revised_human", "published_cleaned", "coauthored",
}
# Language status of the writer relative to the text's language. ESL
# writing sits in the same low-variance region of stylometric space as
# RLHF-aligned LLM output (Liang et al., Patterns 2023, found average
# 61% FPR on TOEFL essays across seven AI-prose detectors). Mixing ESL
# entries into a baseline marked 'use: baseline' for voice-coherence
# work teaches the system that smoothing is part of the writer's voice.
# This field lets the validator and the future validation harness slice
# accordingly. Default 'unknown' rather than an error: corpora collected
# before ESL labeling existed are still usable, just with a wider band.
ALLOWED_LANGUAGE_STATUS = {
    "native", "non_native_advanced", "non_native_intermediate",
    "learner", "unknown",
}

# Fields required on every entry. Missing required fields are errors.
REQUIRED_FIELDS = ("id", "path", "ai_status", "use")

# All recognized field names. Unknown fields generate warnings.
KNOWN_FIELDS = {
    "id", "path", "project_area", "author", "persona", "register",
    "genre", "date_written", "ai_status", "editing_status",
    "word_count", "use", "split", "privacy", "source", "notes",
    "language_status",
    "adversarial_class", "source_id", "transform",
    # Extra fields surfaced by some manifests.
    "pov", "tags",
}


class Issue:
    """One validation finding. ``severity`` is 'error' or 'warning'."""

    __slots__ = ("severity", "lineno", "entry_id", "field", "message")

    def __init__(
        self,
        severity: str,
        lineno: int,
        entry_id: str | None,
        field: str | None,
        message: str,
    ) -> None:
        self.severity = severity
        self.lineno = lineno
        self.entry_id = entry_id
        self.field = field
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "lineno": self.lineno,
            "id": self.entry_id,
            "field": self.field,
            "message": self.message,
        }


# ---------- Path resolution ----------

def resolve_path(manifest: Path, raw: str) -> Path:
    """Match ``stylometry_core.resolve_manifest_path`` so the validator
    accepts the same path conventions the readers do."""
    p = Path(raw)
    if p.is_absolute():
        return p
    candidates = [
        manifest.parent / p,
        manifest.parent.parent / p,
        Path.cwd() / p,
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


# ---------- Per-entry checks ----------

def _has(entry: dict[str, Any], field: str) -> bool:
    value = entry.get(field)
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def validate_entry(
    entry: dict[str, Any],
    lineno: int,
    manifest_path: Path,
    seen_ids: set[str],
    seen_paths: dict[Path, str],
) -> list[Issue]:
    """Per-entry schema and integrity checks. Cross-entry checks
    (duplicates, file existence) use the ``seen_*`` accumulators."""
    issues: list[Issue] = []
    entry_id = entry.get("id") if isinstance(entry.get("id"), str) else None

    # Required fields.
    for field in REQUIRED_FIELDS:
        if not _has(entry, field):
            issues.append(Issue(
                "error", lineno, entry_id, field,
                f"Required field '{field}' is missing or empty.",
            ))

    # Unknown top-level fields trigger a warning per field. Useful for
    # catching typos like 'asi_status'.
    for field in entry:
        if field not in KNOWN_FIELDS:
            issues.append(Issue(
                "warning", lineno, entry_id, field,
                f"Unknown field '{field}'. Typo or extension?",
            ))

    # Duplicate id.
    if isinstance(entry_id, str):
        if entry_id in seen_ids:
            issues.append(Issue(
                "error", lineno, entry_id, "id",
                f"Duplicate id '{entry_id}' (already used earlier).",
            ))
        else:
            seen_ids.add(entry_id)

    # Path must resolve to an existing *file* (not a directory). The
    # corpus readers expect to read text files; a manifest entry whose
    # path resolves to a directory passes os.path.exists but fails or
    # misbehaves at read time.
    raw_path = entry.get("path")
    if isinstance(raw_path, str) and raw_path.strip():
        resolved = resolve_path(manifest_path, raw_path)
        if not resolved.exists():
            issues.append(Issue(
                "error", lineno, entry_id, "path",
                f"Path '{raw_path}' does not resolve to an existing file "
                f"(tried {resolved}).",
            ))
        elif not resolved.is_file():
            issues.append(Issue(
                "error", lineno, entry_id, "path",
                f"Path '{raw_path}' resolves to a non-file "
                f"({'directory' if resolved.is_dir() else 'special'} "
                f"at {resolved}). Manifest entries must point at text "
                "files, not directories or other path types.",
            ))
        else:
            previous = seen_paths.get(resolved)
            if previous is not None and previous != entry_id:
                issues.append(Issue(
                    "warning", lineno, entry_id, "path",
                    f"Same file ('{raw_path}') is also entry "
                    f"'{previous}'. Two ids point at one file.",
                ))
            else:
                seen_paths[resolved] = entry_id or ""

    # Enum-valued fields. Unknown values are warnings (extensible).
    ai_status = entry.get("ai_status")
    if isinstance(ai_status, str) and ai_status not in ALLOWED_AI_STATUS:
        issues.append(Issue(
            "warning", lineno, entry_id, "ai_status",
            f"Unknown ai_status '{ai_status}'. "
            f"Known values: {', '.join(sorted(ALLOWED_AI_STATUS))}.",
        ))
    register = entry.get("register")
    if isinstance(register, str) and register not in ALLOWED_REGISTER:
        issues.append(Issue(
            "warning", lineno, entry_id, "register",
            f"Unknown register '{register}'. "
            f"Known values: {', '.join(sorted(ALLOWED_REGISTER))}.",
        ))
    split = entry.get("split")
    if isinstance(split, str) and split not in ALLOWED_SPLIT:
        issues.append(Issue(
            "warning", lineno, entry_id, "split",
            f"Unknown split '{split}'. "
            f"Known values: {', '.join(sorted(ALLOWED_SPLIT))}.",
        ))
    privacy = entry.get("privacy")
    if isinstance(privacy, str) and privacy not in ALLOWED_PRIVACY:
        issues.append(Issue(
            "warning", lineno, entry_id, "privacy",
            f"Unknown privacy '{privacy}'. "
            f"Known values: {', '.join(sorted(ALLOWED_PRIVACY))}.",
        ))
    editing_status = entry.get("editing_status")
    if isinstance(editing_status, str) and editing_status not in ALLOWED_EDITING_STATUS:
        issues.append(Issue(
            "warning", lineno, entry_id, "editing_status",
            f"Unknown editing_status '{editing_status}'.",
        ))
    language_status = entry.get("language_status")
    if isinstance(language_status, str) and language_status not in ALLOWED_LANGUAGE_STATUS:
        issues.append(Issue(
            "warning", lineno, entry_id, "language_status",
            f"Unknown language_status '{language_status}'. "
            f"Known values: {', '.join(sorted(ALLOWED_LANGUAGE_STATUS))}.",
        ))

    # 'use' must be a list per the manifest spec.
    use = entry.get("use")
    if use is not None:
        if not isinstance(use, list):
            issues.append(Issue(
                "error", lineno, entry_id, "use",
                "'use' must be a list (e.g. [\"baseline\"]). "
                "Got " + type(use).__name__ + ".",
            ))
        else:
            for u in use:
                if u not in ALLOWED_USE:
                    issues.append(Issue(
                        "warning", lineno, entry_id, "use",
                        f"Unknown use tag '{u}'. "
                        f"Known: {', '.join(sorted(ALLOWED_USE))}.",
                    ))

    # use/split contradictions per ROADMAP "Phase 1 -> Phase 2
    # operational sequence" guidance. validation cannot live in baseline
    # split; baseline use should sit in the baseline split.
    if isinstance(use, list) and isinstance(split, str):
        if "validation" in use and split == "baseline":
            issues.append(Issue(
                "error", lineno, entry_id, "use",
                "Entry tagged 'use: validation' but 'split: baseline'. "
                "Validation entries must live outside the baseline split "
                "or the holdout collapses into the training data.",
            ))
        if "baseline" in use and split in ("train", "test", "holdout"):
            issues.append(Issue(
                "warning", lineno, entry_id, "use",
                f"Entry tagged 'use: baseline' but 'split: {split}'. "
                "Baseline use typically sits in 'split: baseline'.",
            ))

    # Privacy ratchet for voiceprint sources. A voice profile or
    # idiolect corpus is a voice-cloning input; sources should be marked
    # explicitly private. Any value other than 'private', including a
    # missing field or a non-string value, fails the ratchet: silence
    # is not consent for a voice-cloning source.
    voiceprint_uses = {"voice_profile", "idiolect"}
    found_voiceprint_uses = (
        sorted(voiceprint_uses.intersection(use))
        if isinstance(use, list)
        else []
    )
    if found_voiceprint_uses:
        if privacy != "private":
            tag_list = ", ".join(f"'{tag}'" for tag in found_voiceprint_uses)
            shown = (
                f"'{privacy}'" if isinstance(privacy, str)
                else f"{type(privacy).__name__} ({privacy!r})"
                if privacy is not None
                else "<missing>"
            )
            issues.append(Issue(
                "warning", lineno, entry_id, "privacy",
                f"Entry has voiceprint use tag(s) "
                f"{tag_list} but privacy={shown}. "
                "Voiceprint sources should be marked privacy='private' "
                "explicitly. Voiceprints are voice-cloning inputs.",
            ))

    # ai_status / editing_status sanity. If a piece is marked
    # pre_ai_human, an AI-implicating editing_status is contradictory.
    if (
        ai_status == "pre_ai_human"
        and isinstance(editing_status, str)
        and editing_status == "coauthored"
    ):
        issues.append(Issue(
            "warning", lineno, entry_id, "ai_status",
            "ai_status='pre_ai_human' with editing_status='coauthored' "
            "may indicate inconsistent provenance.",
        ))

    # ESL ratchet for voice-coherence baselines and voice profiles.
    # Non-native English prose sits in the low-variance region of
    # stylometric space that AI-smoothing detectors flag; using such
    # entries as a baseline for voice-coherence work teaches the system
    # that smoothing is part of the writer's voice. This is a warning,
    # not an error: ESL writing is a legitimate corpus, just not a
    # legitimate voice-coherence baseline without explicit acknowledgment.
    if (
        isinstance(use, list)
        and isinstance(language_status, str)
        and language_status in {"non_native_advanced", "non_native_intermediate", "learner"}
    ):
        if "baseline" in use:
            issues.append(Issue(
                "warning", lineno, entry_id, "language_status",
                f"Entry tagged 'use: baseline' has language_status="
                f"'{language_status}'. ESL prose sits in the low-variance "
                "region that AI-smoothing detectors flag; mixing it into a "
                "voice-coherence baseline can teach the system that "
                "smoothing is part of the writer's voice. Add an explicit "
                "'notes' acknowledgment if this is intentional.",
            ))
        if "voice_profile" in use or "idiolect" in use:
            tag = "voice_profile" if "voice_profile" in use else "idiolect"
            issues.append(Issue(
                "warning", lineno, entry_id, "language_status",
                f"Entry tagged 'use: {tag}' has language_status="
                f"'{language_status}'. Voiceprint sources built from ESL prose "
                "carry a known low-variance bias; downstream comparisons "
                "will under-flag AI smoothing. Acknowledge in 'notes' if "
                "intentional.",
            ))

    # word_count must be a non-negative number when present.
    wc = entry.get("word_count")
    if wc is not None:
        if not isinstance(wc, (int, float)) or wc < 0:
            issues.append(Issue(
                "error", lineno, entry_id, "word_count",
                f"word_count must be a non-negative number. Got {wc!r}.",
            ))

    return issues


# ---------- Whole-manifest driver ----------

def validate_manifest(manifest_path: str | Path) -> dict[str, Any]:
    """Validate every line of the manifest and return a result dict.

    Importable: downstream tools that consume a manifest can call this
    and inspect the result before proceeding. The result includes a
    structured ``issues`` list (errors and warnings together) plus a
    summary of entries by register, ai_status, split, use, and privacy.
    """
    path = Path(manifest_path)
    issues: list[Issue] = []
    n_entries = 0
    seen_ids: set[str] = set()
    seen_paths: dict[Path, str] = {}
    by_register: Counter[str] = Counter()
    by_ai_status: Counter[str] = Counter()
    by_split: Counter[str] = Counter()
    by_use: Counter[str] = Counter()
    by_privacy: Counter[str] = Counter()
    by_persona: Counter[str] = Counter()
    by_language_status: Counter[str] = Counter()
    by_adversarial_class: Counter[str] = Counter()

    if not path.exists():
        return {
            "task_surface": TASK_SURFACE,
            "manifest_path": str(path),
            "n_entries": 0,
            "n_errors": 1,
            "n_warnings": 0,
            "issues": [{
                "severity": "error",
                "lineno": 0,
                "id": None,
                "field": None,
                "message": f"Manifest file '{path}' does not exist.",
            }],
            "summary": {},
        }

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "task_surface": TASK_SURFACE,
            "manifest_path": str(path),
            "n_entries": 0,
            "n_errors": 1,
            "n_warnings": 0,
            "issues": [{
                "severity": "error",
                "lineno": 0,
                "id": None,
                "field": None,
                "message": f"Could not read manifest: {exc}.",
            }],
            "summary": {},
        }

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(Issue(
                "error", lineno, None, None,
                f"Malformed JSON on line {lineno}: {exc.msg}.",
            ))
            continue
        if not isinstance(entry, dict):
            issues.append(Issue(
                "error", lineno, None, None,
                f"Line {lineno} is not a JSON object (got "
                f"{type(entry).__name__}).",
            ))
            continue

        n_entries += 1
        issues.extend(
            validate_entry(entry, lineno, path, seen_ids, seen_paths)
        )

        # Summary buckets.
        register = entry.get("register")
        if isinstance(register, str):
            by_register[register] += 1
        ai_status = entry.get("ai_status")
        if isinstance(ai_status, str):
            by_ai_status[ai_status] += 1
        split = entry.get("split")
        if isinstance(split, str):
            by_split[split] += 1
        use = entry.get("use")
        if isinstance(use, list):
            for u in use:
                if isinstance(u, str):
                    by_use[u] += 1
        privacy = entry.get("privacy")
        if isinstance(privacy, str):
            by_privacy[privacy] += 1
        persona = entry.get("persona")
        if isinstance(persona, str):
            by_persona[persona] += 1
        language_status = entry.get("language_status")
        if isinstance(language_status, str):
            by_language_status[language_status] += 1
        adversarial_class = entry.get("adversarial_class")
        if isinstance(adversarial_class, str):
            by_adversarial_class[adversarial_class] += 1

    n_errors = sum(1 for i in issues if i.severity == "error")
    n_warnings = sum(1 for i in issues if i.severity == "warning")

    return {
        "task_surface": TASK_SURFACE,
        "manifest_path": str(path),
        "n_entries": n_entries,
        "n_errors": n_errors,
        "n_warnings": n_warnings,
        "issues": [i.to_dict() for i in issues],
        "summary": {
            "by_register": dict(by_register),
            "by_ai_status": dict(by_ai_status),
            "by_split": dict(by_split),
            "by_use": dict(by_use),
            "by_privacy": dict(by_privacy),
            "by_persona": dict(by_persona),
            "by_language_status": dict(by_language_status),
            "by_adversarial_class": dict(by_adversarial_class),
        },
    }


# ---------- Output formatting ----------

def _fmt_counter(counter: dict[str, int]) -> str:
    if not counter:
        return "(none)"
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return ", ".join(f"{k}={v}" for k, v in items)


def render_report(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Manifest Validation Report")
    lines.append("")
    lines.append(f"**Task surface:** `{TASK_SURFACE}`")
    lines.append(f"**Manifest:** {result['manifest_path']}")
    lines.append(f"**Entries:** {result['n_entries']}")
    lines.append(
        f"**Errors:** {result['n_errors']}    "
        f"**Warnings:** {result['n_warnings']}"
    )
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

    if not errors and not warnings:
        lines.append("Manifest is clean.")
        lines.append("")

    return "\n".join(lines)


# ---------- CLI ----------

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
    args = parser.parse_args()

    result = validate_manifest(args.manifest)

    if args.json:
        output = json.dumps(result, indent=2, default=str)
    else:
        output = render_report(result)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Written to {args.out}", file=sys.stderr)
    else:
        print(output)

    if result["n_errors"] > 0:
        return 1
    if args.strict and result["n_warnings"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
