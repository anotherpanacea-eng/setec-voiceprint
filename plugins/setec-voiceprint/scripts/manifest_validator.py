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
  python3 -u manifest_validator.py corpus_manifest.jsonl --progress-every 1000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, TextIO

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_output  # type: ignore

# Task-surface tag. The validator is a validation-spine tool, distinct
# from the smoothing-diagnosis and voice-coherence scripts.
TASK_SURFACE = "validation"
TOOL_NAME = "manifest_validator"
SCRIPT_VERSION = "1.0"


# ---------- Schema ----------

# Fields whose values are checked against an allowed set. Unknown values
# generate warnings, not errors, so users can extend the taxonomy.
ALLOWED_AI_STATUS = {
    # Pre-existing values:
    "pre_ai_human", "ai_generated", "ai_assisted",
    "ai_edited", "mixed", "unknown",
    # Added 2026-05-13 per internal/SPEC_authorship_states.md:
    # an opt-in refinement of `ai_generated` for the case where
    # the LLM was given a substantive human seed (outline, brief,
    # transcript, point-by-point structure). The default
    # `ai_generated` remains the backwards-compat catch-all when
    # the seed degree is unknown or unspecified.
    "ai_generated_from_outline",
}
ALLOWED_REGISTER = {
    "literary_fiction", "blog_essay", "academic_philosophy",
    "testimony_policy", "personal", "policy_advocacy",
    "literary_horror", "policy_brief", "scholarly_article",
    "legal_brief", "grant_proposal", "expert_affidavit",
    "regulatory_comment",
}
ALLOWED_SPLIT = {"baseline", "train", "test", "holdout"}
ALLOWED_PRIVACY = {"private", "shareable", "public_domain"}
ALLOWED_USE = {
    "baseline", "validation", "voice_validation", "voice_profile",
    "voice_impostor",
    "idiolect", "negative_baseline", "exclude",
}

