#!/usr/bin/env python3
"""Offline controller for the Voiceprint #396 dependency evaluation.

The controller contains no third-party imports. Version-specific packages live
in isolated workers and communicate through canonical JSON. Stable semantic
results deliberately exclude timing, absolute paths, and wall-clock timestamps.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import re
import random
import subprocess
import sys
import unicodedata
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "setec-dependency-evaluation/1"
ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
MANIFEST = FIXTURES / "manifest.json"
REPO_ROOT = ROOT.parents[2]
SCRIPTS = REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import near_dup_dedup as ndd  # noqa: E402


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize the stable protocol: finite JSON, sorted keys, LF."""
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_fixture_manifest() -> dict[str, Any]:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if payload.get("schema") != "setec-dependency-fixtures/1":
        raise ValueError("fixture manifest schema mismatch")
    for row in payload.get("files", []):
        rel = row.get("path")
        expected = row.get("sha256")
        if not isinstance(rel, str) or not isinstance(expected, str):
            raise ValueError("fixture row requires path and sha256")
        path = FIXTURES / rel
        if not path.is_file():
            raise ValueError(f"missing fixture: {rel}")
        actual = sha256(path)
        if actual != expected:
            raise ValueError(
                f"fixture hash mismatch for {rel}: expected {expected}, got {actual}"
            )
        license_id = row.get("license")
        if not isinstance(license_id, str) or not license_id.strip():
            raise ValueError(f"fixture {rel} lacks a redistribution license")
        if not row.get("license_evidence_url"):
            raise ValueError(f"fixture {rel} lacks license evidence")
    return payload


def normalize_marker_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return re.sub(r"\s+", " ", text).strip()


def marker_score(text: str, markers: Iterable[str]) -> dict[str, Any]:
    normalized = normalize_marker_text(text)
    marker_list = list(markers)
    if not marker_list:
        raise ValueError("score-bearing fixture requires nonempty marker list")
    found = [m for m in marker_list if normalize_marker_text(m) in normalized]
    return {
        "found": sorted(found),
        "found_count": len(found),
        "total": len(marker_list),
        "ratio": len(found) / len(marker_list),
    }


def tokens(text: str) -> list[str]:
    """Return the production tokenizer's exact lowercased word sequence."""
    return [token.lower() for token in ndd._WORD_RE.findall(text)]


