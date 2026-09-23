"""Independent sealed-holdout firewall with separate private and generator outputs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .common import Refusal, WorkBudget, load_manifest, publish_bundle, read_bounded
from .holdout_core import (
    CEILINGS, LABEL, POLICY_LIMIT, SealedSet, build_outputs, holdout_firewall,
    parse_holdout_policy,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def _confine(candidate_manifest: Path, sealed: list[tuple[str, Path]],
             private_out: Path, conflicts_out: Path) -> None:
    """Refuse `path_confinement` before any file is opened (slice 4 section 5)."""
    try:
        candidate_root = candidate_manifest.parent.resolve(strict=True)
        sealed_roots = [path.parent.resolve(strict=True) for _, path in sealed]
    except OSError:
        raise Refusal("path_confinement") from None
    for root in sealed_roots:
        if root == candidate_root or root in candidate_root.parents or candidate_root in root.parents:
            raise Refusal("path_confinement")
    roots = [candidate_root, *sealed_roots]
    # A missing output parent is not a confinement fault: resolve what exists and
    # leave `output_unavailable` to the output phase, after every input check.
    try:
        outputs = [private_out.resolve(), conflicts_out.resolve()]
    except (OSError, RuntimeError):
        raise Refusal("path_confinement") from None
    if outputs[0] == outputs[1] or outputs[0] in outputs[1].parents or outputs[1] in outputs[0].parents:
        raise Refusal("path_confinement")
    for output in outputs:
        for root in roots:
            if output == root or output in root.parents or root in output.parents:
                raise Refusal("path_confinement")


def _check_outputs(private_out: Path, conflicts_out: Path) -> None:
    for raw in (private_out, conflicts_out):
        if raw.exists() or raw.is_symlink():
            raise Refusal("output_collision")
    for raw in (private_out, conflicts_out):
        if not raw.parent.is_dir() or raw.parent.is_symlink():
            raise Refusal("output_unavailable")


def run(candidate_manifest_path: Path, sealed_paths: list[tuple[str, Path]],
        policy_path: Path, private_out: Path, conflicts_out: Path) -> tuple[dict[str, str], bool]:
    """Phases follow slice 1 section 4.6's master order: confinement, the policy
    file's binding and size, manifest intake, policy contract, enumeration
    (`work_limit`), `holdout_contract`, then the two output checks."""
    candidate_manifest_path = Path(os.path.abspath(candidate_manifest_path))
    sealed_paths = [(label, Path(os.path.abspath(path))) for label, path in sealed_paths]
    policy_path = Path(os.path.abspath(policy_path))
    private_out = Path(os.path.abspath(private_out))
    conflicts_out = Path(os.path.abspath(conflicts_out))
    _confine(candidate_manifest_path, sealed_paths, private_out, conflicts_out)
    policy_snapshot = read_bounded(policy_path.parent, policy_path.name, POLICY_LIMIT)
    candidates = load_manifest(candidate_manifest_path)
    total = sum(len(record.candidate.data) for record in
                {record.path: record for record in candidates.records}.values())
    sealed = []
    for label, path in sealed_paths:
        manifest = load_manifest(path, combined_limit=128 * 1024 * 1024 - total)
        total += sum(len(record.candidate.data) for record in
                     {record.path: record for record in manifest.records}.values())
        sealed.append(SealedSet(label, manifest))
    policy, policy_sha256 = parse_holdout_policy(policy_snapshot)
    budget = WorkBudget(CEILINGS)
    result = holdout_firewall(candidates, tuple(sealed), policy, budget)
    if (not 1 <= len(sealed) <= 8 or len({item.label for item in sealed}) != len(sealed)
            or any(type(item.label) is not str or LABEL.fullmatch(item.label) is None
                   for item in sealed)
            or len({item.manifest.manifest_sha256 for item in sealed}) != len(sealed)):
        raise Refusal("holdout_contract")
    detail, receipt, conflicts = build_outputs(result, candidates, tuple(sealed),
                                               policy_sha256)
    _check_outputs(private_out, conflicts_out)
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
