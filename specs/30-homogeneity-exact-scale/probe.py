"""Synthetic design experiment only; NOT a resumable production audit.

Run `python probe.py --scratch <new-local-directory>`. No corpus inputs accepted.
Sort-run generation keeps O(RUN_VALUES) floats; merging keeps FAN_IN input
chunks of RUN_VALUES doubles plus output buffering and FAN_IN heap heads/files.
Worker peak working set includes interpreter/imports; fresh processes isolate sizes.
"""
from __future__ import annotations

import argparse
import array
import ctypes
import heapq
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/setec-voiceprint/scripts"))
import homogeneity_audit as audit

RUN_VALUES = 4096
FAN_IN = 16


def values(path):
    with path.open("rb") as stream:
        while data := stream.read(8 * RUN_VALUES):
            chunk = array.array("d")
            chunk.frombytes(data)
            yield from chunk


def write_values(path, source):
    chunk = array.array("d")
    with path.open("xb") as stream:
        for value in source:
            chunk.append(value)
            if len(chunk) == RUN_VALUES:
                chunk.tofile(stream)
                chunk = array.array("d")
        chunk.tofile(stream)


def external_distribution(source, scratch):
    """Exact ranks and statistics of supplied floats; filenames derived, not retained."""
    scratch.mkdir()  # require a new directory; no reuse or deletion of caller data
    count = runs = 0
    block = []
    for value in source:
        block.append(value)
        count += 1
        if len(block) == RUN_VALUES:
            write_values(scratch / f"0-{runs}.bin", sorted(block))
            runs += 1
            block.clear()
    if block:
        write_values(scratch / f"0-{runs}.bin", sorted(block))
        runs += 1
    if not count:
        raise ValueError("experiment needs nonempty pairs")
    live_bytes = peak_disk = count * 8
    generation = 0
    while runs > 1:
        next_runs = 0
        for first in range(0, runs, FAN_IN):
            paths = [scratch / f"{generation}-{i}.bin"
                     for i in range(first, min(first + FAN_IN, runs))]
            output = scratch / f"{generation + 1}-{next_runs}.bin"
            write_values(output, heapq.merge(*(values(p) for p in paths)))
            live_bytes += output.stat().st_size
            peak_disk = max(peak_disk, live_bytes)
            for path in paths:
                live_bytes -= path.stat().st_size
                path.unlink()  # only scratch files this invocation just created
            next_runs += 1
        generation += 1
        runs = next_runs
    final = scratch / f"{generation}-0.bin"
    ranks = {0}
    for q in (0.1, 0.5, 0.9):
        pos = q * (count - 1)
        ranks.update((int(pos), min(int(pos) + 1, count - 1)))
    selected = {i: value for i, value in enumerate(values(final)) if i in ranks}
    result = {"n": count, "mean": statistics.mean(values(final)),
              "sd": statistics.stdev(values(final)) if count > 1 else 0.0,
              "min": selected[0]}
    for name, q in (("p10", .1), ("p50", .5), ("p90", .9)):
        pos = q * (count - 1)
        lo, hi = int(pos), min(int(pos) + 1, count - 1)
        frac = pos - lo
        result[name] = selected[lo] * (1.0 - frac) + selected[hi] * frac
    return result, peak_disk


def vector(i):
    # Repeats are intentional: occurrences remain distinct pairs.
    return [float(i % 17), float((i * 7) % 23), 1.0]


def pairs(n):
    for i in range(n):
        for j in range(i + 1, n):
            yield audit._cosine(vector(i), vector(j))


