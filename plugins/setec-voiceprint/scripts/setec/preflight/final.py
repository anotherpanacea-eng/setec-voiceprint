"""Verify a final packet against the intake overlap graph."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .common import (
    Refusal, canonical_json, load_manifest, publish_bundle, validate_output_path,
)
from .final_core import load_intake_bundle, project_final
from .overlap_core import load_overlap_policy, load_split_map


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def run(intake_bundle: Path, manifest_path: Path, policy_path: Path,
        split_map_path: Path, out_bundle: Path) -> tuple[bytes, dict[str, str]]:
    manifest = load_manifest(manifest_path)
    policy, policy_sha256 = load_overlap_policy(policy_path)
    intake_detail, _, intake_receipt_hash, intake_detail_hash = load_intake_bundle(
        intake_bundle, policy, policy_sha256)
    assignments, split_hash = load_split_map(split_map_path, manifest.records)
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
