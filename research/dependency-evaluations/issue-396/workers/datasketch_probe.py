#!/usr/bin/env python3
"""Version-local datasketch/RapidFuzz probe for Voiceprint issue #396.

The worker deliberately calls :func:`near_dup_dedup.dedup_records` for both
the control and traced runs.  A worker-local wrapper observes the three layers
that the production return value does not expose (LSH query candidates,
estimated-Jaccard passing edges, and final co-cluster pairs).  The traced result
must remain byte-identical to its unwrapped control before any trace is emitted.

Input and output are one canonical JSON object on stdin/stdout.  Diagnostics go
to stderr and a failed request exits non-zero, so the controller can never
mistake a partial result for evidence.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import itertools
import json
import math
import platform
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator


SCHEMA = "setec-dependency-evaluation/1"
VALID_ACTIONS = frozenset({"accuracy", "scale"})
VALID_SCHEMES = frozenset({"default", "legacy"})
PRIMARY_RAPIDFUZZ_CUTOFF = 85
EXPLORATORY_RAPIDFUZZ_CUTOFFS = (80, 90)

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import near_dup_dedup as ndd  # noqa: E402


Pair = tuple[str, str]


def canonical_json_bytes(value: Any) -> bytes:
    """Return the packet's canonical stable-JSON encoding."""
    _reject_non_finite(value)
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("stable JSON forbids non-finite numbers")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("stable JSON object keys must be strings")
            _reject_non_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_non_finite(item)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _pair(left: str, right: str) -> Pair:
    return (left, right) if left < right else (right, left)


def _pair_rows(pairs: Iterable[Pair]) -> list[list[str]]:
    return [list(item) for item in sorted(set(pairs))]


@dataclass
class Trace:
    """Observation-only state shared by the transparent wrappers."""

    threshold: float
    key_by_object: dict[int, str] = field(default_factory=dict)
    raw_lsh: set[Pair] = field(default_factory=set)
    estimated_pass: set[Pair] = field(default_factory=set)
    estimated_scores: dict[Pair, float] = field(default_factory=dict)

    def bind(self, key: Any, minhash: Any) -> None:
        self.key_by_object[id(minhash)] = str(key)

    def key(self, minhash: Any) -> str | None:
        return self.key_by_object.get(id(minhash))

    def record_query(self, query: Any, candidates: Iterable[Any]) -> None:
        left = self.key(query)
        if left is None:
            raise RuntimeError("tracing wrapper observed an unbound query MinHash")
        for candidate in candidates:
            right = str(candidate)
            if right != left:
                self.raw_lsh.add(_pair(left, right))

    def record_jaccard(self, left_obj: Any, right_obj: Any, score: float) -> None:
        left = self.key(left_obj)
        right = self.key(right_obj)
        if left is None or right is None:
            raise RuntimeError("tracing wrapper observed an unbound MinHash comparison")
        if left == right:
            return
        key = _pair(left, right)
        prior = self.estimated_scores.get(key)
        if prior is not None and prior != score:
            raise RuntimeError(f"estimated Jaccard changed within one run for {key!r}")
        self.estimated_scores[key] = score
        if score >= self.threshold:
            self.estimated_pass.add(key)


def _supports_scheme(minhash_class: type[Any]) -> bool:
    try:
        return "scheme" in inspect.signature(minhash_class).parameters
    except (TypeError, ValueError):
        return False


def _make_minhash_adapter(
    real_minhash: type[Any],
    *,
    scheme: str,
    trace: Trace | None,
) -> type[Any]:
    """Return a default-transparent or explicit-legacy MinHash subclass."""
    if scheme not in VALID_SCHEMES:
        raise ValueError(f"unsupported scheme {scheme!r}")
    if scheme == "legacy" and not _supports_scheme(real_minhash):
        raise ValueError(
            "scheme='legacy' requires datasketch 2.0.0 or newer; "
            "the installed MinHash constructor has no scheme parameter"
        )

    class AdaptedMinHash(real_minhash):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if scheme == "legacy":
                # Research adapter only.  Production passes num_perm and relies
                # on seed=1; make both compatibility determinants explicit.
                kwargs["seed"] = 1
                kwargs["scheme"] = "legacy"
            super().__init__(*args, **kwargs)

        def jaccard(self, other: Any) -> float:
            score = float(super().jaccard(other))
            if trace is not None:
                trace.record_jaccard(self, other, score)
            return score

    AdaptedMinHash.__name__ = (
        "Issue396LegacyMinHash" if scheme == "legacy" else "Issue396TracingMinHash"
    )
    return AdaptedMinHash


