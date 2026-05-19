"""Phase A prompt builder for External Mirror Discrimination.

Takes a target text + parameters and emits ready-to-paste prompts the
operator can carry to any chatbot interface. Supports five window-
positioning strategies, including the expanding-context regime (Design 4)
that produced the methodology's strongest discrimination signal on the
Granta validation target.

Implements SPEC_external_mirror_prompt_builder.md v0.2. Phase B
(operator pastes outputs back, distance computation, evidence pack
composition) is a separate module not implemented here.

CLI:
    python3 build_prompts.py TARGET.txt [options]

Options:
    --windows K              number of windows (default 4)
    --context M              words of preceding context per window (default 500)
    --continuation N         target words for continuation (default 150)
    --positioning STRATEGY   equal | equal_skipping_opening (default)
                             | stratified | custom | expanding
    --positions LIST         comma-separated word indices (custom only)
    --context-grid LIST      comma-separated context sizes (expanding only)
    --out DIR                output dir (default ./prompts/)
    --format FMT             separate | batched | both (default both)
    --genre-descriptor STR   genre label injected into prompts
                             (default "literary prose")
    --run-id STR             explicit run id (default mirror_YYYYMMDD_HHMMSS)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# Tokenizer
# ============================================================

_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class Token:
    """Whitespace-delimited token with byte-offsets into the source text."""
    start: int
    end: int


def tokenize(text: str) -> list[Token]:
    """Return whitespace-delimited tokens preserving original byte-offsets.

    Differs from variance_audit.split_words (which uses ``[A-Za-z']+``
    lowercased). We need to slice the ORIGINAL text by word indices
    without losing punctuation, capitalization, or paragraph structure,
    because the chatbot reading the prompt must see the target as the
    original reader saw it.
    """
    return [Token(m.start(), m.end()) for m in _WORD_RE.finditer(text)]


def normalize_text(text: str) -> str:
    """Hygiene step per spec: normalize line endings, collapse triple-newlines,
    strip trailing whitespace per line. Nothing else.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text


def slice_words(text: str, tokens: list[Token], start_word: int, end_word: int) -> str:
    """Return the substring of ``text`` containing words ``[start_word:end_word]``.

    Preserves all original whitespace and punctuation between those tokens.
    """
    if start_word < 0 or end_word > len(tokens) or start_word >= end_word:
        raise ValueError(
            f"slice range [{start_word}:{end_word}] invalid for {len(tokens)} tokens"
        )
    return text[tokens[start_word].start : tokens[end_word - 1].end]


# ============================================================
# Window specifications
# ============================================================


@dataclass
class WindowSpec:
    window_index: int
    context_start_word: int
    context_end_word: int
    continuation_start_word: int
    continuation_end_word: int
    context_word_count: int
    context_sha256: str = ""  # filled after slicing


def _positions_equal(n_words: int, k: int) -> list[int]:
    """K continuation-start positions equally spaced through the text."""
    return [int(n_words * (i / (k + 1))) for i in range(1, k + 1)]


def _positions_equal_skipping_opening(n_words: int, k: int, m: int, n: int) -> list[int]:
    """Same as equal but skips the first M words so window 1 has full context."""
    usable_start = m
    usable_end = n_words - n
    if usable_end <= usable_start:
        raise ValueError(
            f"target has {n_words} words; equal_skipping_opening with M={m}, N={n} "
            f"requires at least M+N+1 = {m + n + 1} words."
        )
    stride = (usable_end - usable_start) / k
    return [int(usable_start + stride * (i + 0.5)) for i in range(k)]


def _positions_stratified(n_words: int, k: int, m: int, n: int) -> list[int]:
    """Divide the text into K equal segments; place one window per segment
    with the continuation ending at the segment boundary."""
    segment_size = n_words // k
    if segment_size < n:
        raise ValueError(
            f"target has {n_words} words; stratified with K={k}, N={n} "
            f"requires segment size >= N, i.e. at least K*N = {k * n} words."
        )
    positions = []
    for i in range(k):
        segment_end = (i + 1) * segment_size
        position = segment_end - n
        if position < m:
            raise ValueError(
                f"stratified window {i + 1}: segment too short for M={m} context "
                f"+ N={n} continuation. Total target words: {n_words}, K: {k}."
            )
        positions.append(position)
    return positions


def compute_fixed_windows(
    strategy: str,
    n_words: int,
    k: int,
    m: int,
    n: int,
    custom_positions: list[int] | None,
) -> list[WindowSpec]:
    """For the four fixed-window strategies (equal, equal_skipping_opening,
    stratified, custom), return K WindowSpecs with continuation-start positions
    derived from the strategy."""
    if strategy == "equal":
        positions = _positions_equal(n_words, k)
    elif strategy == "equal_skipping_opening":
        positions = _positions_equal_skipping_opening(n_words, k, m, n)
    elif strategy == "stratified":
        positions = _positions_stratified(n_words, k, m, n)
    elif strategy == "custom":
        if custom_positions is None:
            raise ValueError("--positioning=custom requires --positions WORD1,WORD2,...")
        positions = custom_positions
    else:
        raise ValueError(f"unknown fixed-window positioning strategy: {strategy}")

    specs = []
    for i, p in enumerate(positions):
        if p < m:
            raise ValueError(
                f"window {i + 1} at position {p}: insufficient context "
                f"(needs {m} preceding words, target starts at 0)."
            )
        if p + n > n_words:
            raise ValueError(
                f"window {i + 1} at position {p}: insufficient continuation room "
                f"(needs {n} following words, target has {n_words})."
            )
        specs.append(WindowSpec(
            window_index=i + 1,
            context_start_word=p - m,
            context_end_word=p,
            continuation_start_word=p,
            continuation_end_word=p + n,
            context_word_count=m,
        ))
    return specs


def compute_expanding_windows(
    context_grid: list[int],
    n_words: int,
    n: int,
) -> list[WindowSpec]:
    """For the expanding-context regime (Design 4): all windows start at the
    document beginning; only the context SIZE grows along context_grid.
    Each window's continuation starts at word ``context_size``."""
    if not context_grid:
        raise ValueError("--positioning=expanding requires --context-grid with >=1 entry.")
    if any(c <= 0 for c in context_grid):
        raise ValueError(f"--context-grid entries must be positive; got {context_grid}.")
    specs = []
    for i, ctx_size in enumerate(context_grid):
        if ctx_size + n > n_words:
            raise ValueError(
                f"expanding window {i + 1}: context_size={ctx_size} + N={n} "
                f"= {ctx_size + n} exceeds target word count {n_words}."
            )
        specs.append(WindowSpec(
            window_index=i + 1,
            context_start_word=0,
            context_end_word=ctx_size,
            continuation_start_word=ctx_size,
            continuation_end_word=ctx_size + n,
            context_word_count=ctx_size,
        ))
    return specs


def detect_overlaps(specs: list[WindowSpec]) -> list[tuple[int, int]]:
    """Return list of (i, j) pairs where window i and j have overlapping
    contexts. Used for the caveats list — overlap doesn't error, just warns."""
    overlaps = []
    for i in range(len(specs)):
        for j in range(i + 1, len(specs)):
            a = specs[i]
            b = specs[j]
            if a.context_end_word > b.context_start_word and b.context_end_word > a.context_start_word:
                overlaps.append((a.window_index, b.window_index))
    return overlaps


# ============================================================
# Prompt templates
# ============================================================


T3_TEMPLATE = """\
You are continuing a piece of {genre_descriptor}.

Below is the preceding context. Read it, then continue the text for \
approximately {n} words. Match the author's voice, register, sentence \
rhythm, and topical focus as closely as you can.

Output requirements:
- Output ONLY the continuation. No preamble, no commentary, no \
explanation, no quotation marks, no markdown formatting.
- Begin with the next word that would naturally follow the context.
- Stop after approximately {n} words. Do not summarize. Do not metacomment.

--- BEGIN CONTEXT ---
{context}
--- END CONTEXT ---

Continue here:
"""


T4_HEADER_TEMPLATE = """\
You will be given {k} independent continuation tasks below, each marked \
as a numbered window. Treat each window as if it were the first message \
of a fresh conversation — do not let one window influence another. \
Different windows may come from different points in the same text, but \
treat them independently.

The genre is: {genre_descriptor}

For each window, read its context and produce a continuation of \
approximately {n} words matching the author's voice, register, and pacing.

Output the continuations as a JSON array with one object per window in \
window order, with keys:
  - "window": integer, 1-indexed, matching the window number below
  - "continuation": string, the continuation text only \
(no preamble, no markdown, no quotation marks)

Output ONLY the JSON array. No commentary, no explanation, no markdown \
code fence.

"""


T4_BLOCK_TEMPLATE = """\
=== WINDOW {i} ===
--- CONTEXT ---
{context}
--- END CONTEXT ---
"""


def render_separate_prompt(spec: WindowSpec, context: str, genre_descriptor: str, n: int) -> str:
    return T3_TEMPLATE.format(
        genre_descriptor=genre_descriptor,
        n=n,
        context=context,
    )


def render_batched_prompt(
    specs_with_contexts: list[tuple[WindowSpec, str]],
    genre_descriptor: str,
    n: int,
) -> str:
    header = T4_HEADER_TEMPLATE.format(
        k=len(specs_with_contexts),
        genre_descriptor=genre_descriptor,
        n=n,
    )
    blocks = [
        T4_BLOCK_TEMPLATE.format(i=spec.window_index, context=context)
        for spec, context in specs_with_contexts
    ]
    return header + "\n".join(blocks)


# ============================================================
# Provenance helpers
# ============================================================


def git_head_sha(repo_dir: Path) -> str | None:
    """Return the git HEAD SHA for ``repo_dir`` if it's a repo, else None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_dir),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        pass
    return None


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ============================================================
# Main builder
# ============================================================


@dataclass
class BuildResult:
    out_dir: Path
    manifest: dict
    window_specs: list[WindowSpec]


def build(
    target_path: Path,
    out_root: Path,
    windows: int,
    context: int,
    continuation: int,
    positioning: str,
    positions: list[int] | None,
    context_grid: list[int] | None,
    fmt: str,
    genre_descriptor: str,
    run_id: str | None,
    tool_path: Path | None = None,
) -> BuildResult:
    """Build prompts and return the result. Operator-callable from tests."""
    raw = target_path.read_text(encoding="utf-8")
    normalized = normalize_text(raw)
    tokens = tokenize(normalized)
    n_words = len(tokens)

    if n_words < 2:
        raise ValueError(f"target must contain >= 2 word tokens; got {n_words}.")

    if positioning == "expanding":
        if context_grid is None or not context_grid:
            raise ValueError(
                "--positioning=expanding requires --context-grid CTX1,CTX2,..."
            )
        specs = compute_expanding_windows(context_grid, n_words, continuation)
    else:
        specs = compute_fixed_windows(
            positioning, n_words, windows, context, continuation, positions
        )

    contexts = []
    for spec in specs:
        ctx_str = slice_words(
            normalized, tokens, spec.context_start_word, spec.context_end_word
        )
        spec.context_sha256 = sha256_hex(ctx_str)
        contexts.append(ctx_str)

    caveats = []
    if len(specs) == 1:
        caveats.append("low_window_count_n_eq_1")
    overlaps = detect_overlaps(specs)
    if overlaps:
        caveats.append("overlapping_windows")

    actual_run_id = run_id or "mirror_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = out_root / actual_run_id
    out_dir.mkdir(parents=True, exist_ok=False)

    if fmt in ("separate", "both"):
        for spec, ctx in zip(specs, contexts):
            prompt = render_separate_prompt(spec, ctx, genre_descriptor, continuation)
            (out_dir / f"window_{spec.window_index}.md").write_text(prompt, encoding="utf-8")

    if fmt in ("batched", "both"):
        prompt = render_batched_prompt(list(zip(specs, contexts)), genre_descriptor, continuation)
        (out_dir / "windows_batched.md").write_text(prompt, encoding="utf-8")

    resolved_tool_path = (tool_path or Path(__file__)).resolve()
    manifest = {
        "run_id": actual_run_id,
        "target_path": str(target_path.resolve()),
        "target_sha256": sha256_hex(normalized),
        "target_word_count": n_words,
        "positioning": positioning,
        "continuation": continuation,
        "context": context if positioning != "expanding" else None,
        "context_grid": context_grid if positioning == "expanding" else None,
        "windows_count": len(specs),
        "windows": [asdict(s) for s in specs],
        "genre_descriptor": genre_descriptor,
        "format": fmt,
        "tool_path": str(resolved_tool_path),
        "tool_sha256": sha256_file(resolved_tool_path),
        "git_head_sha": git_head_sha(resolved_tool_path.parent),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "caveats_recommended": caveats,
    }
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return BuildResult(out_dir=out_dir, manifest=manifest, window_specs=specs)


# ============================================================
# CLI
# ============================================================


def _parse_int_list(s: str) -> list[int]:
    return [int(x.strip()) for x in s.split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase A prompt builder for External Mirror Discrimination."
    )
    parser.add_argument("target", help="Path to target text file (UTF-8).")
    parser.add_argument("--windows", type=int, default=4, help="Number of windows (default 4).")
    parser.add_argument("--context", type=int, default=500, help="Words of preceding context per window (default 500).")
    parser.add_argument("--continuation", type=int, default=150, help="Target words for continuation (default 150).")
    parser.add_argument(
        "--positioning",
        choices=["equal", "equal_skipping_opening", "stratified", "custom", "expanding"],
        default="equal_skipping_opening",
        help="Window positioning strategy (default equal_skipping_opening).",
    )
    parser.add_argument(
        "--positions",
        type=_parse_int_list,
        default=None,
        help="Comma-separated word indices (required when --positioning=custom).",
    )
    parser.add_argument(
        "--context-grid",
        type=_parse_int_list,
        default=None,
        help="Comma-separated context sizes (required when --positioning=expanding).",
    )
    parser.add_argument("--out", default="prompts", help="Output directory (default ./prompts/).")
    parser.add_argument(
        "--format",
        choices=["separate", "batched", "both"],
        default="both",
        help="Output format (default both).",
    )
    parser.add_argument(
        "--genre-descriptor",
        default="literary prose",
        help="Genre label injected into prompts (default 'literary prose').",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Explicit run id (default mirror_YYYYMMDD_HHMMSS).",
    )
    args = parser.parse_args(argv)

    try:
        result = build(
            target_path=Path(args.target),
            out_root=Path(args.out),
            windows=args.windows,
            context=args.context,
            continuation=args.continuation,
            positioning=args.positioning,
            positions=args.positions,
            context_grid=args.context_grid,
            fmt=args.format,
            genre_descriptor=args.genre_descriptor,
            run_id=args.run_id,
        )
    except (ValueError, FileNotFoundError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Built {len(result.window_specs)} window(s) at {result.out_dir}/")
    if result.manifest["caveats_recommended"]:
        print(f"  caveats: {', '.join(result.manifest['caveats_recommended'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
