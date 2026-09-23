"""Offline multiplicity-rule audit for an operator admission map."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .common import (
    Refusal, canonical_json, load_manifest, publish_bundle, validate_output_path,
    verify_overlap_detail,
)
from .overlap_core import load_overlap_detail
from .multiplicity_core import (
    build_multiplicity_detail, build_multiplicity_receipt, evaluate_multiplicity,
    load_admission_map, load_multiplicity_policy,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def run(manifest_path: Path, policy_path: Path, overlap_detail_path: Path,
        overlap_detail_sha256: str, output_path: Path,
        admission_map_path: Path | None = None) -> tuple[bytes, dict[str, str]]:
    manifest = load_manifest(manifest_path)
    policy, policy_sha256 = load_multiplicity_policy(policy_path)
    overlap = load_overlap_detail(overlap_detail_path, overlap_detail_sha256)
    verify_overlap_detail(overlap, manifest)
    admission, admission_sha256 = (
        load_admission_map(admission_map_path, manifest, overlap_detail_sha256, policy)
        if admission_map_path is not None else (None, None))
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
                                Path(args.admission_map) if args.admission_map else None)
        sys.stdout.buffer.write(receipt)
        for name, status in statuses.items():
            sys.stderr.write(f"{name} {status}\n")
        return 0
    except Refusal as exc:
        sys.stderr.write(exc.code + "\n")
        return 4 if exc.code == "output_unavailable" else 2
    except Exception:
        sys.stderr.write("internal_refusal\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