def _make_lsh_adapter(real_lsh: type[Any], *, trace: Trace) -> type[Any]:
    """Return a delegating LSH subclass that records query candidates."""

    class TracingMinHashLSH(real_lsh):  # type: ignore[misc, valid-type]
        def insert(self, key: Any, minhash: Any, *args: Any, **kwargs: Any) -> Any:
            trace.bind(key, minhash)
            return super().insert(key, minhash, *args, **kwargs)

        def query(self, minhash: Any) -> list[Any]:
            candidates = list(super().query(minhash))
            trace.record_query(minhash, candidates)
            return candidates

    TracingMinHashLSH.__name__ = "Issue396TracingMinHashLSH"
    return TracingMinHashLSH


@contextmanager
def _patched_datasketch_classes(
    minhash_class: type[Any], lsh_class: type[Any],
) -> Iterator[None]:
    original = ndd._require_datasketch
    ndd._require_datasketch = lambda: (minhash_class, lsh_class)
    try:
        yield
    finally:
        ndd._require_datasketch = original


def _real_classes() -> tuple[type[Any], type[Any]]:
    """Resolve the installed classes through the production lazy-import seam."""
    return ndd._require_datasketch()


def _run_control(
    records: list[tuple[str, str]],
    *,
    threshold: float,
    num_perm: int,
    shingle_size: int,
    scheme: str,
) -> dict[str, Any]:
    real_minhash, real_lsh = _real_classes()
    if scheme == "default":
        # This is the genuinely unwrapped shipped run.
        result = ndd.dedup_records(
            records,
            threshold=threshold,
            num_perm=num_perm,
            shingle_size=shingle_size,
        )
    else:
        legacy_minhash = _make_minhash_adapter(
            real_minhash, scheme="legacy", trace=None,
        )
        with _patched_datasketch_classes(legacy_minhash, real_lsh):
            result = ndd.dedup_records(
                records,
                threshold=threshold,
                num_perm=num_perm,
                shingle_size=shingle_size,
            )
    return result.to_dict()


def _run_once(
    records: list[tuple[str, str]],
    *,
    threshold: float,
    num_perm: int,
    shingle_size: int,
    scheme: str,
) -> dict[str, Any]:
    """Call the real seam exactly once, with only the named legacy adapter."""
    real_minhash, real_lsh = _real_classes()
    if scheme == "default":
        result = ndd.dedup_records(
            records,
            threshold=threshold,
            num_perm=num_perm,
            shingle_size=shingle_size,
        )
    else:
        legacy_minhash = _make_minhash_adapter(
            real_minhash, scheme="legacy", trace=None,
        )
        with _patched_datasketch_classes(legacy_minhash, real_lsh):
            result = ndd.dedup_records(
                records,
                threshold=threshold,
                num_perm=num_perm,
                shingle_size=shingle_size,
            )
    return result.to_dict()


def _run_traced(
    records: list[tuple[str, str]],
    *,
    threshold: float,
    num_perm: int,
    shingle_size: int,
    scheme: str,
) -> tuple[dict[str, Any], Trace]:
    real_minhash, real_lsh = _real_classes()
    trace = Trace(threshold=threshold)
    traced_minhash = _make_minhash_adapter(
        real_minhash, scheme=scheme, trace=trace,
    )
    traced_lsh = _make_lsh_adapter(real_lsh, trace=trace)
    with _patched_datasketch_classes(traced_minhash, traced_lsh):
        result = ndd.dedup_records(
            records,
            threshold=threshold,
            num_perm=num_perm,
            shingle_size=shingle_size,
        )
    return result.to_dict(), trace


def _co_cluster_pairs(result: dict[str, Any]) -> set[Pair]:
    out: set[Pair] = set()
    for representative, dropped in result.get("clusters", {}).items():
        members = [str(representative), *(str(item) for item in dropped)]
        out.update(_pair(left, right) for left, right in itertools.combinations(members, 2))
    return out


def _class_for_signature(scheme: str) -> type[Any]:
    real_minhash, _ = _real_classes()
    return _make_minhash_adapter(real_minhash, scheme=scheme, trace=None)


