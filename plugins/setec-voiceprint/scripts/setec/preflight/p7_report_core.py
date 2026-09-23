"""P7 obligation composition with permanently unavailable v1 stages."""

from __future__ import annotations

from dataclasses import dataclass

from .multiplicity_core import PURPOSES

TOOL = "setec.preflight.p7_report"
SCHEMA = "setec-preflight-p7-report/1"
P7_OBLIGATIONS = (
    "encoding_normalization", "markup_verse_apparatus", "detector_calibration",
    "span_boundary", "exact_dedup", "fuzzy_dedup", "semantic_dedup",
    "holdout_decontamination", "perplexity_two_tail", "clusters_at_intake",
    "cluster_whole_split", "multiplicity", "final_packet_recheck",
)
SEVERITY = {"failed": 0, "not_run": 1, "needs_human_review": 2, "passed": 3}


@dataclass(frozen=True)
class Row:
    obligation: str
    status: str
    source: str | None
    receipt_sha256: str | None
    manifest_sha256: str | None

    def as_dict(self) -> dict:
        return {"obligation": self.obligation, "status": self.status,
                "source": self.source, "receipt_sha256": self.receipt_sha256,
                "manifest_sha256": self.manifest_sha256}


def _row(name: str, status: str, item: tuple[dict, str] | None,
         manifest_sha256: str | None = None) -> Row:
    if item is None:
        return Row(name, status, None, None, manifest_sha256)
    receipt, digest = item
    return Row(name, status, receipt["schema"], digest, manifest_sha256)


def _worst(*statuses: str) -> str:
    return min(statuses, key=lambda item: SEVERITY[item])


def _category_status(receipt: dict, categories: tuple[str, ...]) -> str:
    statuses = []
    for category in categories:
        if receipt["category_counts"][category] == 0:
            continue
        disposition = receipt["dispositions"][category]
        statuses.append({"allow": "passed", "annotate": "passed",
                         "review": "needs_human_review", "refuse": "failed"}[disposition])
    return _worst(*statuses) if statuses else "passed"


def compose_rows(purpose: str,
                 receipts: dict[str, tuple[dict, str] | None]) -> tuple[Row, ...]:
    if purpose not in PURPOSES:
        raise ValueError("unknown purpose")
    final = receipts["final"]
    intake = receipts["intake"]
    assert final is not None and intake is not None
    final_manifest = final[0]["manifest_sha256"]
    intake_manifest = intake[0]["manifest_sha256"]
    artifact = receipts.get("artifact")
    calibration = receipts.get("calibration")
    span = receipts.get("span")
    holdout = receipts.get("holdout")
    multiplicity = receipts.get("multiplicity")
    rows = []
    for name in P7_OBLIGATIONS:
        if name == "encoding_normalization":
            status = (_worst(final[0]["stage_status"]["intake"],
                             _category_status(artifact[0], ("encoding_normalization",)))
                      if artifact else "not_run")
            rows.append(_row(name, status, artifact, final_manifest if artifact else None))
        elif name == "markup_verse_apparatus":
            status = (_worst(final[0]["stage_status"]["intake"],
                             _category_status(artifact[0], ("markup", "verse", "apparatus")))
                      if artifact else "not_run")
            rows.append(_row(name, status, artifact, final_manifest if artifact else None))
        elif name == "detector_calibration":
            status = ("failed" if calibration[0]["calibration_status"] == "failed"
                      else "not_run") if calibration else "not_run"
            rows.append(_row(name, status, calibration, None))
        elif name == "span_boundary":
            status = _worst(span[0]["stage_status"]["span_proof"],
                            span[0]["stage_status"]["span_boundary"]) if span else "not_run"
            rows.append(_row(name, status, span, final_manifest if span else None))
        elif name == "exact_dedup":
            rows.append(_row(name, final[0]["stage_status"]["exact_overlap"],
                             final, final_manifest))
        elif name == "fuzzy_dedup":
            rows.append(_row(name, final[0]["stage_status"]["fuzzy_overlap"],
                             final, final_manifest))
        elif name in {"semantic_dedup", "perplexity_two_tail"}:
            rows.append(_row(name, "unavailable", None))
        elif name == "holdout_decontamination":
            if holdout is None or holdout[0]["release"] == "withheld":
                rows.append(_row(name, "not_run", None))
            else:
                status = _worst(*(holdout[0]["stage_status"][stage]
                                  for stage in ("exact", "ngram", "span")))
                rows.append(_row(name, status, holdout, final_manifest))
        elif name == "clusters_at_intake":
            rows.append(_row(name, intake[0]["stage_status"]["split_integrity"],
                             intake, intake_manifest))
        elif name == "cluster_whole_split":
            rows.append(_row(name, final[0]["stage_status"]["split_integrity"],
                             final, final_manifest))
        elif name == "multiplicity":
            status = multiplicity[0]["stage_status"]["multiplicity"] if multiplicity else "not_run"
            rows.append(_row(name, status, multiplicity,
                             final_manifest if multiplicity else None))
        else:
            rows.append(_row(name, final[0]["stage_status"]["projection"],
                             final, final_manifest))
    return tuple(rows)


def decide(purpose: str, rows: tuple[Row, ...]) -> str:
    if purpose in {"set_level_diversity", "evaluation_fixture"}:
        return "not_eligible_by_policy"
    if any(row.status == "failed" for row in rows):
        return "ineligible"
    if any(row.status in {"not_run", "unavailable"} for row in rows):
        return "incomplete_required_stage"
    if any(row.status == "needs_human_review" for row in rows):
        return "needs_human_review"
    return "eligible"
