"""Offline source-span proof and boundary-class command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .common import Refusal, load_manifest, publish_bundle, validate_output_path
from .span_core import build_span, load_span_policy


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise Refusal("input_contract")


def run(manifest_path: Path, policy_path: Path, output_path: Path) -> tuple[bytes, dict]:
    manifest = load_manifest(manifest_path)
    policy = load_span_policy(policy_path)
    detail, receipt, statuses = build_span(manifest, policy)
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