def _signature_report(
    records: list[tuple[str, str]],
    *,
    scheme: str,
    num_perm: int,
    shingle_size: int,
) -> dict[str, Any]:
    minhash_class = _class_for_signature(scheme)
    per_record: list[dict[str, str]] = []
    sample: dict[str, Any] | None = None
    actual_scheme: str | None = None
    seed: int | None = None
    for record_id, text in sorted(records):
        minhash = ndd._build_minhash(
            minhash_class, text, num_perm=num_perm, k=shingle_size,
        )
        values = [int(value) for value in minhash.hashvalues]
        digest = _sha256_json(values)
        per_record.append({"id": record_id, "hashvalues_sha256": digest})
        observed_scheme = getattr(minhash, "scheme", None)
        observed_scheme = str(observed_scheme) if observed_scheme is not None else "legacy"
        if actual_scheme is None:
            actual_scheme = observed_scheme
            seed = int(minhash.seed)
            sample = {
                "id": record_id,
                "hashvalues": values,
                "hashvalues_sha256": digest,
            }
        elif actual_scheme != observed_scheme or seed != int(minhash.seed):
            raise RuntimeError("signature determinants changed between records")
    aggregate = _sha256_json(per_record)
    return {
        "version": importlib.metadata.version("datasketch"),
        "requested_scheme": scheme,
        "scheme": actual_scheme or ("legacy" if scheme == "default" else scheme),
        "num_perm": num_perm,
        "seed": seed if seed is not None else 1,
        "shingle_size": shingle_size,
        "normalization": "near_dup_dedup.shingles/word-regex-lower-v1",
        "record_count": len(records),
        "per_record": per_record,
        "aggregate_sha256": aggregate,
        "sample": sample,
    }


def _normalized_string(text: str) -> str:
    # Keep this bound to the shipped shingle tokenizer: ``_WORD_RE`` + lower.
    return " ".join(token.lower() for token in ndd._WORD_RE.findall(text))


def _exact_positive_pairs(
    records: dict[str, str], *, threshold: float, shingle_size: int,
) -> set[Pair]:
    shingle_sets = {
        record_id: ndd.shingles(text, k=shingle_size)
        for record_id, text in records.items()
    }
    out: set[Pair] = set()
    for left, right in itertools.combinations(sorted(records), 2):
        left_set = shingle_sets[left]
        right_set = shingle_sets[right]
        union = left_set | right_set
        score = len(left_set & right_set) / len(union) if union else 0.0
        if score >= threshold:
            out.add((left, right))
    return out


def _metrics(predicted: set[Pair], truth: set[Pair]) -> dict[str, Any]:
    tp = predicted & truth
    fp = predicted - truth
    fn = truth - predicted
    return {
        "tp": _pair_rows(tp),
        "fp": _pair_rows(fp),
        "fn": _pair_rows(fn),
        "precision": len(tp) / len(predicted) if predicted else (1.0 if not truth else 0.0),
        "recall": len(tp) / len(truth) if truth else 1.0,
    }


def _rapidfuzz_report(
    records: dict[str, str],
    *,
    raw_pairs: set[Pair],
    exact_truth: set[Pair],
) -> dict[str, Any]:
    try:
        from rapidfuzz import fuzz  # type: ignore
    except ImportError:
        return {"available": False, "reason": "rapidfuzz_not_installed"}

    setup_started = time.perf_counter()
    normalized = {key: _normalized_string(value) for key, value in records.items()}
    materialized_shingles = {
        key: ndd.shingles(value, k=5) for key, value in records.items()
    }
    setup_seconds = time.perf_counter() - setup_started
    candidate_truth = exact_truth & raw_pairs
    by_cutoff: dict[str, Any] = {}
    for cutoff in (
        *EXPLORATORY_RAPIDFUZZ_CUTOFFS[:1],
        PRIMARY_RAPIDFUZZ_CUTOFF,
        *EXPLORATORY_RAPIDFUZZ_CUTOFFS[1:],
    ):
        passing: set[Pair] = set()
        scores: list[dict[str, Any]] = []
        for left, right in sorted(raw_pairs):
            score = float(fuzz.ratio(
                normalized[left], normalized[right], processor=None,
            ))
            scores.append({"pair": [left, right], "score": score})
            if score >= cutoff:
                passing.add((left, right))
        by_cutoff[str(cutoff)] = {
            "cutoff": cutoff,
            "role": "primary" if cutoff == PRIMARY_RAPIDFUZZ_CUTOFF else "exploratory",
            "passing_pairs": _pair_rows(passing),
            "global_oracle_metrics": _metrics(passing, exact_truth),
            "candidate_conditioned_metrics": _metrics(passing, candidate_truth),
            "scores": scores,
        }
    candidates = sorted(raw_pairs)
    repeats = max(1, 10_000 // max(1, len(candidates)))
    rapid_started = time.perf_counter()
    for _ in range(repeats):
        for left, right in candidates:
            fuzz.ratio(normalized[left], normalized[right], processor=None)
    rapid_seconds = time.perf_counter() - rapid_started
    exact_started = time.perf_counter()
    for _ in range(repeats):
        for left, right in candidates:
            left_set = materialized_shingles[left]
            right_set = materialized_shingles[right]
            union = left_set | right_set
            _ = len(left_set & right_set) / len(union) if union else 0.0
    exact_seconds = time.perf_counter() - exact_started
    return {
        "available": True,
        "version": importlib.metadata.version("rapidfuzz"),
        "scorer": "rapidfuzz.fuzz.ratio",
        "processor": None,
        "primary_cutoff": PRIMARY_RAPIDFUZZ_CUTOFF,
        "candidate_count": len(raw_pairs),
        "timing": {
            "materialization_seconds": setup_seconds,
            "score_calls_per_scorer": repeats * len(candidates),
            "rapidfuzz_seconds": rapid_seconds,
            "exact_jaccard_seconds": exact_seconds,
        },
        "cutoffs": by_cutoff,
    }


def _environment() -> dict[str, Any]:
    result = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "datasketch": importlib.metadata.version("datasketch"),
    }
    try:
        result["numpy"] = importlib.metadata.version("numpy")
    except importlib.metadata.PackageNotFoundError:
        result["numpy"] = None
    try:
        result["scipy"] = importlib.metadata.version("scipy")
    except importlib.metadata.PackageNotFoundError:
        result["scipy"] = None
    try:
        result["rapidfuzz"] = importlib.metadata.version("rapidfuzz")
    except importlib.metadata.PackageNotFoundError:
        result["rapidfuzz"] = None
    return result


