"""Private artifact census and local two-direction calibration command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .artifacts_core import (
    LABEL_LIMIT, calibrate, census, parse_artifact_labels, parse_artifact_policy,
)
from .common import (
    Refusal, bind_input, canonical_json, check_candidate_sizes, confine_output_path,
    emit_committed, finish_manifest, plan_manifest, publish_bundle, require_hex,
    validate_output_path, POLICY_LIMIT,
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def run_census(manifest_path: Path, policy_path: Path,
               out_bundle: Path) -> tuple[bytes, dict[str, str]]:
    """Run in phases so the first refusal matches slice 1 §4.6's master order."""
    # Confinement: every named input and the output location.
    manifest_input = bind_input(manifest_path)
    policy_input = bind_input(policy_path)
    confine_output_path(manifest_input.root, out_bundle)
    # Manifest paths (confinement, then aliasing), then every size ceiling.
    plan = plan_manifest(manifest_input)
    policy_input.check_size(POLICY_LIMIT)
    check_candidate_sizes(plan)
    # Contracts in master order: manifest, then policy.
    manifest = finish_manifest(plan)[0]
    policy = parse_artifact_policy(policy_input.read(POLICY_LIMIT))
    result = census(manifest, policy)
    receipt = canonical_json(result.receipt)
    dest = validate_output_path(manifest.root, out_bundle)
    publish_bundle(dest, {"detail.json": canonical_json(result.detail),
                          "receipt.json": receipt})
    return receipt, result.receipt["stage_status"]


def run_calibrate(manifest_path: Path, expected_manifest: str,
                  labels_path: Path, expected_labels: str, policy_path: Path,
                  out_bundle: Path) -> tuple[bytes, dict[str, str]]:
    """The census phases plus the labels file; the expected hashes compare last,
    since §4.6 ranks calibration_binding after every contract."""
    manifest_input = bind_input(manifest_path)
    policy_input = bind_input(policy_path)
    labels_input = bind_input(labels_path)
    confine_output_path(manifest_input.root, out_bundle)
    plan = plan_manifest(manifest_input)
    policy_input.check_size(POLICY_LIMIT)
    labels_input.check_size(LABEL_LIMIT)
    check_candidate_sizes(plan)
    manifest, violations, identity = finish_manifest(plan, calibration=True)
    policy = parse_artifact_policy(policy_input.read(POLICY_LIMIT))
    labels, labels_sha256 = parse_artifact_labels(
        labels_input.read(LABEL_LIMIT), policy,
        {record.id for record in manifest.records} | set(violations))
    result = calibrate(manifest, violations, identity, labels, labels_sha256, policy)
    for expected, actual in ((expected_manifest, manifest.manifest_sha256),
                             (expected_labels, labels_sha256)):
        if require_hex(expected, "calibration_binding") != actual:
            raise Refusal("calibration_binding")
    receipt = canonical_json(result.receipt)
    dest = validate_output_path(manifest.root, out_bundle)
    publish_bundle(dest, {"detail.json": canonical_json(result.detail),
                          "receipt.json": receipt})
    return receipt, {"calibration": result.receipt["calibration_status"]}


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(add_help=False, allow_abbrev=False)
    sub = parser.add_subparsers(dest="mode", required=True, parser_class=_Parser)
    for mode in ("census", "calibrate"):
        entry = sub.add_parser(mode, add_help=False, allow_abbrev=False)
        entry.add_argument("--manifest", required=True)
        entry.add_argument("--policy", required=True)
        entry.add_argument("--out-bundle", required=True)
        if mode == "calibrate":
            entry.add_argument("--expect-manifest-sha256", required=True)
            entry.add_argument("--labels", required=True)
            entry.add_argument("--expect-labels-sha256", required=True)
    try:
        args = parser.parse_args(argv)
        if args.mode == "census":
            receipt, statuses = run_census(Path(args.manifest), Path(args.policy),
                                           Path(args.out_bundle))
        else:
            receipt, statuses = run_calibrate(
                Path(args.manifest), args.expect_manifest_sha256,
                Path(args.labels), args.expect_labels_sha256,
                Path(args.policy), Path(args.out_bundle))
    except Refusal as exc:
        sys.stderr.write(exc.code + "\n")
        return 4 if exc.code == "output_unavailable" else 2
    except Exception:
        sys.stderr.write("internal_refusal\n")
        return 2
    # Slice 1 section 4.7 streams: stdout carries only the committed receipt
    # bytes, stderr one aggregate line per completed stage.
    emit_committed(receipt, [f"{name} {status}" for name, status in statuses.items()])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
