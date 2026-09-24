"""Offline overlap-cluster and split-integrity command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .common import (
    Refusal, bind_input, check_candidate_sizes, confine_output_path, emit_committed,
    finish_manifest, plan_manifest, publish_bundle, validate_output_path, POLICY_LIMIT,
    SPLIT_LIMIT,
)
from .overlap_core import build_overlap, parse_overlap_policy, parse_split_map


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def run(manifest_path: Path, policy_path: Path, output_path: Path,
        split_map_path: Path | str | None = None) -> tuple[bytes, dict[str, str]]:
    """Run slice 1 in phases so the first refusal matches §4.6's master order."""
    empty_split = split_map_path == ""
    # Confinement: every named input and the output location.
    manifest_input = bind_input(manifest_path)
    policy_input = bind_input(policy_path)
    split_input = (bind_input(Path(split_map_path))
                   if split_map_path is not None and not empty_split else None)
    confine_output_path(manifest_input.root, output_path)
    # Manifest paths (confinement, then aliasing), then every size ceiling.
    plan = plan_manifest(manifest_input)
    policy_input.check_size(POLICY_LIMIT)
    if split_input is not None:
        split_input.check_size(SPLIT_LIMIT)
    check_candidate_sizes(plan)
    # Contracts in master order: manifest, policy, split map.
    manifest = finish_manifest(plan)[0]
    policy, policy_sha256 = parse_overlap_policy(policy_input.read(POLICY_LIMIT))
    if empty_split:
        raise Refusal("split_contract")
    assignments, split_sha256 = (parse_split_map(split_input.read(SPLIT_LIMIT), manifest.records)
                                 if split_input is not None else (None, None))
    detail, receipt, statuses = build_overlap(manifest, policy, policy_sha256,
                                               assignments, split_sha256)
    dest = validate_output_path(manifest.root, output_path)
    publish_bundle(dest, {"detail.json": detail, "receipt.json": receipt})
    return receipt, statuses


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--out-bundle", required=True)
    parser.add_argument("--split-map")
    try:
        args = parser.parse_args(argv)
        receipt, statuses = run(Path(args.manifest), Path(args.policy),
                                Path(args.out_bundle), args.split_map)
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
