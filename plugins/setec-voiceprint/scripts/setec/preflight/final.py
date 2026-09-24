"""Verify a final packet against the intake overlap graph."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .common import (
    Refusal, bind_input, canonical_json, check_candidate_sizes, confine_output_path,
    emit_committed, finish_manifest, plan_manifest, publish_bundle, validate_output_path,
    POLICY_LIMIT, SPLIT_LIMIT,
)
from .final_core import load_intake_bundle, project_final
from .overlap_core import parse_overlap_policy, parse_split_map


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def run(intake_bundle: Path, manifest_path: Path, policy_path: Path,
        split_map_path: Path, out_bundle: Path) -> tuple[bytes, dict[str, str]]:
    """Run in phases so the first refusal matches slice 1 §4.6's master order.

    The intake bundle is slice 1's artifact: its strict reloaders report every
    failure as ``receipt_contract`` or ``detail_contract``, which rank after
    ``split_contract``, so the bundle is read after every control file.
    """
    # Confinement: every named control file and the output location.
    manifest_input = bind_input(manifest_path)
    policy_input = bind_input(policy_path)
    split_input = bind_input(split_map_path)
    confine_output_path(manifest_input.root, out_bundle)
    # Manifest paths (confinement, then aliasing), then every size ceiling.
    plan = plan_manifest(manifest_input)
    policy_input.check_size(POLICY_LIMIT)
    split_input.check_size(SPLIT_LIMIT)
    check_candidate_sizes(plan)
    # Contracts in master order: manifest, policy, split map, intake bundle.
    manifest = finish_manifest(plan)[0]
    policy, policy_sha256 = parse_overlap_policy(policy_input.read(POLICY_LIMIT))
    assignments, split_hash = parse_split_map(split_input.read(SPLIT_LIMIT), manifest.records)
    intake_detail, _, intake_receipt_hash, intake_detail_hash = load_intake_bundle(
        intake_bundle, policy, policy_sha256)
    result = project_final(intake_detail, manifest, policy, assignments, split_hash,
                           intake_receipt_hash, intake_detail_hash)
    detail_bytes = canonical_json(result.detail)
    receipt_bytes = canonical_json(result.receipt)
    dest = validate_output_path(manifest.root, out_bundle)
    publish_bundle(dest, {"detail.json": detail_bytes, "receipt.json": receipt_bytes})
    return receipt_bytes, result.receipt["stage_status"]


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--intake-bundle", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--split-map", required=True)
    parser.add_argument("--out-bundle", required=True)
    try:
        args = parser.parse_args(argv)
        receipt, statuses = run(Path(args.intake_bundle), Path(args.manifest),
                                Path(args.policy), Path(args.split_map),
                                Path(args.out_bundle))
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
