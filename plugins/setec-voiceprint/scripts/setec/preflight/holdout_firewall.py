"""Independent sealed-holdout firewall with separate private and generator outputs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .common import Refusal, WorkBudget, load_manifest, publish_bundle
from .holdout_core import (
    CEILINGS, LABEL, SealedSet, build_outputs, holdout_firewall,
    load_holdout_policy,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def _paths(candidate_manifest: Path, sealed: list[tuple[str, Path]],
           private_out: Path, conflicts_out: Path) -> None:
    try:
        candidate_root = candidate_manifest.parent.resolve(strict=True)
        sealed_roots = [path.parent.resolve(strict=True) for _, path in sealed]
        private_parent = private_out.parent.resolve(strict=True)
        conflicts_parent = conflicts_out.parent.resolve(strict=True)
    except OSError:
        raise Refusal("path_confinement") from None
    for root in sealed_roots:
        if root == candidate_root or root in candidate_root.parents or candidate_root in root.parents:
            raise Refusal("path_confinement")
    roots = [candidate_root, *sealed_roots]
    outputs = [private_parent / private_out.name, conflicts_parent / conflicts_out.name]
    if outputs[0] == outputs[1] or outputs[0] in outputs[1].parents or outputs[1] in outputs[0].parents:
        raise Refusal("path_confinement")
    for output in outputs:
        for root in roots:
            if output == root or output in root.parents or root in output.parents:
                raise Refusal("path_confinement")
    for raw in (private_out, conflicts_out):
        if raw.exists() or raw.is_symlink():
            raise Refusal("output_collision")
        if not raw.parent.is_dir() or raw.parent.is_symlink():
            raise Refusal("output_unavailable")


def run(candidate_manifest_path: Path, sealed_paths: list[tuple[str, Path]],
        policy_path: Path, private_out: Path, conflicts_out: Path) -> tuple[dict[str, str], bool]:
    candidate_manifest_path = Path(os.path.abspath(candidate_manifest_path))
    sealed_paths = [(label, Path(os.path.abspath(path))) for label, path in sealed_paths]
    private_out = Path(os.path.abspath(private_out))
    conflicts_out = Path(os.path.abspath(conflicts_out))
    if (not 1 <= len(sealed_paths) <= 8 or len({label for label, _ in sealed_paths}) != len(sealed_paths)
            or any(type(label) is not str or LABEL.fullmatch(label) is None
                   for label, _ in sealed_paths)):
        raise Refusal("holdout_contract")
    _paths(candidate_manifest_path, sealed_paths, private_out, conflicts_out)
    candidates = load_manifest(candidate_manifest_path)
    total = sum(len(record.candidate.data) for record in
                {record.path: record for record in candidates.records}.values())
    sealed = []
    hashes: set[str] = set()
    for label, path in sealed_paths:
        manifest = load_manifest(path, combined_limit=128 * 1024 * 1024 - total)
        if manifest.manifest_sha256 in hashes:
            raise Refusal("holdout_contract")
        hashes.add(manifest.manifest_sha256)
        total += sum(len(record.candidate.data) for record in
                     {record.path: record for record in manifest.records}.values())
        sealed.append(SealedSet(label, manifest))
    policy, policy_sha256 = load_holdout_policy(policy_path)
    budget = WorkBudget(CEILINGS)
    result = holdout_firewall(candidates, tuple(sealed), policy, budget)
    detail, receipt, conflicts = build_outputs(result, candidates, tuple(sealed),
                                               policy_sha256)
    publish_bundle(private_out, {"detail.json": detail, "receipt.json": receipt})
    if conflicts is not None:
        try:
            publish_bundle(conflicts_out, {"conflicts.json": conflicts})
        except Refusal:
            raise Refusal("output_unavailable") from None
    return result.stage_status, conflicts is not None


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--candidate-manifest", required=True)
    parser.add_argument("--sealed", nargs=2, action="append", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--private-out", required=True)
    parser.add_argument("--conflicts-out", required=True)
    try:
        args = parser.parse_args(argv)
        statuses, _ = run(Path(args.candidate_manifest),
                          [(label, Path(path)) for label, path in args.sealed],
                          Path(args.policy), Path(args.private_out),
                          Path(args.conflicts_out))
        for name in statuses:
            sys.stderr.write(name + "\n")
        return 0
    except Refusal as exc:
        sys.stderr.write(exc.code + "\n")
        return 4 if exc.code == "output_unavailable" else 2
    except Exception:
        sys.stderr.write("internal_refusal\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
