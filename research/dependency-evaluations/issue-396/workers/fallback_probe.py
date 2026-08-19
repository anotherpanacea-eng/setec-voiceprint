#!/usr/bin/env python3
"""Probe the documented no-datasketch boundary under ``python -S``."""

from __future__ import annotations

import json
import sys

import near_dup_dedup as nd


def main() -> int:
    result: dict[str, object] = {"base_import": True}
    try:
        nd.dedup_records([("a", "one two three"), ("b", "one two three")])
    except RuntimeError as exc:
        result["document_mode_error"] = str(exc)
    else:
        result["document_mode_error"] = None

    passages = nd.chunk_document("a", "one two three four five six seven eight nine ten")
    try:
        nd.stage_a_clusters(passages)
    except RuntimeError as exc:
        result["stage_a_error"] = str(exc)
    else:
        result["stage_a_error"] = None

    repeated = "one two three four five six seven eight nine ten eleven twelve"
    stage_b = nd.stage_b_spans(
        [("a", repeated + " alpha"), ("b", repeated + " beta")],
        min_span_words=10,
    )
    result["stage_b_available"] = bool(stage_b["repeated_spans"])
    json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
