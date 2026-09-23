"""Compose bound P7 evidence without any v1 eligibility clearance."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .artifacts_core import load_artifact_receipt, load_calibration_receipt
from .common import (
    Refusal, canonical_json, exact_keys, load_manifest, parse_json, publish_bundle,
    read_bounded, record_set_sha256, require_hex, validate_output_path,
)
from .final_core import load_final_receipt
from .holdout_core import load_holdout_receipt
from .multiplicity_core import PURPOSES, load_multiplicity_receipt
from .overlap_core import load_overlap_receipt
from .p7_report_core import SCHEMA, TOOL, compose_rows, decide
from .span_core import load_span_receipt

RELOADERS = {"final": load_final_receipt, "intake": load_overlap_receipt,
             "span": load_span_receipt, "multiplicity": load_multiplicity_receipt,
             "holdout": load_holdout_receipt, "artifact": load_artifact_receipt,
             "calibration": load_calibration_receipt}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def _receipt(path: Path, kind: str) -> tuple[dict, str]:
    try:
        snapshot = read_bounded(path.parent, path.name, 1024 * 1024)
    except Refusal:
        raise Refusal("receipt_contract") from None
    return RELOADERS[kind](path, snapshot.sha256), snapshot.sha256


def _register(path: Path) -> list[str]:
    try:
        snapshot = read_bounded(path.parent, path.name, 1024 * 1024)
    except Refusal:
        raise Refusal("receipt_contract") from None
    value = exact_keys(parse_json(snapshot.data, "receipt_contract"),
                       {"schema", "manifest_sha256s"}, "receipt_contract")
    hashes = value["manifest_sha256s"]
    if (value["schema"] != "setec-preflight-sealed-register/1" or
            type(hashes) is not list or not 1 <= len(hashes) <= 8):
        raise Refusal("receipt_contract")
    for item in hashes:
        require_hex(item, "receipt_contract")
    if hashes != sorted(set(hashes)) or canonical_json(value) != snapshot.data:
        raise Refusal("receipt_contract")
    return hashes


def run(manifest_path: Path, purpose: str, receipt_paths: dict[str, Path | None],
        sealed_register: Path | None, out_bundle: Path) -> tuple[bytes, tuple]:
    # Phases follow slice 1 section 4.6's order, first match wins: the manifest
    # (input_contract and above), then purpose (policy_contract), then every
    # receipt and register contract (receipt_contract), then binding
    # (receipt_binding), then output.
    manifest = load_manifest(manifest_path)
    if purpose not in PURPOSES:
        raise Refusal("policy_contract")
    if (receipt_paths.get("final") is None or receipt_paths.get("intake") is None or
            (receipt_paths.get("holdout") is None) != (sealed_register is None) or
            (receipt_paths.get("calibration") is not None and
             receipt_paths.get("artifact") is None)):
        raise Refusal("receipt_contract")
    receipts = {name: (_receipt(path, name) if path is not None else None)
                for name, path in receipt_paths.items()}
    register = _register(sealed_register) if sealed_register is not None else None
    final_receipt, final_sha = receipts["final"]
    intake_receipt, intake_sha = receipts["intake"]
    record_set = record_set_sha256(manifest.records)
    if (final_receipt["manifest_sha256"] != manifest.manifest_sha256 or
            final_receipt["record_set_sha256"] != record_set or
            intake_sha != final_receipt["intake_receipt_sha256"] or
            intake_receipt["manifest_sha256"] != final_receipt["intake_manifest_sha256"] or
            intake_receipt["detail_sha256"] != final_receipt["intake_detail_sha256"]):
        raise Refusal("receipt_binding")
    for name in ("span", "multiplicity", "holdout", "artifact"):
        item = receipts.get(name)
        if item is None:
            continue
        receipt = item[0]
        manifest_field = ("candidate_manifest_sha256" if name == "holdout"
                          else "manifest_sha256")
        if (receipt[manifest_field] != manifest.manifest_sha256 or
                receipt["record_set_sha256"] != record_set):
            raise Refusal("receipt_binding")
    multiplicity = receipts.get("multiplicity")
    if multiplicity is not None and (
            multiplicity[0]["purpose"] != purpose or
            multiplicity[0]["overlap_detail_sha256"] !=
            final_receipt["overlap_detail_sha256"]):
        raise Refusal("receipt_binding")
    holdout = receipts.get("holdout")
    if holdout is not None and (
            {item["manifest_sha256"] for item in holdout[0]["sealed"]} != set(register)):
        raise Refusal("receipt_binding")
    calibration = receipts.get("calibration")
    artifact = receipts.get("artifact")
    if calibration is not None and artifact is not None and any(
            calibration[0][name] != artifact[0][name]
            for name in ("detector_sha256", "policy_sha256", "unicode_version")):
        raise Refusal("receipt_binding")
    rows = compose_rows(purpose, receipts)
    decision = decide(purpose, rows)
    span = receipts.get("span")
    report = {"schema": SCHEMA, "tool": TOOL, "tool_version": 1,
              "manifest_sha256": manifest.manifest_sha256, "purpose": purpose,
              "final_receipt_sha256": final_sha,
              "rows": [row.as_dict() for row in rows],
              "span_evidence": ("proved" if span is not None and
                                span[0]["span_evidence"] == "proved" else "declared"),
              "decision": decision}
    data = canonical_json(report)
    dest = validate_output_path(manifest.root, out_bundle)
    publish_bundle(dest, {"receipt.json": data})
    return data, rows


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--final-receipt", required=True)
    parser.add_argument("--intake-overlap-receipt", required=True)
    for name in ("span", "multiplicity", "holdout", "artifact", "calibration"):
        parser.add_argument("--" + name + "-receipt")
    parser.add_argument("--sealed-register")
    parser.add_argument("--out-bundle", required=True)
    try:
        args = parser.parse_args(argv)
        paths = {"final": Path(args.final_receipt),
                 "intake": Path(args.intake_overlap_receipt),
                 **{name: Path(getattr(args, name + "_receipt"))
                    if getattr(args, name + "_receipt") else None
                    for name in ("span", "multiplicity", "holdout", "artifact", "calibration")}}
        receipt, rows = run(Path(args.manifest), args.purpose, paths,
                            Path(args.sealed_register) if args.sealed_register else None,
                            Path(args.out_bundle))
        sys.stdout.buffer.write(receipt)
        for row in rows:
            sys.stderr.write(f"{row.obligation} {row.status}\n")
        return 3 if any(row.status in {"not_run", "unavailable"} for row in rows) else 0
    except Refusal as exc:
        sys.stderr.write(exc.code + "\n")
        return 4 if exc.code == "output_unavailable" else 2
    except Exception:
        sys.stderr.write("internal_refusal\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
