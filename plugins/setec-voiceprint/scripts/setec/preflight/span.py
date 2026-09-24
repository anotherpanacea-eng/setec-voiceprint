"""Offline source-span proof and boundary-class command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .common import (
    Refusal, bind_input, check_candidate_sizes, confine_output_path, emit_committed,
    finish_manifest, plan_manifest, publish_bundle, validate_output_path,
)
from .span_core import (
    POLICY_LIMIT, bind_span_sources, build_span, check_source_sizes, parse_span_policy,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def run(manifest_path: Path, policy_path: Path, output_path: Path) -> tuple[bytes, dict]:
    """Run in phases so the first refusal matches slice 1 §4.6's master order."""
    # Confinement: every named input and the output location.
    manifest_input = bind_input(manifest_path)
    policy_input = bind_input(policy_path)
    confine_output_path(manifest_input.root, output_path)
    # Manifest paths (confinement, then aliasing), then every size ceiling.
    plan = plan_manifest(manifest_input)
    policy_input.check_size(POLICY_LIMIT)
    check_candidate_sizes(plan)
    check_source_sizes(plan)
    # Contracts: the manifest, the sources' re-binding, then the policy, and
    # then the counted classifier work.
    manifest = finish_manifest(plan)[0]
    policy_snapshot = policy_input.read(POLICY_LIMIT)
    sources = bind_span_sources(manifest)
    policy = parse_span_policy(policy_snapshot)
    detail, receipt, statuses = build_span(manifest, policy, sources)
    dest = validate_output_path(manifest.root, output_path)
    publish_bundle(dest, {"detail.json": detail, "receipt.json": receipt})
    return receipt, statuses


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(add_help=False, allow_abbrev=False)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--out-bundle", required=True)
    try:
        args = parser.parse_args(argv)
        receipt, statuses = run(Path(args.manifest), Path(args.policy), Path(args.out_bundle))
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
