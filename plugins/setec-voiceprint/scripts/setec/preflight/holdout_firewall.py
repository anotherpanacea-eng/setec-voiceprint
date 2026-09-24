"""Independent sealed-holdout firewall with separate private and generator outputs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .common import (
    COMBINED_CANDIDATE_LIMIT, Refusal, WorkBudget, bind_input, check_candidate_sizes,
    confine_output_path, emit_committed, finish_manifest, paths_nest, plan_manifest,
    publish_bundle,
)
from .holdout_core import (
    CEILINGS, LABEL, POLICY_LIMIT, SealedSet, build_outputs, holdout_firewall,
    parse_holdout_policy,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def _confine(candidate_root: Path, sealed_roots: list[Path],
             private_out: Path, conflicts_out: Path) -> None:
    """Refuse `path_confinement` before any file is opened (spec 04 section 5).

    Every comparison is by file identity (common's `paths_nest`), so a
    case-folding or symlinked spelling of a root cannot slip past. A missing or
    looping output parent is not a confinement fault: it is left to the output
    phase's `output_unavailable`, after every input check.
    """
    try:
        if any(paths_nest(candidate_root, root) for root in sealed_roots):
            raise Refusal("path_confinement")
        if paths_nest(private_out, conflicts_out):
            raise Refusal("path_confinement")
    except OSError:
        raise Refusal("path_confinement") from None
    for output in (private_out, conflicts_out):
        for root in (candidate_root, *sealed_roots):
            confine_output_path(root, output)


def _check_outputs(private_out: Path, conflicts_out: Path) -> None:
    for raw in (private_out, conflicts_out):
        if raw.exists() or raw.is_symlink():
            raise Refusal("output_collision")
    for raw in (private_out, conflicts_out):
        if not raw.parent.is_dir() or raw.parent.is_symlink():
            raise Refusal("output_unavailable")


def run(candidate_manifest_path: Path, sealed_paths: list[tuple[str, Path]],
        policy_path: Path, private_out: Path, conflicts_out: Path) -> tuple[dict[str, str], bool]:
    """Run in phases so the first refusal matches slice 1 section 4.6's master
    order: confinement (roots and outputs, then every file and every path each
    manifest names), every size ceiling, the manifest and policy contracts,
    enumeration (`work_limit`), `holdout_contract`, then the two output checks."""
    candidate_manifest_path = Path(os.path.abspath(candidate_manifest_path))
    sealed_paths = [(label, Path(os.path.abspath(path))) for label, path in sealed_paths]
    private_out = Path(os.path.abspath(private_out))
    conflicts_out = Path(os.path.abspath(conflicts_out))
    _confine(candidate_manifest_path.parent, [path.parent for _, path in sealed_paths],
             private_out, conflicts_out)
    candidate_input = bind_input(candidate_manifest_path)
    sealed_inputs = [(label, bind_input(path)) for label, path in sealed_paths]
    policy_input = bind_input(policy_path)
    # Each manifest plan can refuse before the next manifest is inspected.
    # Collect this phase's refusals across the whole packet, so an alias (or
    # oversized manifest) cannot hide confinement failure in a later manifest.
    plans = []
    plan_refusals = []
    for source in (candidate_input, *(source for _, source in sealed_inputs)):
        try:
            plans.append(plan_manifest(source))
        except Refusal as exc:
            plan_refusals.append(exc)
    if plan_refusals:
        priority = {code: index for index, code in enumerate(
            ("input_changed", "path_confinement", "path_alias", "size_limit"))}
        raise min(plan_refusals, key=lambda exc: priority[exc.code])
    candidate_plan = plans[0]
    sealed_plans = [(label, plan) for (label, _), plan in zip(sealed_inputs, plans[1:])]
    # Sizes: the policy, then every manifest's candidates under one combined ceiling.
    policy_input.check_size(POLICY_LIMIT)
    remaining = COMBINED_CANDIDATE_LIMIT
    for plan in (candidate_plan, *(plan for _, plan in sealed_plans)):
        check_candidate_sizes(plan, combined_limit=remaining)
        remaining -= sum(plan.bound[name][1][2] for name in plan.candidates)
    candidates = finish_manifest(candidate_plan)[0]
    sealed = [SealedSet(label, finish_manifest(plan)[0]) for label, plan in sealed_plans]
    policy, policy_sha256 = parse_holdout_policy(policy_input.read(POLICY_LIMIT))
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
    except Refusal as exc:
        sys.stderr.write(exc.code + "\n")
        return 4 if exc.code == "output_unavailable" else 2
    except Exception:
        sys.stderr.write("internal_refusal\n")
        return 2
    # Stdout stays empty (spec 04 section 7): the receipt is private.
    emit_committed(b"", list(statuses))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
