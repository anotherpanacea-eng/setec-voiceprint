"""Closed, descriptive lexical artifact census and private calibration."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from typing import Mapping, Sequence

import preprocessing

from .common import (
    Manifest, Record, Refusal, Snapshot, WorkBudget, canonical_json, collapse_strata,
    coordination_label, domain_hash, exact_keys, parse_json, plain_hash,
    read_bounded, record_set_sha256, require_hex, validate_coordination_strata,
    POLICY_LIMIT,
)

TOOL = "setec.preflight.artifacts"
POLICY_SCHEMA = "setec-preflight-artifact-policy/1"
DETAIL_SCHEMA = "setec-preflight-artifact-detail/1"
RECEIPT_SCHEMA = "setec-preflight-artifact-receipt/1"
CAL_DETAIL_SCHEMA = "setec-preflight-artifact-calibration-detail/1"
CAL_RECEIPT_SCHEMA = "setec-preflight-artifact-calibration/1"
LABEL_SCHEMA = "setec-preflight-artifact-labels/1"
LABEL_ROW_SCHEMA = "setec-preflight-artifact-label-row/1"
CATEGORIES = ("markup", "apparatus", "verse", "encoding_normalization")
ARTIFACT_TYPES = (
    "markup_html", "markup_css", "markup_script", "navigation_boilerplate",
    "markdown_apparatus", "tei_xml_apparatus", "footnote_definition",
    "page_header", "line_number", "ocr_hyphenation", "running_head",
    "truncation_marker", "unbalanced_fence", "verse_likely",
    "replacement_character", "private_use_character",
)
CATEGORY_OF = {name: ("markup" if index < 4 else "apparatus" if index < 13
                      else "verse" if index == 13 else "encoding_normalization")
               for index, name in enumerate(ARTIFACT_TYPES)}
DISPOSITIONS = ("allow", "annotate", "review", "refuse")
OUTCOMES = ("none", *DISPOSITIONS)
CAL_OUTCOMES = (*OUTCOMES, "intake_refusal")
VARIATION_CLASSES = ("dialect", "archaic_spelling", "purposeful_roughness",
                     "unusual_valid_syntax", "verse", "legitimate_apparatus",
                     "quotation", "other")
CONTROL_CLASSES = VARIATION_CLASSES[:4]
CEILINGS = {"lines": 8_000_000, "observations": 1_000_000,
            "running-head keys": 2_000_000, "CSS block lines": 20_000_000}
DETAIL_LIMIT = 256 * 1024 * 1024
RECEIPT_LIMIT = 64 * 1024
LABEL_LIMIT = 1024 * 1024
WS = "".join(chr(code) for code in (*range(9, 14), 32, 0x85, 0xA0,
                                       0x1680, *range(0x2000, 0x200B),
                                       0x2028, 0x2029, 0x202F, 0x205F, 0x3000))
WS_RE = re.compile("[" + re.escape(WS) + "]+")
HTML_TAG = next(pattern for name, pattern, _ in preprocessing.PREPROCESSING_RULES
                if name == "html_tag")
SCRIPT_OPEN = re.compile(r"<script(?:\s|>)")
SCRIPT_CLOSE = re.compile(r"</script\s*>")
SCRIPT_EVENT = re.compile(r"\bon[a-z]+\s*=")
MARKDOWN = re.compile(
    r"^ {0,3}(?:#{1,6}\s|[-*_](?:\s*[-*_]){2,}\s*$|>\s|(?:[-+*]|[0-9]+\.)\s|!\[[^\]\n]*\]\([^\n)]*\))")
TEI = re.compile(r"</?(?:TEI|teiHeader|text|body|div|pb|lb|note|app|lem|rdg)(?:\s|/?>)")
FOOTNOTE = re.compile(r"^ {0,3}(?:\[\^[^\]\n]+\]:|\[(?:[0-9]{1,4}|[A-Za-z])\]\s+)")
PAGE = re.compile(r"^(?:page\s+)?[0-9]{1,6}(?:\s+of\s+[0-9]{1,6})?$")
NUMBER = re.compile(r"^[0-9]{1,6}$")
OCR_END = re.compile(r"[A-Za-z]-$")
OCR_START = re.compile(r"^[a-z]")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ASCII_LETTER = re.compile(r"[A-Za-z]")
VERSE_PUNCT = frozenset(".?!:;")
NAVIGATION = frozenset(("home", "previous", "next", "contents", "table of contents",
                        "back to top", "skip to content", "menu", "navigation"))
TRUNCATION = frozenset(("[truncated]", "[text truncated]", "...", "…"))


def _descriptor() -> dict:
    patterns = {
        "markup_html": (HTML_TAG,), "markup_css": (preprocessing.CSS_AT_RE,),
        "markup_script": (SCRIPT_OPEN, SCRIPT_CLOSE, SCRIPT_EVENT),
        "markdown_apparatus": (MARKDOWN,), "tei_xml_apparatus": (TEI,),
        "footnote_definition": (FOOTNOTE,), "page_header": (PAGE,),
        "line_number": (NUMBER,), "ocr_hyphenation": (OCR_END, OCR_START),
        "running_head": (ASCII_LETTER,),
        "truncation_marker": (), "unbalanced_fence": (FENCE,),
    }
    limits = {"markup_css": {"max_block_lines": 50},
              "page_header": {"max_digits": 6},
              "running_head": {"min_scalars": 3, "max_scalars": 80,
                               "min_paragraphs": 3},
              "verse_likely": {"min_lines": 3, "max_lines": 40,
                               "min_tokens": 12, "max_line_tokens": 12,
                               "fraction_numerator": 4, "fraction_denominator": 5}}
    return {"detector_version": 1,
            "whitespace": [[ord(char), ord(char)] for char in WS],
            "rows": [{"artifact_type": name, "category": CATEGORY_OF[name],
                      "patterns": [[pattern.pattern, pattern.flags]
                                   for pattern in (*patterns.get(name, ()), WS_RE)],
                      "constants": limits.get(name, {})}
                     for name in ARTIFACT_TYPES]}


DETECTOR_DESCRIPTOR = _descriptor()
DETECTOR_SHA256 = domain_hash("setec-preflight-artifact-detectors-v1",
                              canonical_json(DETECTOR_DESCRIPTOR))


@dataclass(frozen=True)
class Observation:
    artifact_type: str
    category: str
    line: int
    paragraph: int


@dataclass(frozen=True)
class ArtifactPolicy:
    dispositions: Mapping[str, str]
    coordination_strata: tuple[str, ...]
    policy_sha256: str


@dataclass(frozen=True)
class CensusResult:
    detail: dict
    receipt: dict


@dataclass(frozen=True)
class CalibrationResult:
    detail: dict
    receipt: dict


def load_artifact_policy(path: Path) -> ArtifactPolicy:
    return parse_artifact_policy(read_bounded(path.parent, path.name, POLICY_LIMIT))


def parse_artifact_policy(snapshot: Snapshot) -> ArtifactPolicy:
    """Validate policy bytes already read under ``POLICY_LIMIT``."""
    value = exact_keys(parse_json(snapshot.data, "policy_contract"),
                       {"schema", "dispositions", "coordination_strata"}, "policy_contract")
    if value["schema"] != POLICY_SCHEMA:
        raise Refusal("policy_contract")
    dispositions = exact_keys(value["dispositions"], set(CATEGORIES), "policy_contract")
    if (any(type(item) is not str or item not in DISPOSITIONS
            for item in dispositions.values()) or dispositions["verse"] == "refuse"):
        raise Refusal("policy_contract")
    strata = validate_coordination_strata(value["coordination_strata"])
    return ArtifactPolicy(dispositions, strata, snapshot.sha256)


_ASCII_LOWER = {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}
_PRIVATE_USE = re.compile("[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd]")


def _ascii_lower(value: str) -> str:
    return value.translate(_ASCII_LOWER)


def _trim(value: str) -> str:
    return value.strip(WS)


def _collapse(value: str) -> str:
    return WS_RE.sub(" ", value)


def _tag_segments(line: str) -> list[tuple[int, int]]:
    """Each `<` restarts a candidate and each `>` closes the open one, found
    with `str.find` so a line costs C-speed scans rather than a Python loop."""
    segments: list[tuple[int, int]] = []
    start = None
    size = len(line)
    next_open = line.find("<")
    next_close = line.find(">")
    while True:
        if next_open < 0:
            next_open = size
        if next_close < 0:
            next_close = size
        if next_open == next_close == size:
            return segments
        if next_open < next_close:
            start = next_open
            next_open = line.find("<", next_open + 1)
        else:
            if start is not None:
                if HTML_TAG.fullmatch(line, start, next_close + 1):
                    segments.append((start, next_close + 1))
                start = None
            next_close = line.find(">", next_close + 1)


def _private_use(text: str) -> bool:
    return _PRIVATE_USE.search(text) is not None


def detect_artifacts(analysis_text: str, budget: WorkBudget) -> tuple[Observation, ...]:
    lines = analysis_text.split("\n")
    paragraph = 0
    paragraph_of = []
    paragraphs: dict[int, list[int]] = defaultdict(list)
    in_paragraph = False
    for index, line in enumerate(lines):
        budget.charge("lines")
        if _trim(line):
            if not in_paragraph:
                paragraph += 1
                in_paragraph = True
            paragraph_of.append(paragraph)
            paragraphs[paragraph].append(index)
        else:
            in_paragraph = False
            paragraph_of.append(0)
    found: set[tuple[str, int]] = set()

    def emit(name: str, index: int) -> None:
        if paragraph_of[index] and (name, index) not in found:
            budget.charge("observations")
            found.add((name, index))

    css_starts: set[int] = set()
    index = 0
    while index < len(lines):
        line = lines[index]
        budget.charge("lines")
        if preprocessing.CSS_AT_RE.match(line):
            css_starts.add(index)
        if "{" in line:
            depth = 0
            for end in range(index, min(index + 50, len(lines))):
                budget.charge("lines")
                budget.charge("CSS block lines")
                depth += lines[end].count("{") - lines[end].count("}")
                if "}" in lines[end] and depth <= 0:
                    if preprocessing.is_css_rule_block("\n".join(lines[index:end + 1])):
                        css_starts.add(index)
                        index = end
                    break
        index += 1
    for index in css_starts:
        emit("markup_css", index)

    running: dict[str, list[int]] = defaultdict(list)
    fence: tuple[str, int, int] | None = None
    for index, line in enumerate(lines):
        budget.charge("lines")
        if not paragraph_of[index]:
            continue
        # ASCII lowering maps no character into or out of WS, so trimming the
        # lowered line equals lowering the trimmed one.
        trimmed = _trim(line)
        collapsed = _collapse(trimmed)
        ascii_line = _ascii_lower(line)
        lower_trimmed = _trim(ascii_line)
        segments = _tag_segments(line) if "<" in line else []
        if segments or "<!--" in line or "-->" in line:
            emit("markup_html", index)
        if (SCRIPT_OPEN.search(ascii_line) or SCRIPT_CLOSE.search(ascii_line) or
                "javascript:" in ascii_line or any(
                    SCRIPT_EVENT.search(ascii_line[start:end]) for start, end in segments)):
            emit("markup_script", index)
        if _ascii_lower(collapsed) in NAVIGATION:
            emit("navigation_boilerplate", index)
        if MARKDOWN.search(line):
            emit("markdown_apparatus", index)
        if TEI.search(line):
            emit("tei_xml_apparatus", index)
        if FOOTNOTE.search(line):
            emit("footnote_definition", index)
        page = bool(PAGE.fullmatch(lower_trimmed) and
                    (index == 0 or not paragraph_of[index - 1] or
                     index + 1 == len(lines) or not paragraph_of[index + 1]))
        if page:
            emit("page_header", index)
        elif NUMBER.fullmatch(trimmed):
            emit("line_number", index)
        if (index + 1 < len(lines) and paragraph_of[index + 1] and
                OCR_END.search(line) and OCR_START.search(lines[index + 1])):
            emit("ocr_hyphenation", index)
        if 3 <= len(collapsed) <= 80 and ASCII_LETTER.search(collapsed):
            if collapsed not in running:
                budget.charge("running-head keys")
            running[collapsed].append(index)
        if lower_trimmed in TRUNCATION or lower_trimmed.endswith("[truncated]"):
            emit("truncation_marker", index)
        fence_match = FENCE.match(line)
        if fence_match:
            run, suffix = fence_match.groups()
            closer = not suffix.strip(" \t")
            if fence is None:
                fence = (run[0], len(run), index)
            elif closer and run[0] == fence[0] and len(run) >= fence[1]:
                fence = None
        if "\ufffd" in line:
            emit("replacement_character", index)
        if _private_use(line):
            emit("private_use_character", index)
    if fence is not None:
        emit("unbalanced_fence", fence[2])
    for indices in running.values():
        if len({paragraph_of[index] for index in indices}) >= 3:
            for index in indices:
                emit("running_head", index)
    for indices in paragraphs.values():
        count = len(indices)
        if not 3 <= count <= 40:
            continue
        budget.charge("lines", count)
        tokens = [len(WS_RE.split(_trim(lines[index]))) for index in indices]
        short = sum(1 <= value <= 12 for value in tokens)
        open_end = sum(_trim(lines[index])[-1] not in VERSE_PUNCT for index in indices)
        if (short * 5 >= count * 4 and open_end * 5 >= count * 4 and
                sum(tokens) >= 12):
            emit("verse_likely", indices[0])
    return tuple(Observation(name, CATEGORY_OF[name], index + 1, paragraph_of[index])
                 for name, index in sorted(found, key=lambda item: (item[0], item[1])))


def record_outcome(observations: Sequence[Observation],
                   dispositions: Mapping[str, str]) -> str:
    return max((dispositions[item.category] for item in observations),
               key=lambda item: (-1 if item == "none" else DISPOSITIONS.index(item)),
               default="none")


def _stage(outcomes: Sequence[str]) -> tuple[dict[str, str], dict[str, int]]:
    status = "failed" if "refuse" in outcomes else (
        "needs_human_review" if "review" in outcomes else "passed")
    stages = {"intake": "passed", "artifact_census": status}
    reasons = {"ok": sum(item == "passed" for item in stages.values()),
               "artifact_annotate": outcomes.count("annotate"),
               "artifact_review": outcomes.count("review"),
               "artifact_refuse": outcomes.count("refuse")}
    return stages, reasons


def census(manifest: Manifest, policy: ArtifactPolicy) -> CensusResult:
    budget = WorkBudget(CEILINGS)
    records = []
    observations = []
    type_records: dict[str, set[str]] = {name: set() for name in ARTIFACT_TYPES}
    category_records: dict[str, set[str]] = {name: set() for name in CATEGORIES}
    strata = Counter()
    stratum_outcomes: dict[str, Counter] = defaultdict(Counter)
    outcomes = []
    for record in sorted(manifest.records, key=lambda item: item.id):
        found = detect_artifacts(record.analysis_text, budget)
        outcome = record_outcome(found, policy.dispositions)
        outcomes.append(outcome)
        records.append({"id": record.id, "stratum": record.stratum,
                        "analysis_sha256": record.analysis_sha256, "outcome": outcome})
        stratum = coordination_label(record.stratum, policy.coordination_strata)
        strata[stratum] += 1
        stratum_outcomes[stratum][outcome] += 1
        for item in found:
            type_records[item.artifact_type].add(record.id)
            category_records[item.category].add(record.id)
            observations.append({"id": record.id, "artifact_type": item.artifact_type,
                                 "category": item.category, "line": item.line,
                                 "paragraph": item.paragraph,
                                 "disposition": policy.dispositions[item.category]})
    observations.sort(key=lambda item: (item["id"], item["artifact_type"], item["line"]))
    stages, reasons = _stage(outcomes)
    identity = record_set_sha256(manifest.records)
    detail = {"schema": DETAIL_SCHEMA, "tool": TOOL, "tool_version": 1,
              "unicode_version": unicodedata.unidata_version,
              "detector_sha256": DETECTOR_SHA256,
              "inputs": {"manifest_sha256": manifest.manifest_sha256,
                         "record_set_sha256": identity,
                         "policy_sha256": policy.policy_sha256},
              "records": records, "observations": observations,
              "stage_status": stages, "reason_counts": reasons}
    detail_bytes = canonical_json(detail)
    if len(detail_bytes) > DETAIL_LIMIT:
        raise Refusal("size_limit")
    stratum_counts = collapse_strata(dict(strata), policy.coordination_strata)
    stratum_outcome_counts = ({key: {name: stratum_outcomes[key][name]
                                     for name in OUTCOMES}
                               for key in sorted(strata)} if stratum_counts is not None and
                              all(value == 0 or value >= 5
                                  for counts in stratum_outcomes.values()
                                  for value in (counts[name] for name in OUTCOMES))
                              else None)
    receipt = {"schema": RECEIPT_SCHEMA, "tool": TOOL, "tool_version": 1,
               "manifest_sha256": manifest.manifest_sha256,
               "record_set_sha256": identity,
               "policy_sha256": policy.policy_sha256,
               "detector_sha256": DETECTOR_SHA256,
               "unicode_version": unicodedata.unidata_version,
               "detail_sha256": plain_hash(detail_bytes),
               "record_count": len(records), "stage_status": stages,
               "reason_counts": reasons,
               "dispositions": dict(policy.dispositions),
               "artifact_counts": {name: {"observations": sum(
                   item["artifact_type"] == name for item in observations),
                   "records": len(type_records[name])} for name in ARTIFACT_TYPES},
               "category_counts": {name: len(category_records[name])
                                   for name in CATEGORIES},
               "outcome_counts": {name: outcomes.count(name) for name in OUTCOMES},
               "stratum_counts": stratum_counts,
               "stratum_outcome_counts": stratum_outcome_counts}
    if len(canonical_json(receipt)) > RECEIPT_LIMIT:
        raise Refusal("size_limit")
    return CensusResult(detail, receipt)


def parse_artifact_labels(snapshot: Snapshot, policy: ArtifactPolicy,
                          manifest_ids: set[str]) -> tuple[dict[str, dict], str]:
    """Validate label bytes already read under ``LABEL_LIMIT``."""
    code = "labels_contract"
    value = exact_keys(parse_json(snapshot.data, code),
                       {"schema", "register_cells", "labels"}, code)
    if value["schema"] != LABEL_SCHEMA or type(value["register_cells"]) is not list:
        raise Refusal(code)
    cells = value["register_cells"]
    if (not 1 <= len(cells) <= 32 or any(type(item) is not str or
                                         item not in policy.coordination_strata
                                         for item in cells) or len(set(cells)) != len(cells)
            or type(value["labels"]) is not list):
        raise Refusal(code)
    labels = {}
    for item in value["labels"]:
        row = exact_keys(item, {"id", "label", "register_cell", "variation_class"}, code)
        if (type(row["id"]) is not str or type(row["label"]) is not str or
                type(row["register_cell"]) is not str or row["id"] in labels or
                row["register_cell"] not in cells or row["label"] not in
                {"artifact", "legitimate_variation"} or
                (row["label"] == "artifact" and row["variation_class"] is not None) or
                (row["label"] == "legitimate_variation" and
                 row["variation_class"] not in VARIATION_CLASSES)):
            raise Refusal(code)
        labels[row["id"]] = row
    if set(labels) != manifest_ids or {row["register_cell"] for row in labels.values()} != set(cells):
        raise Refusal(code)
    return labels, snapshot.sha256


def _outcome_counts() -> dict[str, int]:
    return {"rows": 0, **{name: 0 for name in CAL_OUTCOMES}}


def calibrate(manifest: Manifest, violations: Mapping[str, str],
              record_set_identity: str, labels: Mapping[str, dict], labels_sha256: str,
              policy: ArtifactPolicy) -> CalibrationResult:
    budget = WorkBudget(CEILINGS)
    records = {record.id: record for record in manifest.records}
    rows = []
    cells = {cell: {"artifact": _outcome_counts(),
                    "legitimate_variation": _outcome_counts()}
             for cell in sorted({row["register_cell"] for row in labels.values()})}
    classes = {name: _outcome_counts() for name in VARIATION_CLASSES}
    missed = hard = review_only = 0
    direction_counts = Counter()
    control_counts = Counter()
    for record_id, label in sorted(labels.items()):
        if record_id in violations:
            outcome = "intake_refusal"
            types = []
        else:
            found = detect_artifacts(records[record_id].analysis_text, budget)
            outcome = record_outcome(found, policy.dispositions)
            types = sorted({item.artifact_type for item in found})
        rows.append({"id": record_id, "outcome": outcome, "artifact_types": types})
        direction = label["label"]
        direction_counts[direction] += 1
        cell = cells[label["register_cell"]][direction]
        cell["rows"] += 1
        cell[outcome] += 1
        if direction == "artifact":
            missed += outcome in ("none", "allow", "annotate")
        else:
            variation = label["variation_class"]
            control_counts[variation] += 1
            class_count = classes[variation]
            class_count["rows"] += 1
            class_count[outcome] += 1
            hard += outcome in ("refuse", "intake_refusal")
            review_only += outcome == "review"
    direction_empty = sum(direction_counts[name] == 0 for name in
                          ("artifact", "legitimate_variation"))
    control_empty = sum(control_counts[name] == 0 for name in CONTROL_CLASSES)
    passed = not (missed or hard or direction_empty or control_empty)
    reasons = {"ok": int(passed), "artifact_missed": missed,
               "legitimate_hard_refusal": hard,
               "legitimate_review_only": review_only,
               "direction_empty": direction_empty,
               "control_class_empty": control_empty}
    detail = {"schema": CAL_DETAIL_SCHEMA,
              "inputs": {"manifest_sha256": manifest.manifest_sha256,
                         "labels_sha256": labels_sha256,
                         "policy_sha256": policy.policy_sha256,
                         "detector_sha256": DETECTOR_SHA256}, "rows": rows}
    detail_bytes = canonical_json(detail)
    if len(detail_bytes) > DETAIL_LIMIT:
        raise Refusal("size_limit")
    receipt = {"schema": CAL_RECEIPT_SCHEMA, "tool": TOOL, "tool_version": 1,
               "manifest_sha256": manifest.manifest_sha256,
               "record_set_sha256": record_set_identity,
               "labels_sha256": labels_sha256,
               "policy_sha256": policy.policy_sha256,
               "detector_sha256": DETECTOR_SHA256,
               "unicode_version": unicodedata.unidata_version,
               "detail_sha256": plain_hash(detail_bytes),
               "row_count": len(labels), "register_cells": sorted(cells),
               "cell_counts": cells, "class_counts": classes,
               "calibration_status": "passed" if passed else "failed",
               "reason_counts": reasons}
    if len(canonical_json(receipt)) > RECEIPT_LIMIT:
        raise Refusal("size_limit")
    return CalibrationResult(detail, receipt)


def _load(path: Path, expected_sha256: str, limit: int, keys: set[str],
          schema: str, code: str) -> dict:
    require_hex(expected_sha256, code)
    try:
        snapshot = read_bounded(path.parent, path.name, limit)
    except Refusal:
        raise Refusal(code) from None
    if snapshot.sha256 != expected_sha256:
        raise Refusal(code)
    value = exact_keys(parse_json(snapshot.data, code), keys, code)
    if value["schema"] != schema or canonical_json(value) != snapshot.data:
        raise Refusal(code)
    return value


def _positive(value: object, code: str, *, zero: bool = True) -> int:
    if type(value) is not int or value < (0 if zero else 1):
        raise Refusal(code)
    return value


def _tool(value: dict, code: str) -> None:
    if value["tool"] != TOOL or type(value["tool_version"]) is not int or value["tool_version"] != 1:
        raise Refusal(code)


def _hashes(value: dict, names: Sequence[str], code: str) -> None:
    for name in names:
        require_hex(value[name], code)


def _stages(value: object, code: str) -> dict:
    stages = exact_keys(value, {"intake", "artifact_census"}, code)
    if (stages["intake"] != "passed" or stages["artifact_census"] not in
            {"passed", "failed", "needs_human_review"}):
        raise Refusal(code)
    return stages


def _census_reasons(value: object, code: str) -> dict:
    reasons = exact_keys(value, {"ok", "artifact_annotate", "artifact_review",
                                 "artifact_refuse"}, code)
    for item in reasons.values():
        _positive(item, code)
    return reasons


def load_artifact_detail(path: Path, expected_sha256: str) -> dict:
    code = "detail_contract"
    value = _load(path, expected_sha256, DETAIL_LIMIT,
                  {"schema", "tool", "tool_version", "unicode_version",
                   "detector_sha256", "inputs", "records", "observations",
                   "stage_status", "reason_counts"}, DETAIL_SCHEMA, code)
    _tool(value, code)
    if value["unicode_version"] != unicodedata.unidata_version:
        raise Refusal(code)
    _hashes(value, ("detector_sha256",), code)
    inputs = exact_keys(value["inputs"], {"manifest_sha256", "record_set_sha256",
                                           "policy_sha256"}, code)
    _hashes(inputs, tuple(inputs), code)
    if type(value["records"]) is not list or type(value["observations"]) is not list:
        raise Refusal(code)
    ids = []
    outcome_by_id = {}
    for item in value["records"]:
        row = exact_keys(item, {"id", "stratum", "analysis_sha256", "outcome"}, code)
        if (type(row["id"]) is not str or not row["id"] or
                type(row["stratum"]) is not str or not row["stratum"] or
                row["outcome"] not in OUTCOMES):
            raise Refusal(code)
        require_hex(row["analysis_sha256"], code)
        ids.append(row["id"])
        outcome_by_id[row["id"]] = row["outcome"]
    if not ids or ids != sorted(set(ids)):
        raise Refusal(code)
    obs_keys = []
    grouped: dict[str, list[str]] = defaultdict(list)
    category_dispositions = {}
    for item in value["observations"]:
        row = exact_keys(item, {"id", "artifact_type", "category", "line",
                                "paragraph", "disposition"}, code)
        if (type(row["id"]) is not str or type(row["artifact_type"]) is not str or
                type(row["category"]) is not str or
                type(row["disposition"]) is not str or
                row["id"] not in outcome_by_id or row["artifact_type"] not in CATEGORY_OF or
                row["category"] != CATEGORY_OF[row["artifact_type"]] or
                row["disposition"] not in DISPOSITIONS):
            raise Refusal(code)
        _positive(row["line"], code, zero=False)
        _positive(row["paragraph"], code, zero=False)
        obs_keys.append((row["id"], row["artifact_type"], row["line"]))
        grouped[row["id"]].append(row["disposition"])
        prior = category_dispositions.setdefault(row["category"], row["disposition"])
        if prior != row["disposition"]:
            raise Refusal(code)
    if obs_keys != sorted(set(obs_keys)):
        raise Refusal(code)
    for record_id, outcome in outcome_by_id.items():
        expected = max(grouped[record_id], key=DISPOSITIONS.index, default="none")
        if outcome != expected:
            raise Refusal(code)
    stages = _stages(value["stage_status"], code)
    reasons = _census_reasons(value["reason_counts"], code)
    expected_stages, expected_reasons = _stage(list(outcome_by_id.values()))
    if stages != expected_stages or reasons != expected_reasons:
        raise Refusal(code)
    return value


def load_artifact_receipt(path: Path, expected_sha256: str) -> dict:
    code = "receipt_contract"
    value = _load(path, expected_sha256, RECEIPT_LIMIT,
                  {"schema", "tool", "tool_version", "manifest_sha256",
                   "record_set_sha256", "policy_sha256", "detector_sha256",
                   "unicode_version", "detail_sha256", "record_count",
                   "stage_status", "reason_counts", "dispositions",
                   "artifact_counts", "category_counts", "outcome_counts",
                   "stratum_counts", "stratum_outcome_counts"}, RECEIPT_SCHEMA, code)
    _tool(value, code)
    if value["unicode_version"] != unicodedata.unidata_version:
        raise Refusal(code)
    _hashes(value, ("manifest_sha256", "record_set_sha256", "policy_sha256",
                    "detector_sha256", "detail_sha256"), code)
    count = _positive(value["record_count"], code, zero=False)
    dispositions = exact_keys(value["dispositions"], set(CATEGORIES), code)
    if (any(item not in DISPOSITIONS for item in dispositions.values()) or
            dispositions["verse"] == "refuse"):
        raise Refusal(code)
    artifact_counts = exact_keys(value["artifact_counts"], set(ARTIFACT_TYPES), code)
    for item in artifact_counts.values():
        obj = exact_keys(item, {"observations", "records"}, code)
        _positive(obj["observations"], code)
        if _positive(obj["records"], code) > count or obj["records"] > obj["observations"]:
            raise Refusal(code)
    category_counts = exact_keys(value["category_counts"], set(CATEGORIES), code)
    for category, item in category_counts.items():
        if _positive(item, code) > count:
            raise Refusal(code)
        record_counts = [artifact_counts[name]["records"] for name in ARTIFACT_TYPES
                         if CATEGORY_OF[name] == category]
        if not max(record_counts) <= item <= sum(record_counts):
            raise Refusal(code)
    outcomes = exact_keys(value["outcome_counts"], set(OUTCOMES), code)
    for item in outcomes.values():
        _positive(item, code)
    if sum(outcomes.values()) != count:
        raise Refusal(code)
    observed = count - outcomes["none"]
    if (observed < max(category_counts.values()) or
            observed > sum(category_counts.values())):
        raise Refusal(code)
    for threshold in DISPOSITIONS:
        at_least = sum(outcomes[name] for name in DISPOSITIONS
                       if DISPOSITIONS.index(name) >= DISPOSITIONS.index(threshold))
        supported = sum(category_counts[category] for category in CATEGORIES
                        if DISPOSITIONS.index(dispositions[category]) >=
                        DISPOSITIONS.index(threshold))
        if at_least > supported:
            raise Refusal(code)
    stages = _stages(value["stage_status"], code)
    reasons = _census_reasons(value["reason_counts"], code)
    expected_stages, expected_reasons = _stage(
        [name for name, number in outcomes.items() for _ in range(number)])
    if stages != expected_stages or reasons != expected_reasons:
        raise Refusal(code)
    strata = value["stratum_counts"]
    cell_outcomes = value["stratum_outcome_counts"]
    if strata is not None:
        if type(strata) is not dict or any(type(key) is not str or
                                           _positive(item, code, zero=False) < 5
                                           for key, item in strata.items()) or sum(strata.values()) != count:
            raise Refusal(code)
    if cell_outcomes is not None:
        if strata is None or type(cell_outcomes) is not dict or set(cell_outcomes) != set(strata):
            raise Refusal(code)
        for key, values in cell_outcomes.items():
            obj = exact_keys(values, set(OUTCOMES), code)
            if (any(_positive(item, code) in (1, 2, 3, 4) for item in obj.values()) or
                    sum(obj.values()) != strata[key]):
                raise Refusal(code)
        if any(sum(cell_outcomes[key][name] for key in cell_outcomes) != outcomes[name]
               for name in OUTCOMES):
            raise Refusal(code)
    return value


def load_calibration_receipt(path: Path, expected_sha256: str) -> dict:
    code = "receipt_contract"
    value = _load(path, expected_sha256, RECEIPT_LIMIT,
                  {"schema", "tool", "tool_version", "manifest_sha256",
                   "record_set_sha256", "labels_sha256", "policy_sha256",
                   "detector_sha256", "unicode_version", "detail_sha256",
                   "row_count", "register_cells", "cell_counts", "class_counts",
                   "calibration_status", "reason_counts"}, CAL_RECEIPT_SCHEMA, code)
    _tool(value, code)
    if value["unicode_version"] != unicodedata.unidata_version:
        raise Refusal(code)
    _hashes(value, ("manifest_sha256", "record_set_sha256", "labels_sha256",
                    "policy_sha256", "detector_sha256", "detail_sha256"), code)
    count = _positive(value["row_count"], code, zero=False)
    cells = value["register_cells"]
    if (type(cells) is not list or not 1 <= len(cells) <= 32 or
            any(type(item) is not str or not item for item in cells) or
            cells != sorted(set(cells))):
        raise Refusal(code)
    cell_counts = exact_keys(value["cell_counts"], set(cells), code)
    for cell in cell_counts.values():
        directions = exact_keys(cell, {"artifact", "legitimate_variation"}, code)
        for obj in directions.values():
            exact_keys(obj, {"rows", *CAL_OUTCOMES}, code)
            for item in obj.values():
                _positive(item, code)
            if obj["rows"] != sum(obj[name] for name in CAL_OUTCOMES):
                raise Refusal(code)
        if sum(obj["rows"] for obj in directions.values()) == 0:
            raise Refusal(code)
    if sum(cell[direction]["rows"] for cell in cell_counts.values()
           for direction in ("artifact", "legitimate_variation")) != count:
        raise Refusal(code)
    class_counts = exact_keys(value["class_counts"], set(VARIATION_CLASSES), code)
    for obj in class_counts.values():
        exact_keys(obj, {"rows", *CAL_OUTCOMES}, code)
        for item in obj.values():
            _positive(item, code)
        if obj["rows"] != sum(obj[name] for name in CAL_OUTCOMES):
            raise Refusal(code)
    if sum(obj["rows"] for obj in class_counts.values()) != sum(
            cell["legitimate_variation"]["rows"] for cell in cell_counts.values()):
        raise Refusal(code)
    reasons = exact_keys(value["reason_counts"],
                         {"ok", "artifact_missed", "legitimate_hard_refusal",
                          "legitimate_review_only", "direction_empty",
                          "control_class_empty"}, code)
    for item in reasons.values():
        _positive(item, code)
    if type(value["calibration_status"]) is not str or value["calibration_status"] not in {"passed", "failed"}:
        raise Refusal(code)
    for name in CAL_OUTCOMES:
        if sum(obj[name] for obj in class_counts.values()) != sum(
                cell["legitimate_variation"][name] for cell in cell_counts.values()):
            raise Refusal(code)
    artifact_rows = sum(cell["artifact"]["rows"] for cell in cell_counts.values())
    legitimate_rows = sum(cell["legitimate_variation"]["rows"] for cell in cell_counts.values())
    missed = sum(cell["artifact"][name] for cell in cell_counts.values()
                 for name in ("none", "allow", "annotate"))
    hard = sum(cell["legitimate_variation"][name] for cell in cell_counts.values()
               for name in ("refuse", "intake_refusal"))
    review_only = sum(cell["legitimate_variation"]["review"]
                      for cell in cell_counts.values())
    direction_empty = int(artifact_rows == 0) + int(legitimate_rows == 0)
    control_empty = sum(class_counts[name]["rows"] == 0 for name in CONTROL_CLASSES)
    passed = not (missed or hard or direction_empty or control_empty)
    expected_reasons = {"ok": int(passed), "artifact_missed": missed,
                        "legitimate_hard_refusal": hard,
                        "legitimate_review_only": review_only,
                        "direction_empty": direction_empty,
                        "control_class_empty": control_empty}
    if (reasons != expected_reasons or
            value["calibration_status"] != ("passed" if passed else "failed")):
        raise Refusal(code)
    return value
