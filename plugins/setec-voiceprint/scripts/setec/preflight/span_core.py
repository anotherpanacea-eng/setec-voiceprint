"""Byte-exact source-span proof and paired-endpoint boundary classification."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .common import (
    Manifest, Record, Refusal, Snapshot, WorkBudget, _bind, canonical_json, domain_hash,
    exact_keys, parse_json, plain_hash, read_bounded, record_set_sha256, require_hex,
)

TOOL = "setec.preflight.span"
POLICY_SCHEMA = "setec-preflight-span-policy/1"
DETAIL_SCHEMA = "setec-preflight-span-detail/1"
RECEIPT_SCHEMA = "setec-preflight-span-receipt/1"
BOUNDARY_CLASSES = ("whole_document", "blank_line_paragraph", "physical_line",
                    "sentence_terminal", "none")
CLASSES = BOUNDARY_CLASSES
PROOF_RESULTS = ("proved", "span_source_hash_mismatch", "span_source_encoding",
                 "span_range", "span_code_point", "span_slice_mismatch")
DISPOSITIONS = ("allowed", "disallowed", "unproved")
REASONS = ("ok", *PROOF_RESULTS[1:], "span_class_disallowed", "span_unclassified")
STAGES = ("intake", "span_proof", "span_boundary")
POLICY_LIMIT = 4 * 1024
SOURCE_LIMIT = 8 * 1024 * 1024
COMBINED_SOURCE_LIMIT = 256 * 1024 * 1024
BOUNDARY_VISIT_LIMIT = 10_000_000
DETAIL_LIMIT = 64 * 1024 * 1024
RECEIPT_LIMIT = 64 * 1024
W = frozenset(b"\t\n\r ")
H = frozenset(b"\t ")
T = frozenset(b"!.?")
ASCII_CLOSERS = frozenset(b"\"')]")
UTF8_CLOSERS = ("\u2019".encode(), "\u201d".encode())


@dataclass(frozen=True)
class SpanPolicy:
    policy_sha256: str
    allowed_classes: tuple[str, ...]


@dataclass(frozen=True)
class SpanResult:
    detail: dict
    receipt: dict
    span_evidence: str


@dataclass(frozen=True)
class SpanSources:
    """Distinct source paths bound and size-checked, not yet read."""

    reused: dict[str, Snapshot]
    limits: dict[str, int]


def read_span_policy(path: Path) -> Snapshot:
    return read_bounded(path.parent, path.name, POLICY_LIMIT)


def load_span_policy(path: Path) -> SpanPolicy:
    return parse_span_policy(read_span_policy(path))


def parse_span_policy(snapshot: Snapshot) -> SpanPolicy:
    policy = exact_keys(parse_json(snapshot.data, "policy_contract"),
                        {"schema", "allowed_classes"}, "policy_contract")
    if policy["schema"] != POLICY_SCHEMA or type(policy["allowed_classes"]) is not list:
        raise Refusal("policy_contract")
    allowed = policy["allowed_classes"]
    if not allowed or any(type(item) is not str or item not in CLASSES for item in allowed):
        raise Refusal("policy_contract")
    if allowed != [item for item in CLASSES if item in allowed]:
        raise Refusal("policy_contract")
    return SpanPolicy(snapshot.sha256, tuple(allowed))


class BoundaryReader:
    def __init__(self, source: bytes, budget: WorkBudget) -> None:
        self.source = source
        self.budget = budget
        self.n = len(source)
        d0 = 3 if source.startswith(b"\xef\xbb\xbf") else 0
        self.lead = d0
        while self.lead < self.n and source[self.lead] in W:
            self.lead += 1
        self.trail = self.n
        while self.trail > d0 and source[self.trail - 1] in W:
            self.trail -= 1

    def at(self, index: int) -> int:
        if not 0 <= index < self.n:
            raise ValueError("classifier read outside source")
        self.budget.charge("boundary byte visits")
        return self.source[index]

    def nl_before(self, pos: int) -> tuple[int, int] | None:
        if pos < 1:
            return None
        previous = self.at(pos - 1)
        if previous == 10:
            if pos >= 2 and self.at(pos - 2) == 13:
                return pos - 2, 2
            return pos - 1, 1
        if previous == 13 and (pos == self.n or self.at(pos) != 10):
            return pos - 1, 1
        return None

    def nl_after(self, pos: int) -> tuple[int, int] | None:
        if pos >= self.n:
            return None
        value = self.at(pos)
        if value == 13:
            if pos + 1 < self.n and self.at(pos + 1) == 10:
                return pos + 2, 2
            return pos + 1, 1
        if value == 10 and (pos == 0 or self.at(pos - 1) != 13):
            return pos + 1, 1
        return None

    def line_before(self, pos: int) -> tuple[int, int] | None:
        while pos > 0 and self.at(pos - 1) in H:
            pos -= 1
        return self.nl_before(pos)

    def line_after(self, pos: int) -> tuple[int, int] | None:
        while pos < self.n and self.at(pos) in H:
            pos += 1
        return self.nl_after(pos)

    def para_before(self, pos: int) -> bool:
        first = self.line_before(pos)
        if first is None:
            return False
        q = first[0]
        while q > 0 and self.at(q - 1) in H:
            q -= 1
        return self.nl_before(q) is not None

    def para_after(self, pos: int) -> bool:
        first = self.line_after(pos)
        if first is None:
            return False
        q = first[0]
        while q < self.n and self.at(q) in H:
            q += 1
        return self.nl_after(q) is not None

    def _before_closers(self, pos: int) -> int:
        while pos > 0:
            if self.at(pos - 1) in ASCII_CLOSERS:
                pos -= 1
                continue
            matched = False
            if pos >= 3:
                for closer in UTF8_CLOSERS:
                    if all(self.at(pos - 3 + i) == closer[i] for i in range(3)):
                        pos -= 3
                        matched = True
                        break
            if not matched:
                break
        return pos

    def sent_before(self, pos: int) -> bool:
        original = pos
        while pos > 0 and self.at(pos - 1) in W:
            pos -= 1
        if pos == original:
            return False
        pos = self._before_closers(pos)
        return pos > 0 and self.at(pos - 1) in T

    def sent_after(self, pos: int) -> bool:
        if pos < self.n and self.at(pos) not in W:
            return False
        pos = self._before_closers(pos)
        return pos > 0 and self.at(pos - 1) in T

    def classes(self, start: int, end: int) -> list[str]:
        doc_start = start <= self.lead
        doc_end = end >= self.trail
        paragraph_start = doc_start or self.para_before(start)
        paragraph_end = doc_end or self.para_after(end)
        line_start = doc_start or self.line_before(start) is not None
        line_end = doc_end or self.line_after(end) is not None
        matched = []
        if doc_start and doc_end:
            matched.append("whole_document")
        if paragraph_start and paragraph_end:
            matched.append("blank_line_paragraph")
        if line_start and line_end:
            matched.append("physical_line")
        if (paragraph_start or self.sent_before(start)) and self.sent_after(end):
            matched.append("sentence_terminal")
        matched.append("none")
        return matched


def classify_span(source: bytes, start: int, end: int,
                  budget: WorkBudget) -> tuple[str, ...]:
    if (type(start) is not int or type(end) is not int or
            not 0 <= start < end <= len(source)):
        raise ValueError("invalid span")
    return tuple(BoundaryReader(source, budget).classes(start, end))


def _proof(record: Record, source: bytes, observed_hash: str,
           encoded: bool, classifier: BoundaryReader | None,
           allowed: set[str]) -> dict:
    start, end = record.start_byte, record.end_byte
    if observed_hash != record.source_bytes_sha256:
        result = "span_source_hash_mismatch"
    elif not encoded:
        result = "span_source_encoding"
    elif end > len(source):
        result = "span_range"
    elif ((start < len(source) and 0x80 <= source[start] <= 0xBF) or
          (end < len(source) and 0x80 <= source[end] <= 0xBF)):
        result = "span_code_point"
    elif source[start:end] != record.candidate.data:
        result = "span_slice_mismatch"
    else:
        result = "proved"
    matched = classifier.classes(start, end) if result == "proved" and classifier else []
    disposition = ("unproved" if result != "proved" else
                   "allowed" if any(item in allowed for item in matched) else "disallowed")
    return {
        "id": record.id, "content_sha256": record.content_sha256,
        "source_bytes_sha256": record.source_bytes_sha256,
        "observed_source_sha256": observed_hash, "source_size": len(source),
        "start_byte": start, "end_byte": end, "self_span": record.self_span,
        "proof_result": result, "boundary_class": matched[0] if matched else None,
        "matched_classes": matched, "disposition": disposition,
    }


def bind_span_sources(manifest: Manifest) -> SpanSources:
    """Run every source-file refusal that needs no source bytes.

    Binding every source before the policy is parsed lets `input_changed`,
    `path_confinement`, and `size_limit` outrank `policy_contract` (slice 1
    §4.6) without holding more than one source in memory.
    """
    candidates = {record.path: record.candidate for record in manifest.records}
    names = sorted({record.source_path for record in manifest.records})
    reused = {name: candidates[name] for name in names if name in candidates}
    bound = {name: _bind(manifest.root, name)[1] for name in names if name not in reused}
    if any((fingerprint[0], fingerprint[1]) != manifest.path_identities[name]
           for name, fingerprint in bound.items()):
        raise Refusal("input_changed")
    limits: dict[str, int] = {}
    total = 0
    for name, fingerprint in sorted(bound.items()):
        limits[name] = min(SOURCE_LIMIT, COMBINED_SOURCE_LIMIT - total)
        if fingerprint[2] > limits[name]:
            raise Refusal("size_limit")
        total += fingerprint[2]
    return SpanSources(reused, limits)


def prove_spans(manifest: Manifest, policy: SpanPolicy,
                sources: SpanSources | None = None) -> SpanResult:
    if sources is None:
        sources = bind_span_sources(manifest)
    records_by_source: dict[str, list[Record]] = defaultdict(list)
    for record in manifest.records:
        records_by_source[record.source_path].append(record)
    budget = WorkBudget({"boundary byte visits": BOUNDARY_VISIT_LIMIT})
    allowed = set(policy.allowed_classes)
    findings = []
    for source_name in sorted(records_by_source):
        if source_name in sources.reused:
            snapshot = sources.reused[source_name]
        else:
            snapshot = read_bounded(manifest.root, source_name, sources.limits[source_name])
        if snapshot.identity != manifest.path_identities[source_name]:
            raise Refusal("input_changed")
        try:
            snapshot.data.decode("utf-8", errors="strict")
            encoded = True
        except UnicodeError:
            encoded = False
        classifier = BoundaryReader(snapshot.data, budget) if encoded else None
        for record in records_by_source[source_name]:
            findings.append(_proof(record, snapshot.data, snapshot.sha256,
                                   encoded, classifier, allowed))
    findings.sort(key=lambda row: row["id"])
    proofs = Counter(row["proof_result"] for row in findings)
    dispositions = Counter(row["disposition"] for row in findings)
    statuses = {
        "intake": "passed",
        "span_proof": "passed" if proofs["proved"] == len(findings) else "failed",
        "span_boundary": ("failed" if dispositions["disallowed"] else
                          "needs_human_review" if dispositions["unproved"] else "passed"),
    }
    reasons = {name: 0 for name in REASONS}
    reasons.update({name: proofs[name] for name in PROOF_RESULTS[1:]})
    reasons.update({"ok": sum(status == "passed" for status in statuses.values()),
                    "span_class_disallowed": dispositions["disallowed"],
                    "span_unclassified": dispositions["unproved"]})
    detail = {
        "schema": DETAIL_SCHEMA, "tool": TOOL, "tool_version": 1,
        "inputs": {"manifest_sha256": manifest.manifest_sha256,
                   "policy_sha256": policy.policy_sha256,
                   "record_set_sha256": record_set_sha256(manifest.records)},
        "records": findings, "stage_status": statuses, "reason_counts": reasons,
    }
    detail_bytes = canonical_json(detail)
    if len(detail_bytes) > DETAIL_LIMIT:
        raise Refusal("size_limit")
    classes = {name: {"allowed": 0, "disallowed": 0} for name in CLASSES}
    matched_classes = {name: 0 for name in CLASSES}
    for row in findings:
        if row["proof_result"] == "proved":
            classes[row["boundary_class"]][row["disposition"]] += 1
            for name in row["matched_classes"]:
                matched_classes[name] += 1
    receipt = {
        "schema": RECEIPT_SCHEMA, "tool": TOOL, "tool_version": 1,
        "manifest_sha256": manifest.manifest_sha256,
        "policy_sha256": policy.policy_sha256,
        "detail_sha256": plain_hash(detail_bytes),
        "record_set_sha256": record_set_sha256(manifest.records),
        "record_count": len(findings), "source_count": len(records_by_source),
        "stage_status": statuses, "reason_counts": reasons,
        "disposition_counts": {name: dispositions[name] for name in DISPOSITIONS},
        "class_counts": classes, "matched_class_counts": matched_classes,
        "span_evidence": "proved" if statuses["span_proof"] == "passed" else "not_proved",
    }
    receipt_bytes = canonical_json(receipt)
    if len(receipt_bytes) > RECEIPT_LIMIT:
        raise Refusal("size_limit")
    return SpanResult(detail, receipt, receipt["span_evidence"])


def build_span(manifest: Manifest, policy: SpanPolicy,
               sources: SpanSources | None = None) -> tuple[bytes, bytes, dict]:
    result = prove_spans(manifest, policy, sources)
    return canonical_json(result.detail), canonical_json(result.receipt), result.detail["stage_status"]


def _load(path: Path, expected_sha256: str, limit: int, code: str,
          schema: str, keys: set[str]) -> dict:
    require_hex(expected_sha256, code)
    try:
        snapshot = read_bounded(path.parent, path.name, limit)
    except Refusal:
        raise Refusal(code) from None
    if snapshot.sha256 != expected_sha256:
        raise Refusal(code)
    value = exact_keys(parse_json(snapshot.data, code), keys, code)
    if (value["schema"] != schema or value["tool"] != TOOL or
            type(value["tool_version"]) is not int or value["tool_version"] != 1 or
            canonical_json(value) != snapshot.data):
        raise Refusal(code)
    return value


def _counts(value: object, keys: Sequence[str], code: str) -> dict:
    result = exact_keys(value, set(keys), code)
    if any(type(number) is not int or number < 0 for number in result.values()):
        raise Refusal(code)
    return result


def load_span_detail(path: Path, expected_sha256: str) -> dict:
    code = "detail_contract"
    value = _load(path, expected_sha256, DETAIL_LIMIT, code, DETAIL_SCHEMA,
                  {"schema", "tool", "tool_version", "inputs", "records",
                   "stage_status", "reason_counts"})
    inputs = exact_keys(value["inputs"],
                        {"manifest_sha256", "policy_sha256", "record_set_sha256"}, code)
    for item in inputs.values():
        require_hex(item, code)
    rows = value["records"]
    if type(rows) is not list or not 1 <= len(rows) <= 5000:
        raise Refusal(code)
    ids = []
    proof_counts = Counter()
    disposition_counts = Counter()
    for row in rows:
        item = exact_keys(row, {"id", "content_sha256", "source_bytes_sha256",
                                "observed_source_sha256", "source_size", "start_byte",
                                "end_byte", "self_span", "proof_result", "boundary_class",
                                "matched_classes", "disposition"}, code)
        if type(item["id"]) is not str or not item["id"]:
            raise Refusal(code)
        ids.append(item["id"])
        for name in ("content_sha256", "source_bytes_sha256", "observed_source_sha256"):
            require_hex(item[name], code)
        if (type(item["source_size"]) is not int or item["source_size"] < 0 or
                type(item["start_byte"]) is not int or item["start_byte"] < 0 or
                type(item["end_byte"]) is not int or item["end_byte"] <= item["start_byte"] or
                type(item["self_span"]) is not bool or
                type(item["proof_result"]) is not str or item["proof_result"] not in PROOF_RESULTS or
                type(item["disposition"]) is not str or item["disposition"] not in DISPOSITIONS):
            raise Refusal(code)
        matched = item["matched_classes"]
        if type(matched) is not list or any(type(name) is not str for name in matched):
            raise Refusal(code)
        result = item["proof_result"]
        # §6.2 runs its checks in order, so each code fixes what the earlier
        # checks saw; a self-span reuses the strict-UTF-8 candidate and proves.
        hash_matches = item["observed_source_sha256"] == item["source_bytes_sha256"]
        in_range = item["end_byte"] <= item["source_size"]
        if ((result == "span_source_hash_mismatch") == hash_matches or
                (result == "span_range" and in_range) or
                (result in ("span_code_point", "span_slice_mismatch", "proved") and
                 not in_range) or
                (item["self_span"] and (result != "proved" or item["start_byte"] != 0 or
                                        item["end_byte"] != item["source_size"]))):
            raise Refusal(code)
        if result == "proved":
            if (not matched or matched[-1] != "none" or
                    matched != [name for name in CLASSES if name in matched] or
                    ("whole_document" in matched and "blank_line_paragraph" not in matched) or
                    ("blank_line_paragraph" in matched and "physical_line" not in matched) or
                    item["boundary_class"] != matched[0] or
                    item["disposition"] not in ("allowed", "disallowed")):
                raise Refusal(code)
        elif (item["boundary_class"] is not None or matched or
              item["disposition"] != "unproved"):
            raise Refusal(code)
        proof_counts[item["proof_result"]] += 1
        disposition_counts[item["disposition"]] += 1
    if ids != sorted(set(ids)):
        raise Refusal(code)
    # Rows that observed the same source bytes saw one size and one decode
    # result, and one policy decided every disposition.
    sizes: dict[str, set[int]] = defaultdict(set)
    encodings: dict[str, set[bool]] = defaultdict(set)
    for row in rows:
        sizes[row["observed_source_sha256"]].add(row["source_size"])
        if row["proof_result"] != "span_source_hash_mismatch":
            encodings[row["observed_source_sha256"]].add(
                row["proof_result"] != "span_source_encoding")
    disallowed_classes = {name for row in rows if row["disposition"] == "disallowed"
                          for name in row["matched_classes"]}
    if (any(len(item) != 1 for item in sizes.values()) or
            any(len(item) != 1 for item in encodings.values()) or
            any(row["disposition"] == "allowed" and
                set(row["matched_classes"]) <= disallowed_classes for row in rows)):
        raise Refusal(code)
    expected_record_set = domain_hash(
        "setec-preflight-record-set-v1",
        canonical_json([[row["id"], row["content_sha256"]] for row in rows]))
    if inputs["record_set_sha256"] != expected_record_set:
        raise Refusal(code)
    statuses = exact_keys(value["stage_status"], set(STAGES), code)
    expected_statuses = {
        "intake": "passed",
        "span_proof": "passed" if proof_counts["proved"] == len(rows) else "failed",
        "span_boundary": ("failed" if disposition_counts["disallowed"] else
                          "needs_human_review" if disposition_counts["unproved"] else "passed"),
    }
    if statuses != expected_statuses:
        raise Refusal(code)
    reasons = _counts(value["reason_counts"], REASONS, code)
    expected_reasons = {"ok": sum(name == "passed" for name in statuses.values()),
                        **{name: proof_counts[name] for name in PROOF_RESULTS[1:]},
                        "span_class_disallowed": disposition_counts["disallowed"],
                        "span_unclassified": disposition_counts["unproved"]}
    if reasons != expected_reasons:
        raise Refusal(code)
    return value


def load_span_receipt(path: Path, expected_sha256: str) -> dict:
    code = "receipt_contract"
    value = _load(path, expected_sha256, RECEIPT_LIMIT, code, RECEIPT_SCHEMA,
                  {"schema", "tool", "tool_version", "manifest_sha256", "policy_sha256",
                   "detail_sha256", "record_set_sha256", "record_count", "source_count",
                   "stage_status", "reason_counts", "disposition_counts", "class_counts",
                   "matched_class_counts", "span_evidence"})
    for name in ("manifest_sha256", "policy_sha256", "detail_sha256", "record_set_sha256"):
        require_hex(value[name], code)
    if (type(value["record_count"]) is not int or not 1 <= value["record_count"] <= 5000 or
            type(value["source_count"]) is not int or not 1 <= value["source_count"] <= value["record_count"]):
        raise Refusal(code)
    statuses = exact_keys(value["stage_status"], set(STAGES), code)
    if any(type(item) is not str or item not in
           {"passed", "failed", "needs_human_review", "not_run"} for item in statuses.values()):
        raise Refusal(code)
    _counts(value["reason_counts"], REASONS, code)
    dispositions = _counts(value["disposition_counts"], DISPOSITIONS, code)
    if sum(dispositions.values()) != value["record_count"]:
        raise Refusal(code)
    classes = exact_keys(value["class_counts"], set(CLASSES), code)
    for item in classes.values():
        _counts(item, ("allowed", "disallowed"), code)
    matched = _counts(value["matched_class_counts"], CLASSES, code)
    proved = dispositions["allowed"] + dispositions["disallowed"]
    strongest = {name: classes[name]["allowed"] + classes[name]["disallowed"] for name in CLASSES}
    # Classes 1 to 3 nest and `boundary_class` is the first match (§6.3), so a
    # row matching a structural class has that class or a stronger one.
    if (matched["none"] != proved or
            any(number > proved for number in matched.values()) or
            matched["whole_document"] != strongest["whole_document"] or
            matched["blank_line_paragraph"] != (strongest["whole_document"] +
                                                strongest["blank_line_paragraph"]) or
            matched["physical_line"] != (matched["blank_line_paragraph"] +
                                         strongest["physical_line"]) or
            not strongest["sentence_terminal"] <= matched["sentence_terminal"] <=
            strongest["sentence_terminal"] + matched["physical_line"] or
            (classes["none"]["allowed"] and dispositions["disallowed"]) or
            sum(item["allowed"] for item in classes.values()) != dispositions["allowed"] or
            sum(item["disallowed"] for item in classes.values()) != dispositions["disallowed"] or
            statuses["intake"] != "passed" or
            statuses["span_proof"] != ("passed" if dispositions["unproved"] == 0 else "failed") or
            statuses["span_boundary"] != (
                "failed" if dispositions["disallowed"] else
                "needs_human_review" if dispositions["unproved"] else "passed") or
            value["reason_counts"]["ok"] != sum(
                status == "passed" for status in statuses.values()) or
            value["reason_counts"]["span_class_disallowed"] != dispositions["disallowed"] or
            value["reason_counts"]["span_unclassified"] != dispositions["unproved"] or
            sum(value["reason_counts"][name] for name in PROOF_RESULTS[1:]) !=
            dispositions["unproved"]):
        raise Refusal(code)
    if value["span_evidence"] != ("proved" if statuses["span_proof"] == "passed" else "not_proved"):
        raise Refusal(code)
    return value
