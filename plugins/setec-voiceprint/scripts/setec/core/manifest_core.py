"""Manifest validation core: schema constants, path resolution and validate_manifest.

Moved verbatim from ``setec.surfaces.manifest_validator`` (CB-5) so calibration and
surface modules can validate a manifest without importing that surface.
``manifest_validator`` re-exports every name here as the same object. Stdlib plus
``setec.core.register_taxonomy`` only.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterator, TextIO

from setec.core.register_taxonomy import PROFILE_ONLY_REGISTERS


# Task-surface tag. The validator is a validation-spine tool, distinct
# from the smoothing-diagnosis and voice-coherence scripts.
# The manifest_validator surface tag, used in the envelopes built here. Not
# named TASK_SURFACE: this core module is not a surface.
_VALIDATION_SURFACE = "validation"
TOOL_NAME = "manifest_validator"



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
    "legal_brief", "grant_proposal", "grant_proposal_academic",
    "grant_proposal_nonprofit", "expert_affidavit",
    "regulatory_comment", "professional_letter", "teaching",
    "message.imessage", "message.facebook_messenger",
    "social_media_twitter", "forum_metafilter",
    "social_media_facebook_posts", "social_media_facebook_comments",
}
# The retired umbrella conflated public composed posts with public responsive
# comments. It is a hard error rather than an extensible-vocabulary warning,
# so new rows cannot silently preserve the ambiguity.
RETIRED_REGISTERS = {"social_media_facebook"}



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
# ``source_family`` is a deliberately small, operator-declared analytic
# axis. It is not a path, an acquisition mechanism, or a substitute for
# document-level ``source_id`` provenance. Unlike the extensible legacy enum
# fields above, this initial vocabulary is closed so a typo cannot silently
# split a corpus slice.
ALLOWED_SOURCE_FAMILY = {
    "facebook", "metafilter", "wordpress", "unclassified",
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
# `passage_dedup` (spec 36) is the second documented nested field: every row of a
# `near_dup_dedup --passages --out` export carries it, and the handcrafted
# validator already covers it (it is a provenance/marker object with no per-field
# semantics the validator asserts). Registering it here keeps the Issue #6
# "unfamiliar nested shape" trigger meaningful instead of firing on every export.
TRIPWIRE_KNOWN_NESTED_FIELDS = frozenset({"notes", "passage_dedup"})

# All recognized field names. Unknown fields generate warnings.
KNOWN_FIELDS = {
    "id", "path", "project_area", "author", "persona", "register",
    "genre", "date_written", "ai_status", "editing_status",
    "word_count", "use", "split", "privacy", "source", "notes",
    "language_status",
    "adversarial_class", "source_id", "source_family", "transform",
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
    # Passage-level hygiene marker (spec 36). Stamped on every row of a
    # `near_dup_dedup --passages --out` export: source doc id + ordinal + char
    # offsets + the params echo. Registration only — the validator asserts nothing
    # about its contents. It is load-bearing for `pool_guard`'s refusal at the
    # duplicate-dependent set-level-diversity pools, so it must not read as an
    # unknown stray field.
    "passage_dedup",
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



def _relative_candidates(manifest: Path, raw: str) -> tuple[Path, ...]:
    """The frozen 3-candidate relative-path resolve order.

    Shared by ``resolve_path`` (existence-probing, warning-tolerant) and the
    H2 register-sweep projection seam (safety-checked, refusal-on-unsafe).
    Both must try candidates in exactly this order for a relative path.
    """
    p = Path(raw)
    return (
        manifest.parent / p,
        manifest.parent.parent / p,
        Path.cwd() / p,
    )


def resolve_path(manifest: Path, raw: str) -> Path:
    """Match ``stylometry_core.resolve_manifest_path`` so the validator
    accepts the same path conventions the readers do."""
    p = Path(raw)
    if p.is_absolute():
        return p
    candidates = _relative_candidates(manifest, raw)
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]



class ManifestParseError(Exception):
    """A malformed-manifest failure at the shared strict-parser layer.

    Carries no manifest byte, path, or caught-exception text beyond a fixed
    category description; callers that must not disclose input content wrap
    this in a controlled refusal instead of surfacing ``str(exc)``.
    """


def _strict_row_pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ManifestParseError("duplicate object key")
        out[key] = value
    return out


def _reject_non_finite_constant(name: str) -> None:
    raise ManifestParseError(f"non-finite JSON constant {name!r}")


def parse_manifest_row(line: str) -> Any:
    """Strictly parse one JSONL manifest data row.

    Rejects duplicate object keys at any nesting depth (``object_pairs_hook``
    fires for every object ``json.loads`` constructs, including nested ones)
    and non-finite numeric constants (``NaN``, ``Infinity``, ``-Infinity``).
    Raises :class:`ManifestParseError` on malformed JSON syntax or either
    strict-hook rejection. Returns the parsed JSON value (of any type)
    otherwise unvalidated: callers decide what to do with a non-object row.
    """
    try:
        return json.loads(
            line,
            object_pairs_hook=_strict_row_pairs_hook,
            parse_constant=_reject_non_finite_constant,
        )
    except ManifestParseError:
        raise
    except json.JSONDecodeError as exc:
        raise ManifestParseError(exc.msg) from exc
    except (RecursionError, ValueError) as exc:
        raise ManifestParseError(exc.__class__.__name__) from exc


def decode_manifest_bytes(data: bytes) -> str:
    """Strictly decode one manifest's raw bytes: reject a BOM or non-UTF-8.

    Shared by the legacy full-row validator (given the bytes it reads from
    disk) and the H2 projection seam (given the runner's already-bounded-read
    bytes; the manifest is never reopened or reread).
    """
    if type(data) is not bytes:
        raise ManifestParseError("manifest bytes must be bytes")
    if data[:3] == b"\xef\xbb\xbf":
        raise ManifestParseError("manifest bytes begin with a UTF-8 BOM")
    try:
        return data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ManifestParseError("manifest bytes are not valid UTF-8") from exc


#: Every character ``str.splitlines()`` treats as a row terminator that a strict
#: JSONL row boundary must not: CR, VT, FF, FS, GS, RS, the C1 NEL, and the two
#: Unicode line/paragraph separators.
FORBIDDEN_ROW_BREAKS = "\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"


def _manifest_data_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(lineno, line)`` for nonblank, noncomment JSONL data rows.

    Shared line-splitting rule: 1-based line numbers over a split on ``"\n"``
    ALONE, skipping blank lines and lines whose stripped form starts with ``#``.

    ``str.splitlines()`` also breaks on the characters in
    :data:`FORBIDDEN_ROW_BREAKS`, so one physical JSONL row carrying any of them
    would silently become two logical rows -- and under Spec 73 / H2 the row
    count and every row's ordinal are baked into frozen digests. Those
    characters are refused here, at the LINE level and before any JSON parsing,
    so an unowned field cannot smuggle a row split past the owned fields'
    string domain. (Separately named strict-parser hardening.)

    One deliberate carve-in: a ``"\r\n"`` pair is a single Windows line
    terminator, not an embedded break -- text-mode writers on native Windows
    (``open(..., "w")``, ``Path.write_text``) translate ``"\n"`` to it, and
    the pre-hardening ``str.splitlines()`` always treated it as one break.
    Exactly one trailing ``"\r"`` per line is therefore stripped before the
    refusal check; a bare ``"\r"``, an interior ``"\r"``, or any other
    character in :data:`FORBIDDEN_ROW_BREAKS` still refuses.
    """
    for lineno, raw in enumerate(text.split("\n"), start=1):
        if raw.endswith("\r"):
            raw = raw[:-1]
        for char in FORBIDDEN_ROW_BREAKS:
            if char in raw:
                raise ManifestParseError(
                    "manifest row carries a forbidden line-break character"
                )
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield lineno, line



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
    if isinstance(register, str) and register in RETIRED_REGISTERS:
        issues.append(Issue(
            "error", lineno, entry_id, "register",
            f"Retired register '{register}' must be replaced by a split leaf.",
        ))
    elif isinstance(register, str) and register == "grant_proposal":
        issues.append(Issue(
            "warning", lineno, entry_id, "register",
            "Deprecated register 'grant_proposal'; use "
            "'grant_proposal_academic' or 'grant_proposal_nonprofit'.",
        ))
    elif isinstance(register, str) and register not in ALLOWED_REGISTER:
        issues.append(Issue(
            "warning", lineno, entry_id, "register",
            f"Unknown register '{register}'. "
            f"Known values: {', '.join(sorted(ALLOWED_REGISTER))}.",
        ))
    elif register is not None and not isinstance(register, str):
        issues.append(Issue(
            "error", lineno, entry_id, "register",
            f"Register must be a string, got {type(register).__name__}.",
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
    source_family = entry.get("source_family")
    if "source_family" in entry:
        if not isinstance(source_family, str):
            issues.append(Issue(
                "error", lineno, entry_id, "source_family",
                "source_family must be a string when present. "
                f"Got {type(source_family).__name__}.",
            ))
        elif not source_family or source_family != source_family.strip():
            issues.append(Issue(
                "error", lineno, entry_id, "source_family",
                "source_family must be a non-empty, unpadded string.",
            ))
        elif source_family not in ALLOWED_SOURCE_FAMILY:
            issues.append(Issue(
                "error", lineno, entry_id, "source_family",
                f"Unknown source_family '{source_family}'. "
                f"Allowed values: {', '.join(sorted(ALLOWED_SOURCE_FAMILY))}.",
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
                if not isinstance(u, str):
                    issues.append(Issue(
                        "error", lineno, entry_id, "use",
                        f"Use tag must be a string, got {type(u).__name__}.",
                    ))
                elif u not in ALLOWED_USE:
                    issues.append(Issue(
                        "warning", lineno, entry_id, "use",
                        f"Unknown use tag '{u}'. "
                        f"Known: {', '.join(sorted(ALLOWED_USE))}.",
                    ))
    # Set operations below hash their operands. A non-string tag is already
    # reported above, so drop it here rather than let it reach a hash and
    # abort the run on the malformed input this validator exists to report.
    use_tags = [u for u in use if isinstance(u, str)] if isinstance(use, list) else None

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
    # isinstance before the frozenset test: membership hashes its left
    # operand, so an unhashable register aborted the whole run here.
    if (
        isinstance(register, str)
        and register in PROFILE_ONLY_REGISTERS
        and isinstance(use, list)
        and "baseline" in use
    ):
        issues.append(Issue(
            "error", lineno, entry_id, "use",
            f"{register} is a profile-only private-dyadic register and must "
            "not carry baseline use or enter the pooled author reference.",
        ))

    # Privacy ratchet for voiceprint sources. A voice profile or
    # idiolect corpus is a voice-cloning input; sources should be marked
    # explicitly private. Any value other than 'private', including a
    # missing field or a non-string value, fails the ratchet: silence
    # is not consent for a voice-cloning source.
    voiceprint_uses = {"voice_profile", "idiolect"}
    found_voiceprint_uses = (
        sorted(voiceprint_uses.intersection(use_tags))
        if use_tags is not None
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
    use_set = set(use_tags) if use_tags is not None else set()
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

    def _early_bail_out(message: str) -> dict[str, Any]:
        # Honor the unconditional completion heartbeat even on an early bail-out: emit a
        # phase=complete record (no rows scanned, one error) before returning when progress is on.
        if progress_every:
            assert progress_stream is not None
            _emit_progress(progress_stream, phase="complete", rows=0,
                           entries=0, n_errors=1, n_warnings=0,
                           started_at=started_at)
        return {
            "task_surface": _VALIDATION_SURFACE,
            "manifest_path": str(path),
            "n_entries": 0,
            "n_errors": 1,
            "n_warnings": 0,
            "issues": [{
                "severity": "error",
                "lineno": 0,
                "id": None,
                "field": None,
                "message": message,
            }],
            "summary": {},
        }

    if not path.exists():
        return _early_bail_out(f"Manifest file '{path}' does not exist.")

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        return _early_bail_out(f"Could not read manifest: {exc}.")

    try:
        # Shared strict decode (Spec 73 / H2): also used by
        # project_register_sweep_manifest_bytes below. A BOM or non-UTF-8
        # manifest previously either crashed (read_text's implicit strict
        # UTF-8) or silently mis-parsed the first line; both now bail out
        # the same way an unreadable file does, with a fixed message.
        text = decode_manifest_bytes(raw_bytes)
    except ManifestParseError:
        return _early_bail_out("Could not read manifest: not valid UTF-8 JSONL text.")

    try:
        data_lines = list(_manifest_data_lines(text))
    except ManifestParseError:
        return _early_bail_out(
            "Could not read manifest: a row carries a forbidden line-break "
            "character."
        )

    for lineno, line in data_lines:
        # A progress row is one nonblank, noncomment JSONL candidate, whether it proves to be a
        # valid object, malformed JSON, or another JSON type. This keeps cadence tied to actual
        # scan work without letting bad input make the heartbeat disappear.
        rows_processed += 1
        try:
            entry = parse_manifest_row(line)
        except ManifestParseError as exc:
            issues.append(Issue(
                "error", lineno, None, None,
                f"Malformed JSON on line {lineno}: {exc}.",
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
        "task_surface": _VALIDATION_SURFACE,
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