def _validated_request(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    if payload.get("schema") != SCHEMA:
        raise ValueError("request schema mismatch")
    expected_environment = payload.get("expected_environment")
    required_environment_keys = {
        "python", "implementation", "datasketch", "numpy", "scipy", "rapidfuzz",
    }
    if (
        not isinstance(expected_environment, dict)
        or set(expected_environment) != required_environment_keys
    ):
        raise ValueError(
            "expected_environment must bind python, implementation, datasketch, "
            "numpy, scipy, and rapidfuzz"
        )
    actual_environment = _environment()
    observed = {
        key: actual_environment.get(key) for key in sorted(required_environment_keys)
    }
    if observed != expected_environment:
        raise ValueError(
            f"environment mismatch: expected {expected_environment!r}, got {observed!r}"
        )
    action = payload.get("action")
    if action not in VALID_ACTIONS:
        raise ValueError("unsupported action")
    scheme = payload.get("scheme", "default")
    if scheme not in VALID_SCHEMES:
        raise ValueError(f"scheme must be one of {sorted(VALID_SCHEMES)!r}")
    records_value = payload.get("records")
    if not isinstance(records_value, list):
        raise ValueError("records must be a list of [id, text] pairs")
    records: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(records_value):
        if (
            not isinstance(row, list)
            or len(row) != 2
            or not isinstance(row[0], str)
            or not isinstance(row[1], str)
        ):
            raise ValueError(f"records[{index}] must be [string id, string text]")
        if not row[0] or row[0] in seen:
            raise ValueError(f"records[{index}] has an empty or duplicate id")
        seen.add(row[0])
        records.append((row[0], row[1]))
    threshold = payload.get("threshold", 0.85)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("threshold must be numeric")
    threshold = float(threshold)
    if not math.isfinite(threshold) or not 0 < threshold <= 1:
        raise ValueError("threshold must be finite and in (0, 1]")
    num_perm = payload.get("num_perm", 128)
    shingle_size = payload.get("shingle_size", 5)
    if isinstance(num_perm, bool) or not isinstance(num_perm, int) or num_perm <= 0:
        raise ValueError("num_perm must be a positive integer")
    if (
        isinstance(shingle_size, bool)
        or not isinstance(shingle_size, int)
        or shingle_size <= 0
    ):
        raise ValueError("shingle_size must be a positive integer")
    known_families_value = payload.get("known_families", [])
    if not isinstance(known_families_value, list):
        raise ValueError("known_families must be a list of id lists")
    known_families: list[list[str]] = []
    for index, family in enumerate(known_families_value):
        if (
            not isinstance(family, list)
            or len(family) < 2
            or any(not isinstance(item, str) for item in family)
        ):
            raise ValueError(
                f"known_families[{index}] must contain at least two string ids"
            )
        if len(set(family)) != len(family):
            raise ValueError(f"known_families[{index}] contains duplicate ids")
        missing = sorted(set(family) - seen)
        if missing:
            raise ValueError(
                f"known_families[{index}] references unknown ids: {missing!r}"
            )
        known_families.append(list(family))
    return {
        "action": action,
        "records": records,
        "known_families": known_families,
        "scheme": scheme,
        "threshold": threshold,
        "num_perm": num_perm,
        "shingle_size": shingle_size,
    }


def _evaluate_accuracy(request: dict[str, Any]) -> dict[str, Any]:
    records = request["records"]
    control = _run_control(records, **{k: request[k] for k in (
        "threshold", "num_perm", "shingle_size", "scheme",
    )})
    traced, trace = _run_traced(records, **{k: request[k] for k in (
        "threshold", "num_perm", "shingle_size", "scheme",
    )})
    control_bytes = canonical_json_bytes(control)
    traced_bytes = canonical_json_bytes(traced)
    if control_bytes != traced_bytes:
        raise RuntimeError("tracing wrapper changed the production DedupResult")

    co_cluster = _co_cluster_pairs(traced)
    record_map = dict(records)
    exact_truth = _exact_positive_pairs(
        record_map,
        threshold=request["threshold"],
        shingle_size=request["shingle_size"],
    )
    estimated_scores = [
        {"pair": list(key), "score": value}
        for key, value in sorted(trace.estimated_scores.items())
    ]
    return {
        "schema": SCHEMA,
        "action": "accuracy",
        "environment": _environment(),
        "parameters": {
            "scheme": request["scheme"],
            "threshold": request["threshold"],
            "num_perm": request["num_perm"],
            "shingle_size": request["shingle_size"],
        },
        "signature": _signature_report(
            records,
            scheme=request["scheme"],
            num_perm=request["num_perm"],
            shingle_size=request["shingle_size"],
        ),
        "layers": {
            "raw_lsh": _pair_rows(trace.raw_lsh),
            "estimated_pass": _pair_rows(trace.estimated_pass),
            "co_cluster": _pair_rows(co_cluster),
        },
        "estimated_scores": estimated_scores,
        "dedup_result": traced,
        "control_result_sha256": hashlib.sha256(control_bytes).hexdigest(),
        "traced_result_sha256": hashlib.sha256(traced_bytes).hexdigest(),
        "traced_matches_control": True,
        "rapidfuzz": _rapidfuzz_report(
            record_map,
            raw_pairs=trace.raw_lsh,
            exact_truth=exact_truth,
        ),
    }


def _observed_components(result: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    component_by_id: dict[str, tuple[str, ...]] = {}
    dropped: set[str] = set()
    for representative, members_value in result.get("clusters", {}).items():
        members = tuple(sorted([str(representative), *(str(x) for x in members_value)]))
        for member in members:
            component_by_id[member] = members
        dropped.update(str(item) for item in members_value)
    for record_id in result.get("kept", []):
        record_id = str(record_id)
        if record_id not in dropped:
            component_by_id.setdefault(record_id, (record_id,))
    return component_by_id


def _evaluate_scale(request: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    result = _run_once(
        request["records"],
        **{k: request[k] for k in (
            "threshold", "num_perm", "shingle_size", "scheme",
        )},
    )
    elapsed = time.perf_counter() - started
    if not math.isfinite(elapsed) or elapsed < 0:
        raise RuntimeError("scale timer returned an invalid duration")

    components_by_id = _observed_components(result)
    observations: list[dict[str, Any]] = []
    for index, family in enumerate(request["known_families"]):
        observed = sorted({components_by_id[item] for item in family})
        observations.append({
            "family_index": index,
            "members": list(family),
            "co_clustered": len(observed) == 1,
            "observed_components": [list(component) for component in observed],
        })
    caught = sum(1 for item in observations if item["co_clustered"])
    return {
        "schema": SCHEMA,
        "action": "scale",
        "environment": _environment(),
        "parameters": {
            "scheme": request["scheme"],
            "threshold": request["threshold"],
            "num_perm": request["num_perm"],
            "shingle_size": request["shingle_size"],
        },
        "elapsed_seconds": elapsed,
        "dedup_result": result,
        "layers": {
            "co_cluster": _pair_rows(_co_cluster_pairs(result)),
        },
        "known_families": {
            "count": len(observations),
            "caught": caught,
            "missed": len(observations) - caught,
            "observations": observations,
        },
    }


def evaluate_request(payload: Any) -> dict[str, Any]:
    request = _validated_request(payload)
    if request["action"] == "scale":
        return _evaluate_scale(request)
    return _evaluate_accuracy(request)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = evaluate_request(payload)
        sys.stdout.buffer.write(canonical_json_bytes(result))
        return 0
    except Exception as exc:  # noqa: BLE001 - worker boundary must fail closed
        print(f"datasketch probe failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
