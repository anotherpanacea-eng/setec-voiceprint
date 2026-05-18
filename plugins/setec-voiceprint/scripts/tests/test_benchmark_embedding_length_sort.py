"""Tests for the embedding length-sort benchmark script
(``calibration/_benchmark_embedding_length_sort.py``).

The script's job is informational — feed a synthetic
heterogeneous-length corpus to ``EmbeddingBackend.encode()`` in
three orderings and report wall-clock. These tests pin the
non-timing parts of the script (corpus shape, ordering helpers,
markdown summary) so a refactor doesn't silently break the
benchmark's contract without us noticing. The actual timing call
is exercised against a stubbed backend that records what it was
given — no real sentence-transformers needed.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "calibration"))

import _benchmark_embedding_length_sort as bench  # noqa: E402


def test_build_corpus_returns_requested_count():
    """Sanity: ``--n 100`` produces exactly 100 texts."""
    texts = bench._build_corpus(n=100, short_len=80, long_len=1200)
    assert len(texts) == 100


def test_build_corpus_alternates_short_and_long():
    """Even indices are short-target, odd indices long-target. The
    interleaving is what makes a naive same-order batcher waste
    pad positions; pin it so a refactor doesn't accidentally
    cluster the long texts together (which would defeat the
    benchmark's purpose)."""
    texts = bench._build_corpus(
        n=20, short_len=80, long_len=1200, seed=42,
    )
    for i in range(0, 20, 2):
        # Short texts are targeted at ~80 chars. The generator
        # appends words until it hits the target, so actual char
        # counts overshoot slightly (~80-120 chars).
        assert len(texts[i]) < 200, (
            f"index {i} should be a short text; got {len(texts[i])} chars"
        )
    for i in range(1, 20, 2):
        # Long texts targeted at ~1200 chars; expect at least 1000.
        assert len(texts[i]) >= 1000, (
            f"index {i} should be a long text; got {len(texts[i])} chars"
        )


def test_build_corpus_is_deterministic_under_same_seed():
    """Same seed → same corpus. Pins reproducibility so two
    benchmark runs against the same backend can be compared
    directly."""
    a = bench._build_corpus(n=30, short_len=80, long_len=1200, seed=42)
    b = bench._build_corpus(n=30, short_len=80, long_len=1200, seed=42)
    assert a == b


def test_orderings_produce_three_keys():
    """The three orderings the benchmark times: ``shuffled``,
    ``pre_sorted``, ``adversarial``. Missing one means the
    interpretation section's `for label in ("pre_sorted",
    "adversarial")` loop would crash or skip a row."""
    out = bench._orderings(["short", "longer text", "x"])
    assert set(out.keys()) == {"shuffled", "pre_sorted", "adversarial"}


def test_orderings_pre_sorted_is_ascending_by_length():
    """``pre_sorted`` ordering presents shortest text first. Pins
    the "what the benchmark calls 'pre_sorted'" semantics."""
    texts = ["aaaaa", "a", "aaa", "aaaa", "aa"]
    out = bench._orderings(texts)
    lengths = [len(t) for t in out["pre_sorted"]]
    assert lengths == sorted(lengths)


def test_orderings_adversarial_is_descending_by_length():
    """``adversarial`` ordering presents longest text first. This
    is the worst-case for a naive batcher: batch 1 pads the
    *globally longest* text alongside near-equal-length peers
    (no benefit), and the structure-vs-length correlation makes
    any wrapper-level pre-sort that doesn't ALSO de-sort the
    output look like it's mangling the input order."""
    texts = ["aaaaa", "a", "aaa", "aaaa", "aa"]
    out = bench._orderings(texts)
    lengths = [len(t) for t in out["adversarial"]]
    assert lengths == sorted(lengths, reverse=True)


def test_orderings_preserve_input_set():
    """All three orderings contain the same multiset of strings
    — they only differ in order, not in content."""
    texts = ["aaaaa", "a", "aaa", "aaaa", "aa"]
    out = bench._orderings(texts)
    for label in ("shuffled", "pre_sorted", "adversarial"):
        assert sorted(out[label]) == sorted(texts), (
            f"ordering {label!r} dropped or duplicated a text"
        )


def test_percent_diff_signed():
    """Positive when ``b > a``, negative when ``b < a``. Used in
    the interpretation section to say "pre_sorted is X% faster /
    slower than shuffled"."""
    assert bench._percent_diff(1.0, 1.10) == pytest.approx(10.0)
    assert bench._percent_diff(1.0, 0.90) == pytest.approx(-10.0)
    assert bench._percent_diff(2.0, 2.0) == 0.0


def test_percent_diff_zero_baseline_returns_zero():
    """Defensive: division-by-zero on a zero baseline returns 0
    rather than raising. Should never happen in practice (encode
    times are always > 0), but pinning the edge case makes the
    helper safe to use anywhere."""
    assert bench._percent_diff(0.0, 5.0) == 0.0


# ---------- Integration with a stubbed backend ----------


class _StubBackend:
    """Records the orderings (and batch_sizes) it was called with,
    so the test can pin what the benchmark actually fed to the
    real ``EmbeddingBackend``. Returns a zero ndarray-like dummy
    so the timing call completes without numpy installed."""

    def __init__(self):
        self.calls: list[dict] = []

    def encode(self, texts, *, batch_size=32):
        self.calls.append({
            "lengths": [len(t) for t in texts],
            "batch_size": batch_size,
        })
        # The benchmark doesn't inspect the return value, only times
        # the call. Returning None would propagate to a hypothetical
        # caller; in practice ``run_benchmark`` discards it.
        return None


def test_run_benchmark_calls_backend_three_times_per_repeat(
    monkeypatch: pytest.MonkeyPatch,
):
    """One ``encode`` per ordering, repeated ``repeats`` times.
    With ``repeats=2``: 3 per-ordering warmups + 3 orderings * 2
    repeats = 9 total calls. Pins the loop structure so a refactor
    that accidentally drops an ordering surfaces here.

    Pre-PR-#102-followup, warmup was a single 8-text call (7
    total). The reviewer P2 fix replaced it with per-ordering
    full-corpus warmup so first-batch allocator effects don't
    bias the first ordering of the first repeat -- the steady-
    state behavior the benchmark cares about gets measured."""
    stub = _StubBackend()

    class _FakeBackendModule:
        EmbeddingBackend = lambda self=None, **kw: stub  # noqa: E731

    # Insert the stub before run_benchmark imports embedding_backend.
    monkeypatch.setitem(
        sys.modules, "embedding_backend", _FakeBackendModule(),
    )
    sink = io.StringIO()
    bench.run_benchmark(
        model_alias="minilm",
        n=20, batch_size=8, repeats=2,
        short_len=80, long_len=400,
        out_stream=sink,
    )
    # 3 per-ordering warmups + 3 orderings * 2 repeats = 9.
    assert len(stub.calls) == 9


def test_run_benchmark_writes_markdown_summary(
    monkeypatch: pytest.MonkeyPatch,
):
    """The output stream gets a Markdown table + interpretation
    section. Operators redirect stdout to a file and review the
    result; missing the summary defeats the script's purpose."""
    stub = _StubBackend()

    class _FakeBackendModule:
        EmbeddingBackend = lambda self=None, **kw: stub  # noqa: E731

    monkeypatch.setitem(
        sys.modules, "embedding_backend", _FakeBackendModule(),
    )
    sink = io.StringIO()
    bench.run_benchmark(
        model_alias="minilm",
        n=10, batch_size=8, repeats=2,
        short_len=80, long_len=400,
        out_stream=sink,
    )
    out = sink.getvalue()
    assert "## Results" in out
    assert "| ordering | mean | stdev | median |" in out
    assert "| `shuffled` |" in out
    assert "| `pre_sorted` |" in out
    assert "| `adversarial` |" in out
    assert "## Interpretation" in out


def test_run_benchmark_returns_summary_dict(
    monkeypatch: pytest.MonkeyPatch,
):
    """``run_benchmark`` returns the per-ordering timing summary
    as a dict so test harnesses / wrapper scripts can consume the
    numbers programmatically without parsing the Markdown output."""
    stub = _StubBackend()

    class _FakeBackendModule:
        EmbeddingBackend = lambda self=None, **kw: stub  # noqa: E731

    monkeypatch.setitem(
        sys.modules, "embedding_backend", _FakeBackendModule(),
    )
    sink = io.StringIO()
    summary = bench.run_benchmark(
        model_alias="minilm",
        n=10, batch_size=8, repeats=3,
        short_len=80, long_len=400,
        out_stream=sink,
    )
    assert set(summary.keys()) == {"shuffled", "pre_sorted", "adversarial"}
    for label, stats in summary.items():
        assert {"mean", "stdev", "median"} <= set(stats.keys()), (
            f"summary[{label!r}] missing keys"
        )


def test_warmup_runs_each_ordering_with_full_corpus(
    monkeypatch: pytest.MonkeyPatch,
):
    """Reviewer P2 on PR #102: warmup must hit every ordering with
    the full corpus before the timed loop, so first-batch allocator
    / cache / thermal effects don't bias whichever ordering happens
    to run first. Pin the warmup contract: the first three encode
    calls (the warmups) each carry the full corpus length, not a
    truncated 8-text sample like the pre-fix single warmup did."""
    stub = _StubBackend()

    class _FakeBackendModule:
        EmbeddingBackend = lambda self=None, **kw: stub  # noqa: E731

    monkeypatch.setitem(
        sys.modules, "embedding_backend", _FakeBackendModule(),
    )
    sink = io.StringIO()
    bench.run_benchmark(
        model_alias="minilm",
        n=12, batch_size=4, repeats=1,
        short_len=80, long_len=400,
        out_stream=sink,
    )
    # First 3 calls are the per-ordering warmups. Each must hit
    # exactly n=12 texts (full corpus). The post-fix warmup runs
    # the full corpus through each ordering; the pre-fix warmup
    # ran an 8-text slice through only ``texts[:8]``.
    warmup_calls = stub.calls[:3]
    for call in warmup_calls:
        assert len(call["lengths"]) == 12, (
            f"warmup call had {len(call['lengths'])} texts; expected "
            f"full corpus of 12"
        )


def test_per_repeat_ordering_is_randomized(monkeypatch: pytest.MonkeyPatch):
    """Reviewer P2 on PR #102: per-repeat ordering of the three
    orderings must be randomized so thermal drift over the repeat
    sequence affects each ordering equally on average. With
    ``repeats=4`` and a seeded shuffler, at least one repeat must
    pick a non-default order -- otherwise the script is back to
    the bias-prone fixed sequence the reviewer flagged.

    The stub records the corpus length signature of each call (12
    chars across the four lengths in the synthetic corpus); we
    use the *exact lengths list* to identify which ordering each
    call was."""
    stub = _StubBackend()

    class _FakeBackendModule:
        EmbeddingBackend = lambda self=None, **kw: stub  # noqa: E731

    monkeypatch.setitem(
        sys.modules, "embedding_backend", _FakeBackendModule(),
    )
    sink = io.StringIO()
    bench.run_benchmark(
        model_alias="minilm",
        n=20, batch_size=8, repeats=4,
        short_len=80, long_len=400,
        out_stream=sink,
    )

    # The three orderings produce distinct lengths-sequences:
    # - pre_sorted: ascending
    # - adversarial: descending
    # - shuffled: neither (random)
    def classify(lengths):
        if lengths == sorted(lengths):
            return "pre_sorted"
        if lengths == sorted(lengths, reverse=True):
            return "adversarial"
        return "shuffled"

    # Drop the 3 warmups; group the timed calls into per-repeat
    # triplets in arrival order.
    timed_calls = stub.calls[3:]
    assert len(timed_calls) == 12  # 3 orderings * 4 repeats

    per_repeat_orderings = []
    for i in range(4):
        triplet = timed_calls[i * 3:(i + 1) * 3]
        per_repeat_orderings.append(
            tuple(classify(call["lengths"]) for call in triplet)
        )

    # At least two distinct orderings across the 4 repeats -- if
    # all four matched the same fixed sequence, randomization
    # isn't happening and the reviewer's bias concern stands.
    distinct = set(per_repeat_orderings)
    assert len(distinct) >= 2, (
        f"per-repeat ordering didn't vary across 4 repeats; got "
        f"{per_repeat_orderings} -- randomization broken, "
        f"benchmark may bias toward the first ordering"
    )


# ---------- CLI entry point ----------


def test_cli_help_does_not_crash(capsys: pytest.CaptureFixture):
    """``--help`` works without sentence-transformers installed.
    Operators on a slim install should be able to read the
    benchmark's docstring before deciding whether to install
    the heavy deps."""
    with pytest.raises(SystemExit) as excinfo:
        bench.main(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "Empirical benchmark" in out
    assert "--model" in out
