"""Phase B step 1: ingest operator paste-back outputs.

Takes Phase A's prompts/$RUN_ID/ directory (with its MANIFEST.json) plus a
paste-back directory of LLM outputs organized by family, and emits an
ingested.json record per family per window: raw text, normalized text,
normalization actions taken, caveats.

Output normalization is heuristic and conservative — every action is
recorded so the operator can audit. Refusals and truncations are flagged
but not auto-rejected; the operator decides how to handle them.

Implements SPEC_external_mirror_phase_b.md v0.1.

CLI:
    python3 ingest_outputs.py PROMPTS_DIR OUTPUTS_DIR [--out PATH] [--strict]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# Normalization
# ============================================================


_PREAMBLE_PATTERNS = [
    re.compile(r"^\s*(?:Sure!?|Of course!?|Certainly!?|Here(?:'s| is)(?: the| your)?\s+(?:continued?|continuation|text)[:.])[:.]?\s*", re.IGNORECASE),
    re.compile(r"^\s*Continuation[:\s]+", re.IGNORECASE),
    re.compile(r"^\s*Here(?:'s| is) (?:the )?(?:my )?(?:attempted? )?continuation[:\s]+", re.IGNORECASE),
    re.compile(r"^\s*(?:Continuing|Continued)(?: from where you left off)?[:.]?\s*", re.IGNORECASE),
]

_TRAILING_COMMENTARY_PATTERNS = [
    re.compile(r"\n\s*(?:Let me know|I hope|Note:|\(Continuing|Feel free|Would you like|Let me adjust).*$", re.IGNORECASE | re.DOTALL),
    re.compile(r"\n\s*---.*$", re.DOTALL),
]

_REFUSAL_PATTERNS = [
    re.compile(r"^\s*I (?:can(?:not|'?t)|am unable|won'?t|will not)\s+(?:help|continue|generate|complete|write|provide)", re.IGNORECASE),
    re.compile(r"^\s*As an AI", re.IGNORECASE),
    re.compile(r"^\s*I'?m sorry,? (?:but )?I", re.IGNORECASE),
    re.compile(r"^\s*Unfortunately,? I", re.IGNORECASE),
]

_CODE_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n```\s*$", re.DOTALL)


def normalize_output(raw: str, *, expected_words: int) -> tuple[str, list[str]]:
    """Normalize an LLM output and return (normalized_text, actions_taken).

    Conservative: only strips clear-cut preambles, code fences, quotation
    wrappers, and trailing commentary. Records every action for audit.
    Refusals are flagged but not stripped — the operator inspects raw text.
    """
    actions: list[str] = []
    text = raw

    fence_match = _CODE_FENCE_RE.match(text.strip())
    if fence_match:
        text = fence_match.group(1)
        actions.append("stripped_code_fence")

    for pat in _PREAMBLE_PATTERNS:
        m = pat.match(text)
        if m and m.end() < 200:
            text = text[m.end():]
            actions.append(f"stripped_preamble:{pat.pattern[:40]}")
            break

    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ('"', "'", "“"):
        if stripped[0] == "“" and stripped[-1] == "”":
            text = stripped[1:-1]
            actions.append("stripped_curly_quotes")
        elif stripped[0] in ('"', "'") and stripped[-1] in ('"', "'"):
            text = stripped[1:-1]
            actions.append("stripped_quotes")

    for pat in _TRAILING_COMMENTARY_PATTERNS:
        m = pat.search(text)
        if m:
            text = text[:m.start()]
            actions.append(f"stripped_trailing:{pat.pattern[:40]}")
            break

    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = text.strip()

    return text, actions


def detect_refusal(text: str) -> bool:
    head = text[:200].strip()
    return any(pat.match(head) for pat in _REFUSAL_PATTERNS)


def count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


# ============================================================
# Window record
# ============================================================


@dataclass
class WindowRecord:
    family: str
    window_index: int
    source_file: str
    raw_text: str
    normalized_text: str
    normalized_word_count: int
    normalization_actions: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


# ============================================================
# Format detection + parsing
# ============================================================


_WINDOW_FILE_RE = re.compile(r"^window_(\d+)\.(txt|md)$")


# ============================================================
# Per-family metadata (v0.2: cutoff / interface / blinding / control)
# ============================================================
#
# Per SPEC_external_mirror_discrimination.md v0.2 §JSON schema. A family's
# output directory may contain an optional `family.json` declaring either a
# mirror-panel block (training_cutoff_date / interface /
# orchestration_layer_blinding) or a control block (publication_date /
# cutoff_precedes_publication / visibility_class), or both. Absent file =
# v0.1 behavior (no metadata, no derived caveats).

_VALID_INTERFACES = frozenset({
    "manual_fresh_chat",
    "codex_serial_agent",
    "claude_code_task_tool",
    "other",
})
_VALID_BLINDING = frozenset({"isolated", "partial", "not_blinded"})
_VALID_VISIBILITY = frozenset({"low", "medium", "high"})


def _load_family_metadata(family_dir: Path) -> tuple[dict | None, list[str]]:
    """Load optional family.json. Returns (metadata, caveats).

    Both halves of the return value can be empty. Validation errors append
    caveats but do not raise — the operator gets the v0.1 distance output
    plus a warning. A missing file is not an error.
    """
    fpath = family_dir / "family.json"
    if not fpath.exists():
        return None, []
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"family_json_parse_failed:{exc}"]
    if not isinstance(data, dict):
        return None, ["family_json_not_an_object"]

    caveats: list[str] = []
    cleaned: dict = {}

    mp = data.get("mirror_panel")
    if mp is not None:
        if not isinstance(mp, dict):
            caveats.append("family_json_mirror_panel_not_an_object")
        else:
            interface = mp.get("interface")
            blinding = mp.get("orchestration_layer_blinding")
            if interface is not None and interface not in _VALID_INTERFACES:
                caveats.append(f"family_json_unknown_interface:{interface}")
            if blinding is not None and blinding not in _VALID_BLINDING:
                caveats.append(f"family_json_unknown_blinding:{blinding}")
            # v0.2: once a mirror_panel block is present, the SPEC requires
            # training_cutoff_date, interface, and orchestration_layer_blinding
            # (the three load-bearing identification + blinding fields). A
            # present-but-incomplete block should not look clean to the auditor.
            # Other mirror_panel fields are advisory or trigger their own
            # caveat in derive_metadata_caveats.
            for required in (
                "training_cutoff_date",
                "interface",
                "orchestration_layer_blinding",
            ):
                if mp.get(required) is None:
                    caveats.append(
                        f"family_json_missing_required_field:mirror_panel.{required}"
                    )
            cleaned["mirror_panel"] = {
                "training_cutoff_date": mp.get("training_cutoff_date"),
                "interface": interface,
                "orchestration_layer_blinding": blinding,
                # v0.2 codex-rerun additions:
                "nominal_family": mp.get("nominal_family"),
                "reasoning_mode": mp.get("reasoning_mode"),
                "web_search_enabled": mp.get("web_search_enabled"),
            }

    ctrl = data.get("control")
    if ctrl is not None:
        if not isinstance(ctrl, dict):
            caveats.append("family_json_control_not_an_object")
        else:
            visibility = ctrl.get("visibility_class")
            if visibility is not None and visibility not in _VALID_VISIBILITY:
                caveats.append(f"family_json_unknown_visibility:{visibility}")
            # v0.2: once a control block is present, the SPEC requires
            # publication_date, cutoff_precedes_publication, and visibility_class.
            for required in (
                "publication_date",
                "cutoff_precedes_publication",
                "visibility_class",
            ):
                if ctrl.get(required) is None:
                    caveats.append(
                        f"family_json_missing_required_field:control.{required}"
                    )
            cleaned["control"] = {
                "publication_date": ctrl.get("publication_date"),
                "cutoff_precedes_publication": ctrl.get("cutoff_precedes_publication"),
                "visibility_class": visibility,
            }

    if not cleaned:
        caveats.append("family_json_present_but_no_recognized_blocks")
        return None, caveats
    return cleaned, caveats


def derive_metadata_caveats(
    metadata: dict | None,
    *,
    family_name: str | None = None,
) -> list[str]:
    """Return v0.2 caveat tags implied by family metadata.

    Pure function — easy to test. Returns the controlled-vocabulary tags
    from SPEC v0.2 caveats_explicitly_named.

    From the v0.2 base refinements:
      - orchestration_layer_not_fully_blinded: blinding is partial / not_blinded
      - control_visibility_high_refresh_risk: visibility_class == high
      - control_predates_cutoff_gap_conservative: cutoff_precedes_publication is False

    From the codex-rerun refinements (caveats #10–#12):
      - reasoning_mode_variant_used: mirror_panel.reasoning_mode is True
      - web_search_not_disabled: mirror_panel block present but
        web_search_enabled is not explicitly False (True or None, the
        operator failed to verify)
      - effective_model_differs_from_nominal: mirror_panel.nominal_family is
        set and differs from family_name (the directory name)

    Backwards-compat note: caveats #10–#12 only fire when a mirror_panel
    block is present in family.json. A family with no family.json emits
    no derived caveats (v0.1 behavior).
    """
    if not metadata:
        return []
    out: list[str] = []
    mp = metadata.get("mirror_panel") or {}
    blinding = mp.get("orchestration_layer_blinding")
    if blinding in ("partial", "not_blinded"):
        out.append("orchestration_layer_not_fully_blinded")
    if mp:
        if mp.get("reasoning_mode") is True:
            out.append("reasoning_mode_variant_used")
        if mp.get("web_search_enabled") is not False:
            out.append("web_search_not_disabled")
        nominal = mp.get("nominal_family")
        if nominal and family_name and nominal != family_name:
            out.append("effective_model_differs_from_nominal")
    ctrl = metadata.get("control") or {}
    if ctrl.get("visibility_class") == "high":
        out.append("control_visibility_high_refresh_risk")
    if ctrl.get("cutoff_precedes_publication") is False:
        out.append("control_predates_cutoff_gap_conservative")
    return out


def parse_t4_batched(path: Path) -> list[tuple[int, str]]:
    """Parse a T4 batched-format file. Returns [(window_index, raw_text), ...].

    Tolerates code-fence-wrapped JSON; raises ValueError on unrecoverable
    parse failure.
    """
    raw = path.read_text(encoding="utf-8")
    cleaned = raw.strip()

    fence_match = _CODE_FENCE_RE.match(cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"could not parse {path.name} as JSON: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"{path.name}: expected JSON array, got {type(data).__name__}")

    pairs = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"{path.name}: entry {i} is not an object")
        if "window" not in entry or "continuation" not in entry:
            raise ValueError(f"{path.name}: entry {i} missing 'window' or 'continuation'")
        pairs.append((int(entry["window"]), str(entry["continuation"])))
    return pairs


def parse_t3_separate(family_dir: Path) -> list[tuple[int, str, Path]]:
    """Parse a directory of T3 separate-format files.

    Returns [(window_index, raw_text, source_path), ...]
    """
    out = []
    for p in sorted(family_dir.iterdir()):
        if not p.is_file():
            continue
        m = _WINDOW_FILE_RE.match(p.name)
        if not m:
            continue
        idx = int(m.group(1))
        out.append((idx, p.read_text(encoding="utf-8"), p))
    return out


def ingest_family(
    family_dir: Path, manifest_windows_count: int
) -> tuple[list[WindowRecord], list[str], dict | None]:
    """Ingest one family's outputs. Returns (records, family_caveats, metadata)."""
    family = family_dir.name
    caveats: list[str] = []

    metadata, meta_caveats = _load_family_metadata(family_dir)
    caveats.extend(meta_caveats)

    t4_file = family_dir / "windows_batched.json"
    t3_files = list(family_dir.glob("window_*.txt")) + list(family_dir.glob("window_*.md"))

    records: list[WindowRecord] = []

    if t4_file.exists() and t3_files:
        caveats.append("both_t3_and_t4_present_preferred_t4")
    if t4_file.exists():
        try:
            pairs = parse_t4_batched(t4_file)
        except ValueError as exc:
            caveats.append(f"t4_parse_failed:{exc}")
            return records, caveats, metadata
        for idx, raw in pairs:
            records.append(_build_record(family, idx, str(t4_file), raw, manifest_windows_count, caveats_out=caveats))
    elif t3_files:
        for idx, raw, src in parse_t3_separate(family_dir):
            records.append(_build_record(family, idx, str(src), raw, manifest_windows_count, caveats_out=caveats))
    else:
        caveats.append("no_outputs_found")

    return records, caveats, metadata


def _build_record(family: str, idx: int, source: str, raw: str, manifest_windows_count: int, *, caveats_out: list[str]) -> WindowRecord:
    if not (1 <= idx <= manifest_windows_count):
        caveats_out.append(f"window_index_{idx}_out_of_range")
    record = WindowRecord(
        family=family,
        window_index=idx,
        source_file=source,
        raw_text=raw,
        normalized_text="",
        normalized_word_count=0,
    )
    if detect_refusal(raw):
        record.caveats.append("refused")
        record.normalized_text = ""
        record.normalized_word_count = 0
        return record
    normalized, actions = normalize_output(raw, expected_words=0)
    record.normalized_text = normalized
    record.normalized_word_count = count_words(normalized)
    record.normalization_actions = actions
    if record.normalized_word_count == 0 and not normalized.strip():
        record.caveats.append("empty_output")
    return record


# ============================================================
# Main
# ============================================================


def ingest(prompts_dir: Path, outputs_dir: Path, strict: bool) -> dict:
    """Ingest all families. Returns the ingested.json payload."""
    manifest_path = prompts_dir / "MANIFEST.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"PROMPTS_DIR must contain a MANIFEST.json (looked at {manifest_path}).")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if not outputs_dir.exists():
        raise FileNotFoundError(f"OUTPUTS_DIR does not exist: {outputs_dir}")

    family_dirs = sorted(p for p in outputs_dir.iterdir() if p.is_dir())
    if not family_dirs:
        raise ValueError(f"OUTPUTS_DIR has no family subdirectories: {outputs_dir}")

    families_payload: list[dict] = []
    global_caveats: list[str] = []
    expected_word_count = manifest.get("continuation", 0)
    truncation_threshold = expected_word_count * 0.5
    windows_count = manifest.get("windows_count", 0)

    family_metadata: dict[str, dict] = {}
    derived_metadata_caveats: list[str] = []

    for family_dir in family_dirs:
        records, family_caveats, metadata = ingest_family(family_dir, windows_count)

        seen = {r.window_index for r in records}
        expected = set(range(1, windows_count + 1))
        missing = sorted(expected - seen)
        if missing:
            if strict:
                raise ValueError(f"family {family_dir.name} missing windows {missing} (strict mode)")
            family_caveats.append(f"missing_windows:{missing}")

        for r in records:
            if "refused" in r.caveats:
                continue
            if r.normalized_word_count < truncation_threshold and "empty_output" not in r.caveats:
                r.caveats.append(f"truncated:{r.normalized_word_count}<{int(truncation_threshold)}")

        family_payload: dict = {
            "family": family_dir.name,
            "caveats": family_caveats,
            "windows": [asdict(r) for r in records],
        }
        if metadata is not None:
            family_payload["metadata"] = metadata
            family_metadata[family_dir.name] = metadata
            derived_metadata_caveats.extend(
                derive_metadata_caveats(metadata, family_name=family_dir.name)
            )

        families_payload.append(family_payload)
        global_caveats.extend(f"{family_dir.name}:{c}" for c in family_caveats)

    # De-dup derived caveats; they are controlled-vocabulary tags from SPEC v0.2.
    seen_derived = set()
    deduped_derived = []
    for c in derived_metadata_caveats:
        if c not in seen_derived:
            seen_derived.add(c)
            deduped_derived.append(c)

    return {
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "prompts_dir": str(prompts_dir.resolve()),
        "outputs_dir": str(outputs_dir.resolve()),
        "manifest": {
            "run_id": manifest.get("run_id"),
            "target_sha256": manifest.get("target_sha256"),
            "target_path": manifest.get("target_path"),
            "target_word_count": manifest.get("target_word_count"),
            "positioning": manifest.get("positioning"),
            "windows_count": windows_count,
            "continuation": expected_word_count,
            "windows": manifest.get("windows", []),
        },
        "families": families_payload,
        "family_metadata": family_metadata,
        "derived_caveats": deduped_derived,
        "caveats": global_caveats,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase B step 1: ingest operator paste-back outputs."
    )
    parser.add_argument("prompts_dir", help="Phase A prompts/$RUN_ID/ directory")
    parser.add_argument("outputs_dir", help="outputs/$RUN_ID/ with family subdirs")
    parser.add_argument("--out", default=None, help="Output JSON path (default: OUTPUTS_DIR/ingested.json)")
    parser.add_argument("--strict", action="store_true", help="Error on any missing window in any family")
    args = parser.parse_args(argv)

    try:
        payload = ingest(Path(args.prompts_dir), Path(args.outputs_dir), strict=args.strict)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else Path(args.outputs_dir) / "ingested.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Ingested {len(payload['families'])} family/families to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