def peak_memory_bytes():
    if sys.platform == "win32":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        counter = Counters()
        counter.cb = ctypes.sizeof(counter)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counter), counter.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counter.PeakWorkingSetSize
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def moment_modes(rows):
    import numpy as np
    x = np.asarray(rows, dtype="float64")
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1
    x = x / norms
    x -= x.mean(axis=0, keepdims=True)
    moment = np.zeros((x.shape[1], x.shape[1]), dtype="float64")
    for start in range(0, len(x), 7):
        block = x[start:start + 7]
        moment += block.T @ block
    trace = float(np.trace(moment))
    square = float(np.sum(moment * moment))
    return 1.0 if square <= 0 else max(1.0, min(float(len(x)), trace * trace / square))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--worker", type=int, choices=(512, 2048))
    parser.add_argument("--small-only", action="store_true")
    args = parser.parse_args()
    if args.worker:
        baseline = peak_memory_bytes()
        tracemalloc.start()
        started = time.perf_counter()
        dist, disk = external_distribution(pairs(args.worker), args.scratch)
        _, allocation_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert dist["n"] == args.worker * (args.worker - 1) // 2
        print(json.dumps({"n": args.worker, "distribution": dist,
                          "peak_working_set_bytes": peak_memory_bytes(),
                          "pre_reduction_peak_bytes": baseline,
                          "reduction_traced_peak_bytes": allocation_peak,
                          "peak_live_pair_file_bytes": disk,
                          "elapsed_seconds": time.perf_counter() - started}))
        return
    args.scratch.mkdir(parents=True, exist_ok=False)
    import numpy as np
    cases = {
        "varied": [vector(i) for i in range(31)],
        "multi_pass_merge": [vector(i) for i in range(370)],
        "identical": [[1., 2., 3.]] * 17,
        "zero": [[0., 0., 0.]] * 12,
        "mixed_zero": [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]],
        "orthonormal": np.eye(12).tolist(),
        "rank_one": [[1., 0.], [-1., 0.]] * 7,
        "near_identical": [[1., 1. + i * 1e-14, 1. - i * 1e-14] for i in range(19)],
    }
    evidence = []
    for name, rows in cases.items():
        expected = audit.cosine_distribution(audit.pairwise_cosines(rows))
        actual, _ = external_distribution(iter(audit.pairwise_cosines(rows)), args.scratch / name)
        assert actual == expected, (name, actual, expected)
        old, proposed = audit.effective_modes(rows), moment_modes(rows)
        assert math.isclose(old, proposed, rel_tol=1e-10, abs_tol=1e-10), (name, old, proposed)
        evidence.append({"case": name, "distribution_exact": True,
                         "legacy_modes": old, "moment_modes": proposed,
                         "absolute_difference": abs(old - proposed)})
    if args.small_only:
        print(json.dumps({"small_cases": evidence}, indent=2))
        return
    scales = []
    for n in (512, 2048):
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                               "--scratch", str(args.scratch / f"scale-{n}"),
                               "--worker", str(n)], check=True, capture_output=True, text=True)
        scales.append(json.loads(proc.stdout))
    sensitivity = []
    rng = np.random.default_rng(8)
    for eps in (1e-8, 1e-12, 1e-15, 1e-16, 0.0):
        rows = np.full((97, 11), .25) + rng.normal(size=(97, 11)) * eps
        unit = rows / np.linalg.norm(rows, axis=1, keepdims=True)
        mean = sum((unit[i:i+16].sum(axis=0) for i in range(0, len(unit), 16)), np.zeros(11)) / len(unit)
        centered = unit - mean
        moment = centered.T @ centered
        square = float(np.square(moment).sum())
        proposed = float(np.trace(moment))**2 / square if square else 1.0
        sensitivity.append({"perturbation": eps, "legacy_modes": audit.effective_modes(rows.tolist()),
                            "blocked_mean_modes": max(1.0, min(float(len(rows)), proposed))})
    # Pure Python/array pair reduction: trace allocations after imports so a
    # prior import high-water mark cannot conceal a dense pair-list regression.
    # Also prove the ceiling rejects an actual dense list of this pair count.
    tracemalloc.start()
    dense_control = [float(i) for i in range(2048 * 2047 // 2)]
    _, dense_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del dense_control
    memory_pass = all(s["reduction_traced_peak_bytes"] < 24 * 1024**2 for s in scales)
    memory_pass = memory_pass and dense_peak >= 24 * 1024**2
    print(json.dumps({"python": sys.version.split()[0], "numpy": np.__version__,
                      "run_values": RUN_VALUES, "fan_in": FAN_IN,
                      "small_cases": evidence, "scale": scales,
                      "blocked_mean_sensitivity": sensitivity,
                      "pair_allocation_gate_pass": memory_pass,
                      "dense_control_traced_peak_bytes": dense_peak,
                      "limits": "Pair-reduction experiment only; no parser/vocabulary/resume/representatives acceptance."}, indent=2))
    assert memory_pass, "pair-reduction peak grew by at least 24 MiB"


if __name__ == "__main__":
    main()
