#!/usr/bin/env python3
"""crosslingual_voice_distance.py — language-agnostic, parser-free voice distance.

A stylometric distance between a target and a baseline corpus that uses only
signals which survive a language switch: character n-gram profiles, a punctuation
profile, token-/sentence-length distributions, and script statistics. **No spaCy,
no English assumption** — it runs on any Unicode script with zero model
dependencies. The door-opener for non-English operation, which the framework
otherwise treats only as a fairness caution.

Honest about its ceiling: language-*agnostic*, not language-*aware*. It carries no
morphology and no function-word list, so the claim-license refuses every
morphology-/function-word-dependent voice claim, and refuses cross-language
comparison entirely — the required `--lang` tag is provenance, and target and
baseline must share it.

Usage:

    python3 scripts/crosslingual_voice_distance.py target.txt \
        --baseline-dir baselines/es/ --lang es
    python3 scripts/crosslingual_voice_distance.py target.txt \
        --baseline-dir baselines/es/ --lang es --char-ngram 4 --json
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_output  # type: ignore

TASK_SURFACE = "voice_coherence"
TOOL_NAME = "crosslingual_voice_distance"
SCRIPT_VERSION = "1.0"
LENGTH_FLOOR_WORDS = 500

_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_SENT_SPLIT_RE = re.compile(r"[.!?。！？…।]+")
_PUNCT_SET = ".,;:!?—–-…\"'()[]«»¡¿。、！？"


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _normalize(text: str) -> str:
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", text)).strip()


def char_ngram_counts(text: str, n: int) -> Counter:
    norm = _normalize(text)
    return Counter(norm[i:i + n] for i in range(len(norm) - n + 1)) if len(norm) >= n else Counter()


def _rel_freq(counts: Counter, keys: list[str]) -> dict[str, float]:
    total = sum(counts.values())
    if total == 0:
        return {k: 0.0 for k in keys}
    return {k: counts.get(k, 0) / total for k in keys}


def aux_profile(text: str) -> dict[str, Any]:
    """Punctuation per-1k-chars, token/sentence-length stats, script ratios."""
    chars = len(text)
    tokens = text.split()
    tok_lens = [len(t) for t in tokens]
    sentences = [s for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    sent_lens = [len(s.split()) for s in sentences]
    letters = [c for c in text if c.isalpha()]
    non_ascii_letters = [c for c in letters if ord(c) > 127]
    punct = sum(1 for c in text if c in _PUNCT_SET)
    return {
        "punctuation_per_1k_chars": round(punct / chars * 1000, 3) if chars else 0.0,
        "token_length_mean": round(statistics.fmean(tok_lens), 3) if tok_lens else 0.0,
        "token_length_sd": round(statistics.pstdev(tok_lens), 3) if len(tok_lens) > 1 else 0.0,
        "sentence_length_mean": round(statistics.fmean(sent_lens), 3) if sent_lens else 0.0,
        "sentence_length_sd": round(statistics.pstdev(sent_lens), 3) if len(sent_lens) > 1 else 0.0,
        "non_ascii_letter_ratio": round(len(non_ascii_letters) / len(letters), 4) if letters else 0.0,
        "uppercase_ratio": round(sum(c.isupper() for c in letters) / len(letters), 4) if letters else 0.0,
        "whitespace_ratio": round(sum(c.isspace() for c in text) / chars, 4) if chars else 0.0,
    }


def _cosine_distance(a: dict[str, float], b: dict[str, float], keys: list[str]) -> float:
    va = [a[k] for k in keys]
    vb = [b[k] for k in keys]
    dot = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va))
    nb = math.sqrt(sum(y * y for y in vb))
    if na == 0 or nb == 0:
        return 1.0
    return round(1.0 - dot / (na * nb), 4)


def compute_distance(target_text: str, baseline_texts: list[str], *,
                     n: int, top_k: int) -> dict[str, Any]:
    """Burrows-Delta-style + cosine distance over top-K char n-grams. Deterministic."""
    target_counts = char_ngram_counts(target_text, n)
    baseline_counts = [char_ngram_counts(t, n) for t in baseline_texts]

    pooled = Counter()
    pooled.update(target_counts)
    for c in baseline_counts:
        pooled.update(c)
    # Deterministic top-K: by count desc, then n-gram asc.
    top = sorted(pooled.items(), key=lambda kv: (-kv[1], kv[0]))[:top_k]
    keys = [k for k, _ in top]

    target_rf = _rel_freq(target_counts, keys)
    baseline_rfs = [_rel_freq(c, keys) for c in baseline_counts]

    # Per-feature baseline mean/sd.
    means: dict[str, float] = {}
    sds: dict[str, float] = {}
    for k in keys:
        vals = [rf[k] for rf in baseline_rfs]
        means[k] = statistics.fmean(vals) if vals else 0.0
        sds[k] = statistics.pstdev(vals) if len(vals) > 1 else 0.0

    centroid = means  # mean rel-freq vector is the baseline centroid

    # Delta = mean |z| over features with positive sd. If none vary (e.g.,
    # identical baseline files), fall back to cosine distance — Burrows Delta is
    # undefined with zero corpus variance.
    z_by_key: dict[str, float] = {}
    varying = [k for k in keys if sds[k] > 0]
    if varying:
        for k in varying:
            z_by_key[k] = (target_rf[k] - means[k]) / sds[k]
        delta = round(statistics.fmean(abs(z) for z in z_by_key.values()), 4)
        contrib = sorted(
            ((k, round(abs(z), 4)) for k, z in z_by_key.items()),
            key=lambda kv: (-kv[1], kv[0]))[:10]
    else:
        delta = _cosine_distance(target_rf, centroid, keys)
        contrib = sorted(
            ((k, round(abs(target_rf[k] - centroid[k]), 6)) for k in keys),
            key=lambda kv: (-kv[1], kv[0]))[:10]

    cosine = _cosine_distance(target_rf, centroid, keys)

    # Per-baseline-file cosine distance to the centroid (cohesion of the corpus).
    per_file = [_cosine_distance(rf, centroid, keys) for rf in baseline_rfs]
    per_file_summary = {
        "mean": round(statistics.fmean(per_file), 4) if per_file else 0.0,
        "sd": round(statistics.pstdev(per_file), 4) if len(per_file) > 1 else 0.0,
        "n": len(per_file),
    }

    return {
        "char_ngram_n": n,
        "top_k": len(keys),
        "delta": delta,
        "cosine_distance": cosine,
        "per_baseline_file": per_file_summary,
        "top_contributing_ngrams": [[k, v] for k, v in contrib],
    }


def _load_baseline(baseline_dir: str) -> tuple[list[str], list[Path], int]:
    files = sorted(
        p for p in Path(baseline_dir).expanduser().glob("**/*")
        if p.suffix.lower() in {".txt", ".md"} and p.is_file()
    )
    texts: list[str] = []
    loaded: list[Path] = []
    words = 0
    for f in files:
        t = f.read_text(encoding="utf-8", errors="ignore")
        n = count_words(t)
        if n == 0:  # skip empty / whitespace-only / non-word files
            continue
        texts.append(t)
        loaded.append(f)
        words += n
    return texts, loaded, words


def _claim_license(lang: str) -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "a language-agnostic, parser-free stylometric distance (character "
            "n-grams, punctuation profile, token-/sentence-length distributions, "
            f"and script statistics) between a target and a baseline corpus in the "
            f"declared language ({lang!r})."
        ),
        does_not_license=(
            "any AI-provenance or identity verdict; any morphology- or "
            "function-word-dependent voice claim (this is language-agnostic, NOT "
            "language-aware); or any cross-language comparison — the --lang tag is "
            "provenance, and target and baseline MUST share it."
        ),
        comparison_set={"mode": "target_vs_baseline_corpus", "lang": lang},
        language_match=[lang],
        additional_caveats=[
            "Character n-grams carry topic leakage; a topic change between target "
            "and baseline can read as voice distance.",
            "Needs a register-matched, same-language baseline.",
            "PROVISIONAL — ships no operating point; 'delta' is a relative "
            "distance, not a calibrated band.",
        ],
        references=[
            "plugins/setec-voiceprint/specs/19-crosslingual-voice-distance.md",
        ],
    )


def build_payload(results: dict[str, Any], *, target_path: Path | str,
                  word_count: int, available: bool, lang: str,
                  baseline: dict[str, Any] | None = None,
                  warnings: list[str] | None = None) -> dict[str, Any]:
    return build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=word_count,
        baseline=baseline,
        results=results if available else {},
        claim_license=_claim_license(lang) if available else None,
        available=available,
        warnings=warnings,
    )


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Cross-lingual voice distance — `{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}  ",
        f"**Words:** {payload['target']['words']}",
        "",
    ]
    if not payload["available"]:
        lines.append("_Insufficient input — no distance produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = payload["results"]
    lines += [
        f"**Language:** `{r['lang']}`  |  **char {r['char_ngram_n']}-grams "
        f"(top {r['top_k']})**",
        "",
        "## Distance",
        "",
        f"- **Delta (mean |z|):** {r['delta']}",
        f"- **Cosine distance:** {r['cosine_distance']}",
        f"- **Baseline cohesion (per-file cosine):** {r['per_baseline_file']}",
        f"- **Top contributing n-grams:** {r['top_contributing_ngrams']}",
    ]
    # The opt-in learned-encoder block must appear in the MARKDOWN report too, not only the JSON
    # envelope — otherwise `--encoder muar` without `--json` silently drops it (Codex P2).
    eb = r.get("encoder_block")
    if eb:
        lines += ["", f"## Learned-encoder block — `{eb.get('encoder_id')}` (opt-in `--encoder`)", ""]
        if eb.get("available"):
            lines += [
                f"- **Cosine distribution (target vs baseline centroid):** "
                f"{eb.get('cosine_distribution')}",
                f"- **Windows:** {eb.get('n_windows')} target / "
                f"{eb.get('n_baseline_windows')} baseline",
            ]
        else:
            lines.append(f"_Encoder block unavailable — {eb.get('note', 'no cosine produced')}._")
        if eb.get("claim_license_caveat"):
            lines += ["", eb["claim_license_caveat"]]
    lines += ["", payload["claim_license_rendered"] or ""]
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="Path to .md or .txt target file.")
    p.add_argument("--baseline-dir", required=True,
                   help="Required: register-matched, same-language baseline corpus.")
    p.add_argument("--lang", required=True,
                   help="Required provenance: the language code shared by target "
                        "and baseline (e.g., en, es, fr). Comparison across "
                        "languages is meaningless.")
    p.add_argument("--char-ngram", type=int, default=3,
                   help="Character n-gram order (default: 3).")
    p.add_argument("--top-k", type=int, default=200,
                   help="Top-K most frequent n-grams to profile (default: 200).")
    p.add_argument("--encoder", choices=("muar",), default=None,
                   help="OPT-IN, default OFF. Additionally report a learned "
                        "mUAR (multilingual authorship, arXiv:2509.16531) "
                        "cosine block BESIDE the parser-free distance — it "
                        "does NOT replace delta and does NOT relax the "
                        "same-language --lang refusal. Requires transformers "
                        "(imported lazily, only on this flag); the default "
                        "invocation stays zero-dependency / import-time stdlib.")
    p.add_argument("--json", action="store_true",
                   help="Emit the JSON envelope instead of a markdown report.")
    p.add_argument("--out", help="Write output to this path instead of stdout.")
    return p


def _encoder_cosine_block(
    target_text: str,
    baseline_texts: list[str],
    *,
    encoder_alias: str,
    device: str | None = None,
) -> dict[str, Any]:
    """Compute a learned-encoder cosine block BESIDE the parser-free
    distance (spec 28 M1, opt-in `--encoder muar`).

    POSTURE / [P2] finding folded: the `voice_fingerprint` import is
    LAZY and IN-BRANCH — it lives ONLY inside this function, never at
    the crosslingual module's top level, so the default invocation stays
    import-time stdlib (spec 19's zero-dependency, any-Unicode identity)
    and pulls neither `transformers` NOR `voice_fingerprint` /
    `semantic_trajectory_audit`. The `voice_fingerprint` import chain is
    itself import-time stdlib (it pulls `semantic_trajectory_audit` ->
    `embedding_backend`, whose torch / transformers deps are lazy), so
    this in-branch import does not drag torch in eagerly; `transformers`
    is only touched when the encoder actually loads weights.

    The two-corpus cosine computation is REUSED from `voice_fingerprint`
    (`run_two_corpus` / `_centroid` / `cosine_distribution` via the
    shared windowing) — NOT reimplemented. There is ONE mUAR load path,
    shared with `voice_fingerprint --model muar`.

    Returns an encoder block carrying `encoder_id`, the cosine
    distribution target-vs-baseline-centroid, and a per-encoder
    claim-license caveat. It does NOT replace `delta`, emits NO new
    scalar / band / verdict, and does NOT relax the cross-language
    refusal — the `--lang` shared-provenance contract is unchanged.
    """
    # LAZY, in-branch import — the [P2] guard. Importing voice_fingerprint
    # here (not at module top) keeps the default crosslingual path stdlib.
    import voice_fingerprint as vf  # type: ignore

    encoder_id = vf.MODEL_ALIASES.get(encoder_alias, encoder_alias)
    try:
        encoder = vf._load_encoder(encoder_alias, device=device)
    except vf.VoiceFingerprintError as exc:
        # The OPT-IN encoder couldn't load — a missing style-embedding tier, or a SPEC-ONLY/unreleased
        # encoder such as `muar` (no public checkpoint). Surface the block as unavailable with the
        # reason; the parser-free distance above is unaffected. Do NOT let it traceback the whole run
        # (Codex P1: `--encoder muar` raised an uncaught VoiceFingerprintError here).
        return {"encoder_id": encoder_id, "available": False, "note": str(exc)}

    # Window both corpora with voice_fingerprint's SHARED windowing, then
    # reuse its two-corpus computation (target windows vs. baseline
    # centroid). The crosslingual surface has no windowing of its own; we
    # use the paragraph strategy (voice_fingerprint's default) so the
    # units match the authorship_embedding surface.
    target_windows = vf.split_windows(target_text, "paragraph", window_size=200)
    baseline_windows = vf._window_corpus(baseline_texts, "paragraph", 200)

    if len(target_windows) < 1 or len(baseline_windows) < 1:
        # Not enough windows for a centroid comparison — surface the
        # encoder block as unavailable rather than fabricating a number.
        return {
            "encoder_id": encoder_id,
            "available": False,
            "note": (
                "Too few paragraph windows for a learned-encoder cosine "
                "block (need >=1 target and >=1 baseline window). The "
                "parser-free distance above is unaffected."
            ),
        }

    two_corpus = vf.run_two_corpus(target_windows, baseline_windows, encoder)
    return {
        "encoder_id": encoder_id,
        "available": True,
        "cosine_distribution": two_corpus["cosine_distribution"],
        "n_windows": two_corpus["n_windows"],
        "n_baseline_windows": two_corpus["n_baseline_windows"],
        "claim_license_caveat": (
            f"Learned-encoder block under mUAR (`{encoder_id}`, "
            "arXiv:2509.16531), a MULTILINGUAL authorship manifold. This "
            "is a model-bound cosine DISTRIBUTION beside the parser-free "
            "distance — NOT a same-author / different-author / AI verdict, "
            "NO threshold, NO new scalar. Cosines are within-encoder and "
            "NOT comparable to the parser-free delta or to another "
            "encoder. Multilingual representation is a CAPABILITY, not a "
            "LICENSE: it does NOT relax the same-language --lang refusal — "
            "cross-language comparison remains a separate, calibrated, "
            "explicitly-flagged claim, never the silent default here. "
            "Ships PROVISIONAL — uncalibrated."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2

    target_text = target_path.read_text(encoding="utf-8", errors="ignore")
    word_count = count_words(target_text)
    baseline_texts, loaded, baseline_words = _load_baseline(args.baseline_dir)

    warnings: list[str] = []
    if word_count < LENGTH_FLOOR_WORDS:
        warnings.append(
            f"Target is {word_count} words; below the {LENGTH_FLOOR_WORDS}-word "
            "floor for a meaningful distance."
        )
    if not baseline_texts:
        warnings.append(
            f"No usable (non-empty) .txt/.md baseline files found in "
            f"{args.baseline_dir}."
        )

    if word_count < LENGTH_FLOOR_WORDS or not baseline_texts:
        payload = build_payload(
            {}, target_path=target_path, word_count=word_count, available=False,
            lang=args.lang, warnings=warnings,
        )
    else:
        results = compute_distance(
            target_text, baseline_texts, n=args.char_ngram, top_k=args.top_k)
        results["lang"] = args.lang
        results["target_profile"] = aux_profile(target_text)
        results["baseline_profile"] = aux_profile("\n".join(baseline_texts))
        # OPT-IN learned-encoder block (spec 28 M1). Default OFF: the
        # parser-free distance above is the surface. When --encoder is
        # supplied, a learned cosine block is added BESIDE it (never
        # replacing `delta`); the voice_fingerprint import is lazy and
        # in-branch, so the default path stays import-time stdlib.
        if args.encoder:
            results["encoder_block"] = _encoder_cosine_block(
                target_text, baseline_texts, encoder_alias=args.encoder,
            )
        baseline_meta = build_baseline_metadata(
            n_files=len(loaded), words=baseline_words, files_loaded=loaded,
            extra={"lang": args.lang},
        )
        payload = build_payload(
            results, target_path=target_path, word_count=word_count,
            available=True, lang=args.lang, baseline=baseline_meta,
        )

    text_out = (json.dumps(payload, indent=2, default=str)
                if args.json else render_report(payload))
    if args.out:
        Path(args.out).write_text(text_out, encoding="utf-8")
        sys.stderr.write(f"Wrote report to {args.out}\n")
    else:
        sys.stdout.write(text_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