def shingles(text: str, k: int = 5) -> set[str]:
    """Call the repository's shipped document-mode shingle implementation."""
    return ndd.shingles(text, k=k)


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def public_fixture_documents() -> dict[str, str]:
    """Load the six hash-bound, score-bearing redistributed HTML fixtures."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    documents: dict[str, str] = {}
    for row in manifest["files"]:
        if row.get("kind") != "html" or not row.get("score_bearing"):
            continue
        parser = _VisibleText()
        parser.feed((FIXTURES / row["path"]).read_text(encoding="utf-8"))
        text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
        if not text:
            raise ValueError(f"public fixture has no visible text: {row['path']}")
        documents[row["path"]] = text
    if len(documents) != 6:
        raise ValueError(f"expected 6 public fixture documents, got {len(documents)}")
    return documents


def jaccard(left: set[Any], right: set[Any]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


Pair = tuple[str, str]


def pair(a: str, b: str) -> Pair:
    return (a, b) if a < b else (b, a)


def exact_pair_oracle(
    records: dict[str, str], *, threshold: float = 0.85, k: int = 5,
) -> tuple[set[Pair], dict[Pair, float]]:
    sets = {rid: shingles(text, k=k) for rid, text in records.items()}
    positives: set[Pair] = set()
    scores: dict[Pair, float] = {}
    for left, right in itertools.combinations(sorted(records), 2):
        key = (left, right)
        score = jaccard(sets[left], sets[right])
        scores[key] = score
        if score >= threshold:
            positives.add(key)
    return positives, scores


def components(ids: Iterable[str], edges: Iterable[Pair]) -> list[list[str]]:
    parent = {rid: rid for rid in ids}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for left, right in edges:
        a, b = find(left), find(right)
        if a != b:
            parent[max(a, b)] = min(a, b)
    grouped: dict[str, list[str]] = defaultdict(list)
    for rid in sorted(parent):
        grouped[find(rid)].append(rid)
    return sorted((sorted(group) for group in grouped.values()), key=lambda x: x[0])


def component_pairs(groups: Iterable[Iterable[str]]) -> set[Pair]:
    out: set[Pair] = set()
    for group in groups:
        out.update(pair(a, b) for a, b in itertools.combinations(sorted(group), 2))
    return out


def representative(group: Iterable[str], records: dict[str, str]) -> str:
    return min(group, key=lambda rid: (-len(records[rid]), rid))


def oracle_keep_drop(records: dict[str, str], edges: set[Pair]) -> dict[str, Any]:
    groups = components(records, edges)
    kept: list[str] = []
    dropped: list[str] = []
    clusters: dict[str, list[str]] = {}
    for group in groups:
        rep = representative(group, records)
        kept.append(rep)
        others = sorted(rid for rid in group if rid != rep)
        if others:
            clusters[rep] = others
            dropped.extend(others)
    order = {rid: index for index, rid in enumerate(records)}
    return {
        "kept": sorted(kept, key=order.get),
        "dropped": sorted(dropped, key=order.get),
        "clusters": {key: clusters[key] for key in sorted(clusters)},
    }


def layer_metrics(predicted: set[Pair], truth: set[Pair]) -> dict[str, Any]:
    tp = predicted & truth
    fp = predicted - truth
    fn = truth - predicted
    precision = len(tp) / len(predicted) if predicted else (1.0 if not truth else 0.0)
    recall = len(tp) / len(truth) if truth else 1.0
    return {
        "tp": [list(x) for x in sorted(tp)],
        "fp": [list(x) for x in sorted(fp)],
        "fn": [list(x) for x in sorted(fn)],
        "precision": precision,
        "recall": recall,
    }


def evaluate_worker_result(
    records: dict[str, str], worker: dict[str, Any], *, threshold: float = 0.85,
) -> dict[str, Any]:
    truth, scores = exact_pair_oracle(records, threshold=threshold)
    oracle_groups = components(records, truth)
    oracle_drop = oracle_keep_drop(records, truth)
    layers = {
        name: {pair(*row) for row in worker["layers"][name]}
        for name in ("raw_lsh", "estimated_pass", "co_cluster")
    }
    result = {
        "layers": {name: layer_metrics(edges, truth) for name, edges in layers.items()},
        "oracle": {
            "positive_pairs": [list(x) for x in sorted(truth)],
            "components": oracle_groups,
            "keep_drop": oracle_drop,
            "pair_count": len(scores),
        },
        "observed_keep_drop": worker["dedup_result"],
        "keep_drop_matches_oracle": all(
            worker["dedup_result"][key] == oracle_drop[key]
            for key in ("kept", "dropped", "clusters")
        ),
        "traced_matches_control": worker["traced_matches_control"],
    }
    return result


def _unique_tokens(prefix: str, count: int = 1000) -> list[str]:
    return [f"{prefix}word{index}" for index in range(count)]


def _mutate_tail(words: list[str], count: int, prefix: str) -> str:
    changed = list(words)
    for index in range(count):
        changed[-1 - index] = f"{prefix}mutation{index}"
    return " ".join(changed)


def _near_pair(prefix: str, *, above: bool) -> tuple[str, str]:
    base = _unique_tokens(prefix)
    base_text = " ".join(base)
    chosen: tuple[float, str] | None = None
    for count in range(1, 180):
        candidate = _mutate_tail(base, count, prefix)
        score = jaccard(shingles(base_text), shingles(candidate))
        if (above and 0.85 <= score < 0.88) or (not above and 0.82 < score < 0.85):
            chosen = (score, candidate)
            break
    if chosen is None:
        raise RuntimeError("could not construct near-threshold pair")
    return base_text, chosen[1]


def accuracy_records() -> dict[str, str]:
    """Build the exhaustive 200-document deterministic accuracy corpus."""
    records: dict[str, str] = {}
    for family in range(10):
        text = " ".join(_unique_tokens(f"exact{family}"))
        records[f"exact-{family:02d}-a"] = text
        records[f"exact-{family:02d}-b"] = text
    for family in range(10):
        words = _unique_tokens(f"truncated{family}")
        records[f"truncated-{family:02d}-a"] = " ".join(words)
        records[f"truncated-{family:02d}-b"] = " ".join(words[:950])
    for family in range(10):
        words = _unique_tokens(f"boilerplate{family}")
        records[f"boilerplate-{family:02d}-a"] = " ".join(words)
        prefix = _unique_tokens(f"chrome{family}", 50)
        records[f"boilerplate-{family:02d}-b"] = " ".join(prefix + words)
    for family in range(10):
        words = _unique_tokens(f"reordered{family}")
        passages = [words[index:index + 200] for index in range(0, 1000, 200)]
        records[f"reordered-{family:02d}-a"] = " ".join(words)
        records[f"reordered-{family:02d}-b"] = " ".join(
            token for index in (1, 0, 2, 4, 3) for token in passages[index]
        )
    for family in range(10):
        a, b = _near_pair(f"above{family}", above=True)
        records[f"above-{family:02d}-a"] = a
        records[f"above-{family:02d}-b"] = b
    for family in range(10):
        a, b = _near_pair(f"below{family}", above=False)
        records[f"below-{family:02d}-a"] = a
        records[f"below-{family:02d}-b"] = b
    for family in range(10):
        base = _unique_tokens(f"chain{family}")
        middle = list(base)
        end = list(base)
        for index in range(50):
            bridge = f"chainbridge{family}mutation{index}"
            middle[-1 - index] = bridge
            end[-1 - index] = bridge
        for index in range(50, 100):
            end[-1 - index] = f"chainend{family}mutation{index}"
        records[f"chain-{family:02d}-a"] = " ".join(base)
        records[f"chain-{family:02d}-b"] = " ".join(middle)
        records[f"chain-{family:02d}-c"] = " ".join(end)
    public_documents = list(public_fixture_documents().items())
    for index, (path, text) in enumerate(public_documents):
        records[f"public-{index:02d}-{Path(path).stem}"] = text
    for index in range(50 - len(public_documents)):
        records[f"control-{index:02d}"] = " ".join(_unique_tokens(f"control{index}"))
    if len(records) != 200:
        raise AssertionError(f"accuracy corpus size drift: {len(records)}")
    return records


def run_worker(python: Path, worker: Path, request: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPTS)
    completed = subprocess.run(
        [str(python), str(worker)],
        input=canonical_json_bytes(request),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env=env,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"worker failed rc={completed.returncode}: "
            f"{completed.stderr.decode('utf-8', 'replace')}"
        )
    return json.loads(completed.stdout)


def run_worker_measured(
    python: Path, worker: Path, request: dict[str, Any], *, timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one scale worker under macOS time; keep measurements non-stable."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPTS)
    completed = subprocess.run(
        ["/usr/bin/time", "-l", str(python), str(worker)],
        input=canonical_json_bytes(request),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env=env,
    )
    stderr = completed.stderr.decode("utf-8", "replace")
    if completed.returncode != 0:
        raise RuntimeError(f"measured worker failed rc={completed.returncode}: {stderr}")
    rss_match = re.search(r"(\d+)\s+maximum resident set size", stderr)
    raw = json.loads(completed.stdout)
    timing = {
        "worker_elapsed_seconds": raw.pop("elapsed_seconds", None),
        "maximum_resident_bytes": int(rss_match.group(1)) if rss_match else None,
    }
    return raw, timing


def _env_python(name: str) -> Path:
    return ROOT / ".venvs" / name / "bin" / "python"


def expected_datasketch_environment(name: str) -> dict[str, Any]:
    return {
        "python": "3.13.7",
        "implementation": "CPython",
        "datasketch": "1.6.5" if name == "datasketch-165" else "2.0.0",
        "numpy": "2.5.2",
        "scipy": "1.18.0",
        "rapidfuzz": None if name == "datasketch-165" else "3.14.5",
    }


def evaluate_fallback() -> dict[str, Any]:
    """Exercise the no-datasketch import and passage fallback under ``-S``."""
    env = {"PYTHONPATH": str(SCRIPTS), "PATH": os.environ.get("PATH", "")}
    completed = subprocess.run(
        [sys.executable, "-S", str(ROOT / "workers" / "fallback_probe.py")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "fallback probe failed: "
            + completed.stderr.decode("utf-8", "replace")
        )
    result = json.loads(completed.stdout)
    expected = "datasketch is not installed"
    if not (
        result.get("base_import")
        and expected in str(result.get("document_mode_error"))
        and expected in str(result.get("stage_a_error"))
        and result.get("stage_b_available") is True
    ):
        raise RuntimeError(f"fallback contract drift: {result!r}")
    return result


def evaluate_trafilatura(manifest: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    request = {
        "schema": SCHEMA,
        "action": "trafilatura",
        "expected_environment": {"python": "3.13.7", "trafilatura": "2.1.0"},
        "fixture_root": str(FIXTURES),
        "fixtures": manifest["files"],
        "reruns": 5,
    }
    raw = run_worker(
        _env_python("trafilatura-210"),
        ROOT / "workers" / "trafilatura_probe.py",
        request,
        timeout=120,
    )
    performance = raw.pop("performance")
    aggregate = raw["semantic"]["html_aggregate"]
    fallback = aggregate["paths"]["fallback"]
    full = aggregate["paths"]["full_seam"]
    semantic_gates = {
        "deterministic": aggregate["all_reruns_deterministic"],
        "primary_hits_every_structure_class": all(
            aggregate["primary_success_by_structure_class"].values()
        ),
        "recall_within_0_05": full["macro_recall"] >= fallback["macro_recall"] - 0.05,
        "leakage_within_0_05": full["macro_leakage"] <= fallback["macro_leakage"] + 0.05,
        "no_fixture_lost_all_required": not aggregate["full_seam_lost_all_required"],
        "titles_do_not_regress": (
            full["title_retained_count"] >= fallback["title_retained_count"]
        ),
        "failures_fail_soft": aggregate["failure_cases_fail_soft"],
    }
    raw["semantic_gates"] = semantic_gates
    raw["semantic_recommendation"] = (
        "keep_optional" if all(semantic_gates.values()) else "reject"
    )
    return raw, performance


def scale_records(total: int, duplicate_families: int) -> tuple[dict[str, str], set[Pair]]:
    if total <= 0 or duplicate_families <= 0 or duplicate_families * 2 > total:
        raise ValueError("invalid scale parameters")
    records: dict[str, str] = {}
    known: set[Pair] = set()
    public_words = [tokens(text) for text in public_fixture_documents().values()]

    def mixed_public_text(label: str, index: int) -> str:
        source = public_words[index % len(public_words)]
        words: list[str] = []
        for position in range(500):
            words.append(source[position % len(source)])
            words.append(f"{label}word{position}")
        return " ".join(words)

    for index in range(duplicate_families):
        text = mixed_public_text(f"scaledup{index}", index)
        left = f"duplicate-{index:04d}-a"
        right = f"duplicate-{index:04d}-b"
        records[left] = text
        records[right] = text
        known.add((left, right))
    for index in range(total - 2 * duplicate_families):
        records[f"unrelated-{index:05d}"] = mixed_public_text(
            f"scalecontrol{index}", index,
        )
    if len(records) != total or any(len(tokens(text)) != 1000 for text in records.values()):
        raise AssertionError("scale corpus shape drift")
    return records, known


def sampled_negative_pairs(
    ids: list[str], known: set[Pair], *, count: int = 10_000,
) -> set[Pair]:
    rng = random.Random(396)
    negatives: set[Pair] = set()
    while len(negatives) < count:
        left, right = rng.sample(ids, 2)
        candidate = pair(left, right)
        if candidate not in known:
            negatives.add(candidate)
    return negatives


def evaluate_scale(worker: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    stable: dict[str, Any] = {}
    timing: dict[str, Any] = {}
    for rung, total, families, repeats, timeout in (
        ("small", 1_000, 50, 3, 120),
        ("large", 5_000, 250, 1, 600),
    ):
        records, known = scale_records(total, families)
        negatives = sampled_negative_pairs(list(records), known)
        for name, scheme in (
            ("datasketch-165", "default"),
            ("datasketch-200", "default"),
            ("datasketch-200", "legacy"),
        ):
            key = name if scheme == "default" else f"{name}-{scheme}"
            outcomes: list[dict[str, Any]] = []
            measurements: list[dict[str, Any]] = []
            for _ in range(repeats):
                request = {
                    "schema": SCHEMA,
                    "action": "scale",
                    "expected_environment": expected_datasketch_environment(name),
                    "records": [[rid, text] for rid, text in records.items()],
                    "threshold": 0.85,
                    "num_perm": 128,
                    "shingle_size": 5,
                    "scheme": scheme,
                }
                try:
                    raw, measured = run_worker_measured(
                        _env_python(name), worker, request, timeout=timeout,
                    )
                except subprocess.TimeoutExpired:
                    outcomes.append({"measured_failure": "timeout"})
                    measurements.append({"timeout_seconds": timeout})
                    break
                co_cluster = {pair(*row) for row in raw["layers"]["co_cluster"]}
                resident = measured["maximum_resident_bytes"]
                outcomes.append({
                    "dedup_result": raw["dedup_result"],
                    "known_family_recall": len(co_cluster & known) / len(known),
                    "memory_ceiling_pass": resident is not None and resident <= 4 * 1024**3,
                    "sampled_negative_false_positives": [
                        list(item) for item in sorted(co_cluster & negatives)
                    ],
                })
                measurements.append(measured)
            first = canonical_json_bytes(outcomes[0])
            stable.setdefault(rung, {})[key] = {
                "document_count": total,
                "known_duplicate_families": families,
                "sampled_negative_pairs": len(negatives),
                "deterministic_across_repeats": all(
                    canonical_json_bytes(outcome) == first for outcome in outcomes
                ),
                "semantics": outcomes[0],
            }
            timing.setdefault(rung, {})[key] = measurements
    return stable, timing


def evaluate(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = load_fixture_manifest()
    trafilatura, trafilatura_timing = evaluate_trafilatura(manifest)
    records = accuracy_records()
    worker = ROOT / "workers" / "datasketch_probe.py"
    request = {
        "schema": SCHEMA,
        "action": "accuracy",
        "records": [[rid, text] for rid, text in records.items()],
        "threshold": 0.85,
        "num_perm": 128,
        "shingle_size": 5,
    }
    tracks: dict[str, Any] = {}
    accuracy_timing: dict[str, Any] = {}
    for name, scheme in (
        ("datasketch-165", "default"),
        ("datasketch-200", "default"),
        ("datasketch-200", "legacy"),
    ):
        key = name if scheme == "default" else f"{name}-{scheme}"
        req = dict(
            request,
            scheme=scheme,
            expected_environment=expected_datasketch_environment(name),
        )
        runs = [run_worker(_env_python(name), worker, req, timeout=120) for _ in range(5)]
        run_timings: list[Any] = []
        for raw_run in runs:
            rapidfuzz = raw_run.get("rapidfuzz")
            if isinstance(rapidfuzz, dict):
                run_timings.append(rapidfuzz.pop("timing", None))
        raw = runs[0]
        deterministic = all(
            canonical_json_bytes(candidate) == canonical_json_bytes(raw)
            for candidate in runs[1:]
        )
        accuracy_timing[key] = run_timings
        tracks[key] = {
            "environment": raw["environment"],
            "signature": raw["signature"],
            "rapidfuzz": raw["rapidfuzz"],
            "evaluation": evaluate_worker_result(records, raw),
            "deterministic_across_five_runs": deterministic,
        }
    scale, timing = evaluate_scale(worker)
    timing["accuracy_scorers"] = accuracy_timing
    timing["trafilatura"] = trafilatura_timing
    signatures = {
        key: value["signature"]["aggregate_sha256"] for key, value in tracks.items()
    }
    keep_drop = {
        key: value["evaluation"]["observed_keep_drop"] for key, value in tracks.items()
    }
    destructive_gate = all(
        value["deterministic_across_five_runs"]
        and value["evaluation"]["keep_drop_matches_oracle"]
        and not value["evaluation"]["layers"]["co_cluster"]["fn"]
        for value in tracks.values()
    )
    rapidfuzz_200 = tracks["datasketch-200"]["rapidfuzz"]
    if rapidfuzz_200.get("available"):
        raw_metrics = tracks["datasketch-200"]["evaluation"]["layers"]["raw_lsh"]
        rapid_primary = rapidfuzz_200["cutoffs"]["85"]["global_oracle_metrics"]
        rapid_semantic_gate = (
            rapid_primary["precision"] >= raw_metrics["precision"] + 0.10
            and not rapid_primary["fn"]
        )
    else:
        rapid_semantic_gate = False
    stable = {
        "schema": SCHEMA,
        "fixture_manifest_sha256": sha256(MANIFEST),
        "fixture_count": len(manifest["files"]),
        "accuracy_document_count": len(records),
        "accuracy_public_fixture_documents": len(public_fixture_documents()),
        "no_datasketch_fallback": evaluate_fallback(),
        "compatibility": {
            "signature_165_equals_200_default": (
                signatures["datasketch-165"] == signatures["datasketch-200"]
            ),
            "signature_165_equals_200_legacy": (
                signatures["datasketch-165"] == signatures["datasketch-200-legacy"]
            ),
            "keep_drop_165_equals_200_default": (
                keep_drop["datasketch-165"] == keep_drop["datasketch-200"]
            ),
            "keep_drop_165_equals_200_legacy": (
                keep_drop["datasketch-165"] == keep_drop["datasketch-200-legacy"]
            ),
            "persisted_index_policy": "rebuild_or_refuse_unproven_version_scheme_boundary",
        },
        "trafilatura": trafilatura,
        "scale": scale,
        "tracks": tracks,
        "recommendations": {
            "datasketch_document_seam": (
                "keep_optional_destructive" if destructive_gate
                else "reject_destructive_retain_candidate_only_behind_exact_confirmation"
            ),
            "rapidfuzz_semantic_gate": rapid_semantic_gate,
            "rapidfuzz": (
                "eligible_pending_runtime_gate" if rapid_semantic_gate
                else "reject_no_added_dependency"
            ),
        },
    }
    return stable, timing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "results.json")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify-fixtures", action="store_true")
    args = parser.parse_args(argv)
    if args.verify_fixtures:
        load_fixture_manifest()
        return 0
    stable, timing = evaluate(args)
    rendered = canonical_json_bytes(stable)
    if args.check:
        if not args.out.is_file() or args.out.read_bytes() != rendered:
            print("results.json is stale", file=sys.stderr)
            return 1
        return 0
    args.out.write_bytes(rendered)
    (ROOT / "timing.json").write_bytes(canonical_json_bytes(timing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
