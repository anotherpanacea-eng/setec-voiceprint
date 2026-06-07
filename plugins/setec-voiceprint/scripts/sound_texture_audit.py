#!/usr/bin/env python3
"""sound_texture_audit.py — descriptive sound-texture profile of prose.

Measures the **sonic** texture of prose — a layer the shipped suite (sentence-length
variance only) is structurally blind to: alliteration / assonance / consonance
adjacency density and a consonant-class (plosive / fricative / sibilant / nasal /
liquid / glide) profile.

This is an **orthographic-onset proxy**, and the claim-license says so plainly: it
reads sound off spelling (word-initial consonant letters, vowel-letter nuclei, final
consonant letters, consonant-class letter membership). English spelling is an
imperfect sound map ("knight", "psalm"), so it is NOT a phonetic transcription, NOT
an AI detector, and NOT a quality judgment — alliteration is a craft choice, not a
tell. No band, no verdict.

With `--baseline-dir`, every metric is reported as a deviation from the writer's own
baseline (`{draft, baseline_mean, baseline_sd, z}`) — strictly descriptive.

Usage:

    python3 scripts/sound_texture_audit.py INPUT.md
    python3 scripts/sound_texture_audit.py INPUT.md --json
    python3 scripts/sound_texture_audit.py INPUT.md --baseline-dir baselines/personal/
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore
from output_schema import build_baseline_metadata, build_output  # type: ignore

TASK_SURFACE = "sound_texture"
TOOL_NAME = "sound_texture_audit"
SCRIPT_VERSION = "1.0"
LENGTH_FLOOR_WORDS = 300

_WORD_RE = re.compile(r"\b\w[\w'-]*\b", re.UNICODE)
_ALPHA_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_VOWELS = frozenset("aeiou")

# Disjoint consonant-class partition over letters; fractions sum to 1 over
# the consonant letters present. Sibilance is reported separately (non-disjoint).
_CONSONANT_CLASS = {
    "plosive": frozenset("pbtdkgcq"),
    "fricative": frozenset("fvszh"),
    "nasal": frozenset("mn"),
    "liquid": frozenset("lr"),
    "glide": frozenset("wyj"),
}
_SIBILANT = frozenset("szx")
_ALL_CONSONANTS = frozenset("bcdfghjklmnpqrstvwxz") | frozenset("y")

METRIC_KEYS = (
    "alliteration_pairs_per_1k",
    "assonance_pairs_per_1k",
    "consonance_pairs_per_1k",
    "sibilant_ratio",
    "vowel_consonant_ratio",
)


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _alpha_words(text: str) -> list[str]:
    return [w.lower() for w in _ALPHA_WORD_RE.findall(text)]


def _onset(word: str) -> str:
    """Leading maximal consonant-letter run (word-initial 'y' is a consonant)."""
    out: list[str] = []
    for i, ch in enumerate(word):
        if ch in _VOWELS or (ch == "y" and i > 0):
            break
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out)


def _nucleus(word: str) -> str:
    """First maximal vowel-letter run (a,e,i,o,u; 'y' counts only non-initially)."""
    started = False
    out: list[str] = []
    for i, ch in enumerate(word):
        is_vowel = ch in _VOWELS or (ch == "y" and i > 0)
        if is_vowel:
            started = True
            out.append(ch)
        elif started:
            break
    return "".join(out)


def _coda(word: str) -> str:
    """Trailing maximal consonant-letter run."""
    out: list[str] = []
    for ch in reversed(word):
        if ch in _VOWELS or ch == "y":
            break
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(reversed(out))


def _per_1k(n: int, words: int) -> float:
    return round(n / words * 1000, 3) if words else 0.0


def audit_sound_texture(text: str) -> dict[str, Any]:
    """Compute the descriptive sound-texture profile. Deterministic."""
    words = _alpha_words(text)
    n_words = len(words)

    onsets = [_onset(w) for w in words]
    nuclei = [_nucleus(w) for w in words]
    codas = [_coda(w) for w in words]

    allit = asson = conson = 0
    for i in range(len(words) - 1):
        if onsets[i] and onsets[i + 1] and onsets[i][0] == onsets[i + 1][0]:
            allit += 1
        if nuclei[i] and nuclei[i + 1] and nuclei[i] == nuclei[i + 1]:
            asson += 1
        if codas[i] and codas[i + 1] and codas[i][-1] == codas[i + 1][-1]:
            conson += 1

    # Letter-level consonant-class profile over the whole text.
    class_counts = {k: 0 for k in _CONSONANT_CLASS}
    class_counts["other"] = 0
    consonant_total = 0
    vowel_total = 0
    sibilant = 0
    for ch in text.lower():
        if not ch.isalpha():
            continue
        if ch in _VOWELS:
            vowel_total += 1
            continue
        # 'y' treated as a consonant for the letter-class profile.
        if ch in _ALL_CONSONANTS:
            consonant_total += 1
            if ch in _SIBILANT:
                sibilant += 1
            placed = False
            for cls, members in _CONSONANT_CLASS.items():
                if ch in members:
                    class_counts[cls] += 1
                    placed = True
                    break
            if not placed:
                class_counts["other"] += 1

    fractions = {
        k: round(v / consonant_total, 4) if consonant_total else 0.0
        for k, v in class_counts.items()
    }

    return {
        "alliteration_pairs_per_1k": _per_1k(allit, n_words),
        "assonance_pairs_per_1k": _per_1k(asson, n_words),
        "consonance_pairs_per_1k": _per_1k(conson, n_words),
        "consonant_class_fractions": fractions,
        "sibilant_ratio": round(sibilant / consonant_total, 4) if consonant_total else 0.0,
        "vowel_consonant_ratio": round(vowel_total / consonant_total, 4) if consonant_total else 0.0,
        "alphabetic_words": n_words,
    }


def _load_baseline(baseline_dir: str) -> tuple[list[dict[str, Any]], list[Path]]:
    files = sorted(
        p for p in Path(baseline_dir).expanduser().glob("**/*")
        if p.suffix.lower() in {".txt", ".md"} and p.is_file()
    )
    profiles: list[dict[str, Any]] = []
    loaded: list[Path] = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        if count_words(text) < LENGTH_FLOOR_WORDS:
            continue
        profiles.append(audit_sound_texture(text))
        loaded.append(f)
    return profiles, loaded


def baseline_deviation(target: dict[str, Any],
                       baseline_profiles: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-metric {draft, baseline_mean, baseline_sd, z}. Descriptive only."""
    out: dict[str, Any] = {}
    for key in METRIC_KEYS:
        vals = [float(p[key]) for p in baseline_profiles]
        mean = statistics.fmean(vals) if vals else 0.0
        sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
        draft = float(target[key])
        z = round((draft - mean) / sd, 3) if sd else 0.0
        out[key] = {
            "draft": round(draft, 3),
            "baseline_mean": round(mean, 3),
            "baseline_sd": round(sd, 3),
            "z": z,
        }
    return out