# Impostor-corpus support (per internal/2026-05-08-impostor-corpus-spec.md).
# corpus_role distinguishes the writer's own baseline material from
# impostor-pool entries the General Imposters validation harness will
# need (Koppel et al. 2014, Kestemont et al. 2016). identity_baseline
# is the default for backward compatibility when the field is absent;
# new acquisition scripts emit it explicitly.
ALLOWED_CORPUS_ROLE = {
    "identity_baseline", "impostor", "distractor", "adversarial",
}
# register_match and topic_match describe how closely an impostor
# entry's register / topic resembles the target persona's. Used by
# the future GI harness to weight impostor candidates.
ALLOWED_REGISTER_MATCH = {"high", "medium", "low"}
ALLOWED_TOPIC_MATCH = {"high", "medium", "low"}
# consent_status records the legal/ethical posture for redistributing
# anything derived from this entry. The validator ratchets impostor
# entries with consent_status='undocumented'; future public-report
# harnesses must escalate that ratchet to a refusal.
ALLOWED_CONSENT_STATUS = {
    "public_record", "cc_licensed", "fair_use_research",
    "author_consent", "undocumented",
}
# era is finer than ai_status for impostor calibration: pre-ChatGPT
# (Nov 2022) prose is the cleanest impostor pool; post-AI-widespread
# (mid-2024+) entries may include AI-collaborated writing that
# contaminates the human-impostor signal.
ALLOWED_ERA = {
    "pre_chatgpt", "pre_ai_widespread", "post_ai_widespread", "undated",
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

# Schema-migration tripwire (Issue #6). The handcrafted validator stays
# in place until the manifest shape outgrows it: *unfamiliar* nested
# per-entry objects, an explicit schema/manifest version field, or
# substantially more fields than today's flat KNOWN_FIELDS set. When
# any of these fire, validate_manifest() records a tripwire entry
# pointing back to Issue #6 so the next reader knows to consider
# migrating structural checks to the jsonschema library.
#
# Already-documented nested fields (the `ai_status: mixed` path uses
# ``notes.composite_states``; see references/manifest-schema.md §16
# and editlens_to_manifest.py) do NOT fire the nested-trigger — the
# handcrafted validator already covers them. The trigger is for
# *unfamiliar* nested shape that would warrant moving to jsonschema.
TRIPWIRE_BROAD_FIELD_THRESHOLD = 45
TRIPWIRE_VERSION_FIELDS = ("schema_version", "manifest_version")
TRIPWIRE_KNOWN_NESTED_FIELDS = frozenset({"notes"})

# All recognized field names. Unknown fields generate warnings.
KNOWN_FIELDS = {
    "id", "path", "project_area", "author", "persona", "register",
    "genre", "date_written", "ai_status", "editing_status",
    "word_count", "use", "split", "privacy", "source", "notes",
    "language_status",
    "adversarial_class", "source_id", "transform",
    # Extra fields surfaced by some manifests.
    "pov", "tags",
    # Impostor-corpus fields (see ALLOWED_CORPUS_ROLE etc. above).
    "corpus_role", "impostor_for", "register_match", "topic_match",
    "consent_status", "era", "acquired_via", "content_hash",
    # Eval-discipline: operator-declared topic / content-bucket group key
    # (spec 28). Free-text, open-set (no enum, no validation) like a tag —
    # it is the bucket the topic-leakage split partitions on. DISTINCT from
    # `topic_match` (impostor-corpus closeness, high/medium/low): `topic`
    # is the content bucket a record's prose belongs to. Topic is parsed,
    # never inferred — SETEC asserts no semantics it cannot license.
    "topic",
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

    # ai_status: mixed consistency check. Per
    # internal/SPEC_authorship_states.md §6.4, `mixed` entries should
    # carry a `notes.composite_states` array listing the authorship
    # states present across sections of the document. Without it, the
    # `mixed` value is semantically empty — downstream consumers
    # cannot route the entry by state. Soft warning, not error: legacy
    # `mixed` entries from before this consistency check remain valid
    # so existing manifests don't break on validation.
    if ai_status == "mixed":
        notes = entry.get("notes")
        composite_states = None
        if isinstance(notes, dict):
            composite_states = notes.get("composite_states")
        if not (
            isinstance(composite_states, list) and composite_states
        ):
            issues.append(Issue(
                "warning", lineno, entry_id, "ai_status",
                "ai_status='mixed' should carry a "
                "`notes.composite_states` array listing the authorship "
                "states present across sections (e.g., "
                "['ai_assisted', 'ai_generated_from_outline']). Without "
                "it, the 'mixed' value is semantically empty and "
                "downstream consumers cannot route by state.",
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

    # ---------- Impostor-corpus fields ---------------------
    # Per internal/2026-05-08-impostor-corpus-spec.md. corpus_role
    # defaults to "identity_baseline" when absent (backward compat
    # for pre-impostor manifests). Most ratchets fire only when
    # corpus_role is explicitly set, so old manifests don't suddenly
    # generate new errors.
    corpus_role = entry.get("corpus_role")
    if corpus_role is not None:
        if not isinstance(corpus_role, str) or corpus_role not in ALLOWED_CORPUS_ROLE:
            issues.append(Issue(
                "warning", lineno, entry_id, "corpus_role",
                f"Unknown corpus_role {corpus_role!r}. "
                f"Known: {', '.join(sorted(ALLOWED_CORPUS_ROLE))}.",
            ))
    register_match = entry.get("register_match")
    if register_match is not None:
        if not isinstance(register_match, str) or register_match not in ALLOWED_REGISTER_MATCH:
            issues.append(Issue(
                "warning", lineno, entry_id, "register_match",
                f"Unknown register_match {register_match!r}. "
                f"Known: {', '.join(sorted(ALLOWED_REGISTER_MATCH))}.",
            ))
    topic_match = entry.get("topic_match")
    if topic_match is not None:
        if not isinstance(topic_match, str) or topic_match not in ALLOWED_TOPIC_MATCH:
            issues.append(Issue(
                "warning", lineno, entry_id, "topic_match",
                f"Unknown topic_match {topic_match!r}. "
                f"Known: {', '.join(sorted(ALLOWED_TOPIC_MATCH))}.",
            ))
    consent_status = entry.get("consent_status")
    if consent_status is not None:
        if not isinstance(consent_status, str) or consent_status not in ALLOWED_CONSENT_STATUS:
            issues.append(Issue(
                "warning", lineno, entry_id, "consent_status",
                f"Unknown consent_status {consent_status!r}. "
                f"Known: {', '.join(sorted(ALLOWED_CONSENT_STATUS))}.",
            ))
    era = entry.get("era")
    if era is not None:
        if not isinstance(era, str) or era not in ALLOWED_ERA:
            issues.append(Issue(
                "warning", lineno, entry_id, "era",
                f"Unknown era {era!r}. "
                f"Known: {', '.join(sorted(ALLOWED_ERA))}.",
            ))
    impostor_for = entry.get("impostor_for")
    if impostor_for is not None and not (
        isinstance(impostor_for, list)
        and all(isinstance(s, str) for s in impostor_for)
    ):
        issues.append(Issue(
            "error", lineno, entry_id, "impostor_for",
            "impostor_for must be a list of strings (persona slugs "
            f"this impostor serves). Got {type(impostor_for).__name__}.",
        ))

    # Ratchet 1: impostor entries require the impostor metadata block.
    if corpus_role == "impostor":
        for required in (
            "impostor_for", "register_match", "topic_match",
            "consent_status", "era", "acquired_via",
        ):
            value = entry.get(required)
            missing = (
                value is None
                or (isinstance(value, str) and not value.strip())
                or (isinstance(value, list) and not value)
            )
            if missing:
                issues.append(Issue(
                    "error", lineno, entry_id, required,
                    f"Entry has corpus_role='impostor' but {required!r} "
                    f"is missing or empty. Impostor entries must carry "
                    f"the full impostor metadata block (impostor_for, "
                    f"register_match, topic_match, consent_status, era, "
                    f"acquired_via).",
                ))

    # Ratchet 3: impostor + undocumented consent → warn. Future
    # public-report harnesses should escalate this to a refusal
    # unless identities are anonymized and no raw text is emitted.
    if corpus_role == "impostor" and consent_status == "undocumented":
        issues.append(Issue(
            "warning", lineno, entry_id, "consent_status",
            "Entry has corpus_role='impostor' with consent_status="
            "'undocumented'. Public-report harnesses should refuse "
            "to name or quote this impostor unless the consent "
            "status is upgraded.",
        ))

    # Ratchet 4: impostor + post-AI-widespread era → warn. Post-2024
    # prose may include AI-collaborated writing that contaminates
    # the human-impostor signal.
    if corpus_role == "impostor" and era == "post_ai_widespread":
        issues.append(Issue(
            "warning", lineno, entry_id, "era",
            "Entry has corpus_role='impostor' with era="
            "'post_ai_widespread'. Impostor entries from this era may "
            "include AI-collaborated prose, which contaminates the "
            "human-impostor signal. Calibrate against current-era "
            "contemporaries only when intentional.",
        ))

    # Ratchet 5: identity_baseline (explicit or defaulted) + missing
    # era → informational warning, but only for entries that
    # actually feed impostor calibration. Era is meant to support
    # impostor-pool stratification (pre-ChatGPT vs. post-AI-
    # widespread); validation-only entries (use: validation) and
    # purely-excluded entries don't need it. This keeps the warning
    # signal-to-noise high.
    effective_role = corpus_role if isinstance(corpus_role, str) else "identity_baseline"
    impostor_relevant_uses = {
        "baseline", "voice_profile", "voice_validation",
        "idiolect", "voice_impostor",
    }
    use_set = set(use) if isinstance(use, list) else set()
    if (
        effective_role == "identity_baseline"
        and era is None
        and use_set & impostor_relevant_uses
    ):
        issues.append(Issue(
            "warning", lineno, entry_id, "era",
            "Entry has effective corpus_role='identity_baseline' and "
            "feeds impostor-relevant use(s) "
            f"({sorted(use_set & impostor_relevant_uses)}) but no "
            "'era' field. Recommended values: pre_chatgpt, "
            "pre_ai_widespread, post_ai_widespread, undated. Era "
            "supports impostor-pool stratification even on baseline "
            "entries.",
        ))

    # Ratchet 6: pre_ai_human claim on post-AI-widespread material →
    # warn, for ANY corpus_role. A pre_ai_human ai_status on an entry
    # dated post-2024 is exactly as suspect for an identity_baseline
    # entry (a writer's own corpus, the ground-truth anchor) as for an
    # impostor one — the module docstring's warning that a mistakenly
    # pre_ai_human-tagged AI-assisted entry can teach the baseline that
    # AI-smoothed text is the writer's unassisted voice applies
    # regardless of role. Ratchet 4 above covers only impostor entries
    # and carries no ai_status term; this is a separate, additive check,
    # NOT a modification of Ratchet 4 — the two compose (an impostor +
    # pre_ai_human + post_ai_widespread entry trips both).
    # Per internal/2026-07-09-manifest-validator-ai-status-era-ratchet-spec.md.
    if ai_status == "pre_ai_human" and era == "post_ai_widespread":
        issues.append(Issue(
            "warning", lineno, entry_id, "ai_status",
            "Entry has ai_status='pre_ai_human' with era="
            "'post_ai_widespread'. A pre-AI-human claim on post-2024 "
            "material is unverifiable and, if wrong, teaches the "
            "ground-truth baseline that AI-assisted prose is the "
            "writer's own unassisted voice. Re-check the ai_status, or "
            "the date this entry derives its era from.",
        ))

    return issues


# ---------- Whole-manifest driver ----------

def _validate_progress_options(progress_every: int, progress_stream: TextIO | None) -> None:
    if isinstance(progress_every, bool) or not isinstance(progress_every, int) or progress_every < 0:
        raise ValueError("progress_every must be a non-negative integer")
    if progress_every == 0 and progress_stream is not None:
        raise ValueError("progress_stream must be None when progress_every is 0")
    if progress_every > 0 and progress_stream is None:
        raise ValueError("progress_stream is required when progress_every is positive")


def _emit_progress(progress_stream: TextIO, *, phase: str, rows: int, entries: int,
                   started_at: float, issues: list[Issue] | None = None,
                   n_errors: int | None = None, n_warnings: int | None = None) -> None:
    elapsed = time.monotonic() - started_at
    if phase == "scan":
        message = (
            f"[{TOOL_NAME}] phase=scan rows={rows} entries={entries} "
            f"issues_so_far={len(issues or [])} elapsed_seconds={elapsed:.1f}"
        )
    else:
        message = (
            f"[{TOOL_NAME}] phase=complete rows={rows} entries={entries} "
            f"errors={n_errors} warnings={n_warnings} elapsed_seconds={elapsed:.1f}"
        )
    print(message, file=progress_stream, flush=True)


def validate_manifest(manifest_path: str | Path, *, progress_every: int = 0,
                      progress_stream: TextIO | None = None) -> dict[str, Any]:
    """Validate every line of the manifest and return a result dict.

    Importable: downstream tools that consume a manifest can call this
    and inspect the result before proceeding. The result includes a
    structured ``issues`` list (errors and warnings together) plus a
    summary of entries by register, ai_status, split, use, and privacy.
    """
    _validate_progress_options(progress_every, progress_stream)
    started_at = time.monotonic()
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
    # Impostor-corpus summary buckets (per the impostor-corpus spec).
    by_corpus_role: Counter[str] = Counter()
    by_era: Counter[str] = Counter()
    by_consent_status: Counter[str] = Counter()
    by_register_match: Counter[str] = Counter()
    # For ratchet 2 (cross-entry persona-reference check), record
    # registered personas from identity-baseline entries plus the
    # impostor entries we'll validate after the main pass.
    persona_to_registers: dict[str, set[str]] = {}
    impostor_entries: list[tuple[int, dict[str, Any]]] = []
    tripwires: list[dict[str, Any]] = []
    tripwires_seen: set[str] = set()
    rows_processed = 0

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
        # A progress row is one nonblank, noncomment JSONL candidate, whether it proves to be a
        # valid object, malformed JSON, or another JSON type. This keeps cadence tied to actual
        # scan work without letting bad input make the heartbeat disappear.
        rows_processed += 1
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(Issue(
                "error", lineno, None, None,
                f"Malformed JSON on line {lineno}: {exc.msg}.",
            ))
            if progress_every and rows_processed % progress_every == 0:
                assert progress_stream is not None
                _emit_progress(progress_stream, phase="scan", rows=rows_processed,
                               entries=n_entries, issues=issues, started_at=started_at)
            continue
        if not isinstance(entry, dict):
            issues.append(Issue(
                "error", lineno, None, None,
                f"Line {lineno} is not a JSON object (got "
                f"{type(entry).__name__}).",
            ))
            if progress_every and rows_processed % progress_every == 0:
                assert progress_stream is not None
                _emit_progress(progress_stream, phase="scan", rows=rows_processed,
                               entries=n_entries, issues=issues, started_at=started_at)
            continue

        n_entries += 1
        issues.extend(
            validate_entry(entry, lineno, path, seen_ids, seen_paths)
        )

        # Issue #6 schema-migration tripwire. One trigger per category
        # is enough; subsequent fires of the same category are skipped.
        entry_id_for_tripwire = (
            entry.get("id") if isinstance(entry.get("id"), str) else None
        )
        if "nested" not in tripwires_seen:
            for fname, fvalue in entry.items():
                if (
                    isinstance(fvalue, dict)
                    and fname not in TRIPWIRE_KNOWN_NESTED_FIELDS
                ):
                    tripwires_seen.add("nested")
                    tripwires.append({
                        "category": "nested",
                        "lineno": lineno,
                        "id": entry_id_for_tripwire,
                        "field": fname,
                        "message": (
                            f"Entry on line {lineno} has an unfamiliar "
                            f"nested-object field '{fname}'. The "
                            "handcrafted validator handles documented "
                            "nesting (e.g. `notes.composite_states`); "
                            "an unfamiliar nested field is the trigger "
                            "Issue #6 named for considering a "
                            "jsonschema-library migration."
                        ),
                    })
                    break
        if "versioned" not in tripwires_seen:
            for vfield in TRIPWIRE_VERSION_FIELDS:
                if vfield in entry:
                    tripwires_seen.add("versioned")
                    tripwires.append({
                        "category": "versioned",
                        "lineno": lineno,
                        "id": entry_id_for_tripwire,
                        "field": vfield,
                        "message": (
                            f"Entry on line {lineno} carries a "
                            f"version field '{vfield}'. Per-entry "
                            "versioning is the trigger Issue #6 named "
                            "for considering a jsonschema-library "
                            "migration."
                        ),
                    })
                    break
        if (
            "broad" not in tripwires_seen
            and len(entry) > TRIPWIRE_BROAD_FIELD_THRESHOLD
        ):
            tripwires_seen.add("broad")
            tripwires.append({
                "category": "broad",
                "lineno": lineno,
                "id": entry_id_for_tripwire,
                "field": None,
                "message": (
                    f"Entry on line {lineno} has {len(entry)} fields, "
                    f"above the threshold ({TRIPWIRE_BROAD_FIELD_THRESHOLD}). "
                    "Per-entry breadth is the trigger Issue #6 named "
                    "for considering a jsonschema-library migration."
                ),
            })

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
        # Impostor-corpus summary buckets.
        corpus_role_value = entry.get("corpus_role")
        if isinstance(corpus_role_value, str):
            by_corpus_role[corpus_role_value] += 1
        else:
            # Default-as-identity_baseline for the summary so the
            # bucket reflects the effective semantics the validator
            # uses everywhere else.
            by_corpus_role["identity_baseline"] += 1
        era_value = entry.get("era")
        if isinstance(era_value, str):
            by_era[era_value] += 1
        consent_value = entry.get("consent_status")
        if isinstance(consent_value, str):
            by_consent_status[consent_value] += 1
        register_match_value = entry.get("register_match")
        if isinstance(register_match_value, str):
            by_register_match[register_match_value] += 1

        # Build the persona->register map for ratchet 2. Only entries
        # whose effective corpus_role is identity_baseline contribute
        # registered personas; impostor entries are the queries
        # against this map.
        effective_role = (
            corpus_role_value
            if isinstance(corpus_role_value, str) else "identity_baseline"
        )
        if effective_role == "identity_baseline":
            persona_value = entry.get("persona")
            if isinstance(persona_value, str) and isinstance(register, str):
                persona_to_registers.setdefault(persona_value, set()).add(register)
        elif effective_role == "impostor":
            impostor_entries.append((lineno, entry))

        if progress_every and rows_processed % progress_every == 0:
            assert progress_stream is not None
            _emit_progress(progress_stream, phase="scan", rows=rows_processed,
                           entries=n_entries, issues=issues, started_at=started_at)

    # Ratchet 2: cross-entry persona-reference + cross-register
    # warnings. An impostor's `impostor_for` should name personas
    # that exist in the manifest's identity-baseline entries; a
    # high-register-match impostor whose own register doesn't
    # appear in any of the named target persona's registers is a
    # likely misconfiguration.
    for lineno_imp, imp_entry in impostor_entries:
        imp_id = (
            imp_entry.get("id") if isinstance(imp_entry.get("id"), str) else None
        )
        targets = imp_entry.get("impostor_for")
        if not isinstance(targets, list):
            continue
        for target in targets:
            if not isinstance(target, str):
                continue
            if target not in persona_to_registers:
                issues.append(Issue(
                    "warning", lineno_imp, imp_id, "impostor_for",
                    f"impostor_for references persona {target!r} but no "
                    f"identity-baseline entry in the manifest claims "
                    f"that persona. Either the slug is a typo or the "
                    f"target persona's baseline isn't in this manifest.",
                ))
        # High register-match should mean the impostor's own register
        # actually overlaps the target persona's register space.
        imp_register = imp_entry.get("register")
        register_match_imp = imp_entry.get("register_match")
        if (
            isinstance(imp_register, str)
            and register_match_imp == "high"
        ):
            for target in targets:
                if not isinstance(target, str):
                    continue
                target_registers = persona_to_registers.get(target)
                if target_registers is None or not target_registers:
                    continue
                if imp_register not in target_registers:
                    issues.append(Issue(
                        "warning", lineno_imp, imp_id, "register_match",
                        f"register_match='high' but impostor's register "
                        f"{imp_register!r} does not appear in the target "
                        f"persona {target!r}'s register set "
                        f"{sorted(target_registers)}. Lower register_match "
                        f"to 'medium' or 'low', or pick a target persona "
                        f"whose baseline includes this register.",
                    ))

    n_errors = sum(1 for i in issues if i.severity == "error")
    n_warnings = sum(1 for i in issues if i.severity == "warning")

    # Always emit a post-cross-entry completion record when progress is enabled. A scan heartbeat
    # at an exact multiple is necessarily provisional because cross-entry checks run below the row
    # loop; the completion record is the only heartbeat carrying final error/warning counts.
    if progress_every:
        assert progress_stream is not None
        _emit_progress(progress_stream, phase="complete", rows=rows_processed,
                       entries=n_entries, n_errors=n_errors, n_warnings=n_warnings,
                       started_at=started_at)

    return {
        "task_surface": TASK_SURFACE,
        "manifest_path": str(path),
        "n_entries": n_entries,
        "n_errors": n_errors,
        "n_warnings": n_warnings,
        "issues": [i.to_dict() for i in issues],
        "tripwires": tripwires,
        "summary": {
            "by_register": dict(by_register),
            "by_ai_status": dict(by_ai_status),
            "by_split": dict(by_split),
            "by_use": dict(by_use),
            "by_privacy": dict(by_privacy),
            "by_persona": dict(by_persona),
            "by_language_status": dict(by_language_status),
            "by_adversarial_class": dict(by_adversarial_class),
            "by_corpus_role": dict(by_corpus_role),
            "by_era": dict(by_era),
            "by_consent_status": dict(by_consent_status),
            "by_register_match": dict(by_register_match),
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

    if not errors and not warnings:
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
        "--progress-every", type=_nonnegative_int, default=1000, metavar="N",
        help="Emit a flushed aggregate stderr heartbeat every N manifest rows (default: 1000; "
             "use 0 to disable).",
    )
    args = parser.parse_args()

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
        "issues", "tripwires", "summary",
    ):
        if k in result:
            results_payload[k] = result[k]

    warnings: list[str] = []
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
