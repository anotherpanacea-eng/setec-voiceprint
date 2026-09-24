"""Offline multiplicity-rule audit for an operator admission map."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .common import (
    Refusal, bind_input, canonical_json, check_candidate_sizes, confine_output_path,
    emit_committed, finish_manifest, plan_manifest, publish_bundle, validate_output_path,
    verify_overlap_detail, POLICY_LIMIT,
)
from .overlap_core import load_overlap_detail
from .multiplicity_core import (
    ADMISSION_LIMIT, build_multiplicity_detail, build_multiplicity_receipt,
    evaluate_multiplicity, parse_admission_map, parse_multiplicity_policy,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def run(manifest_path: Path, policy_path: Path, overlap_detail_path: Path,
        overlap_detail_sha256: str, output_path: Path,
        admission_map_path: Path | None = None) -> tuple[bytes, dict[str, str]]:
    """Run in phases so the first refusal matches slice 1 §4.6's master order.

    The overlap detail is another slice's artifact: its strict reloader
    reports every failure, read failures included, as ``detail_contract``.
    """
    # An empty --admission-map names no file; it refuses admission_contract.
    empty_map = admission_map_path is not None and not admission_map_path.name
    # Confinement: every named control file and the output location.
    manifest_input = bind_input(manifest_path)
    policy_input = bind_input(policy_path)
    admission_input = (bind_input(admission_map_path)
                       if admission_map_path is not None and not empty_map else None)
    confine_output_path(manifest_input.root, output_path)
    # Manifest paths (confinement, then aliasing), then every size ceiling.
    plan = plan_manifest(manifest_input)
    policy_input.check_size(POLICY_LIMIT)
    if admission_input is not None:
        admission_input.check_size(ADMISSION_LIMIT)
    check_candidate_sizes(plan)
    # Contracts in master order: manifest, policy, detail, admission map.
    manifest = finish_manifest(plan)[0]
    policy, policy_sha256 = parse_multiplicity_policy(policy_input.read(POLICY_LIMIT))
    overlap = load_overlap_detail(overlap_detail_path, overlap_detail_sha256)
    verify_overlap_detail(overlap, manifest)
    admission, admission_sha256 = (None, None)
    if admission_map_path is not None:
        admission, admission_sha256 = parse_admission_map(
            admission_input.read(ADMISSION_LIMIT) if admission_input is not None else None,
            manifest, overlap_detail_sha256, policy)
    result = evaluate_multiplicity(overlap, policy, admission)
    detail = build_multiplicity_detail(manifest, policy, policy_sha256,
                                       overlap_detail_sha256, admission_sha256, result)
    receipt = build_multiplicity_receipt(detail)
    detail_bytes, receipt_bytes = canonical_json(detail), canonical_json(receipt)
    dest = validate_output_path(manifest.root, output_path)
    publish_bundle(dest, {"detail.json": detail_bytes, "receipt.json": receipt_bytes})
    return receipt_bytes, detail["stage_status"]


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--overlap-detail", required=True)
    parser.add_argument("--overlap-detail-sha256", required=True)
    parser.add_argument("--admission-map")
    parser.add_argument("--out-bundle", required=True)
    try:
        args = parser.parse_args(argv)
        receipt, statuses = run(Path(args.manifest), Path(args.policy),
                                Path(args.overlap_detail), args.overlap_detail_sha256,
                                Path(args.out_bundle),
                                None if args.admission_map is None else Path(args.admission_map))
    except Refusal as exc:
        sys.stderr.write(exc.code + "\n")
        return 4 if exc.code == "output_unavailable" else 2
    except Exception:
        sys.stderr.write("internal_refusal\n")
        return 2
    emit_committed(receipt, [f"{name} {status}" for name, status in statuses.items()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