def _claim_license() -> ClaimLicense:
    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=(
            "descriptive sound-texture measurements — alliteration / assonance / "
            "consonance adjacency density and a consonant-class (plosive / "
            "fricative / sibilant / nasal / liquid / glide) profile — computed via "
            "an orthographic-onset proxy."
        ),
        does_not_license=(
            "any inference about AI provenance, authorial voice/identity, or "
            "writing quality. Sound texture is a craft choice, not an AI signal "
            "and not a quality judgment."
        ),
        comparison_set={"mode": "single_document_descriptive"},
        additional_caveats=[
            "Orthographic-onset PROXY — it reads sound off spelling, NOT a "
            "phonetic transcription; English spelling is an imperfect sound map "
            "('knight', 'psalm').",
            "Tuned to English orthography; other languages' spelling-to-sound "
            "mappings differ.",
            "Density is heavily register-dependent; descriptive only — no band, "
            "no verdict, no threshold.",
        ],
        references=[
            "plugins/setec-voiceprint/specs/17-sound-texture-audit.md",
        ],
    )


def build_payload(results: dict[str, Any], *, target_path: Path | str,
                  word_count: int, available: bool,
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
        claim_license=_claim_license() if available else None,
        available=available,
        warnings=warnings,
    )


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Sound-texture profile — `{payload['target'].get('path')}`",
        "",
        f"**Task surface:** `{TASK_SURFACE}`  ",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}  ",
        f"**Words:** {payload['target']['words']}",
        "",
    ]
    if not payload["available"]:
        lines.append("_Insufficient length — no sound-texture profile produced._")
        for w in payload.get("warnings", []):
            lines.append(f"- {w}")
        return "\n".join(lines) + "\n"

    r = payload["results"]
    lines += [
        "## Texture (orthographic-onset proxy)",
        "",
        f"- **Alliteration:** {r['alliteration_pairs_per_1k']}/1k adjacent pairs",
        f"- **Assonance:** {r['assonance_pairs_per_1k']}/1k",
        f"- **Consonance:** {r['consonance_pairs_per_1k']}/1k",
        f"- **Consonant-class fractions:** {r['consonant_class_fractions']}",
        f"- **Sibilant ratio:** {r['sibilant_ratio']}  |  "
        f"**Vowel:consonant:** {r['vowel_consonant_ratio']}",
        "",
    ]
    if "baseline_deviation" in r:
        lines.append("## Deviation vs. baseline")
        lines.append("")
        for key, dev in r["baseline_deviation"].items():
            lines.append(
                f"- **{key}:** draft {dev['draft']} vs "
                f"{dev['baseline_mean']}±{dev['baseline_sd']} (z={dev['z']})"
            )
        lines.append("")
    lines.append(payload["claim_license_rendered"] or "")
    return "\n".join(lines) + "\n"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help="Path to .md or .txt target file.")
    p.add_argument("--baseline-dir",
                   help="Optional: report each metric as a deviation from this "
                        "writer's own baseline corpus.")
    p.add_argument("--json", action="store_true",
                   help="Emit the JSON envelope instead of a markdown report.")
    p.add_argument("--out", help="Write output to this path instead of stdout.")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_path = Path(args.input).expanduser()
    if not target_path.is_file():
        sys.stderr.write(f"Input not found: {target_path}\n")
        return 2

    text = target_path.read_text(encoding="utf-8", errors="ignore")
    word_count = count_words(text)

    if word_count < LENGTH_FLOOR_WORDS:
        payload = build_payload(
            {}, target_path=target_path, word_count=word_count, available=False,
            warnings=[
                f"Target is {word_count} words; below the {LENGTH_FLOOR_WORDS}-word "
                "floor for a meaningful sound-texture profile."
            ],
        )
    else:
        results = audit_sound_texture(text)
        baseline_meta = None
        warnings: list[str] = []
        if args.baseline_dir:
            profiles, loaded = _load_baseline(args.baseline_dir)
            if profiles:
                results["baseline_deviation"] = baseline_deviation(results, profiles)
                baseline_meta = build_baseline_metadata(
                    n_files=len(loaded),
                    words=sum(p["alphabetic_words"] for p in profiles),
                    files_loaded=loaded,
                )
            else:
                warnings.append(
                    f"No baseline files >= {LENGTH_FLOOR_WORDS} words found in "
                    f"{args.baseline_dir}; reporting standalone metrics."
                )
        payload = build_payload(
            results, target_path=target_path, word_count=word_count,
            available=True, baseline=baseline_meta,
            warnings=warnings or None,
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
