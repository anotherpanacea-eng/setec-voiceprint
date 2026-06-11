#!/usr/bin/env python3
"""argmove_profile.py — deterministic argument-move population baseline (ArgScope B3/B4 + AGD).

The cheap, judge-free half of ArgScope. Aggregates the *reuse* signals (B3 abstraction,
B4 stance) plus a net-new AGD marker module over a corpus into a population profile, and
**compares two corpora** (the separation test) — to answer *do the deterministic signals
even separate human from LLM argument?* BEFORE any spend on the B1/B2 LLM judge.

NOT the ArgScope surface: no B1/B2 paragraph-role / discourse-mode labeling (that is the
LLM judge, separate). No verdict, no provenance detection. A research/baseline builder,
analogous to voice_profile.py.

Reuses (one source of truth; a contract assertion fails loudly if a signal key vanishes):
  - stance_modality_audit.audit_stance_modality  (B4: hedge/booster/evidential/… per-1k)
  - agency_abstraction_audit.audit_agency_abstraction  (B3: nominalization/concrete/… per-1k)
  - concreteness.get_concreteness  (B3: mean Brysbaert concreteness)

Net-new = the AGD marker module only — scoped to what the stance audit does NOT capture:
  discounting (concession/counter texture -> B1), argument markers (link density -> B1
  proxy, flagged noisy per the substitution-test caveat), and abusive-assuring (a
  distinctive booster subset -> the "thinness localizer"). Guarding sub-types are
  deliberately omitted: they overlap the existing hedge/epistemic/first_person signals.

Usage:
  argmove_profile.py <corpus_dir> [--min-words N] [--json OUT] [--md]
  argmove_profile.py --compare <dirA> <dirB> [--min-words N] [--json OUT] [--md]
  argmove_profile.py --self-test
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stance_modality_audit import audit_stance_modality  # type: ignore  # noqa: E402
from agency_abstraction_audit import audit_agency_abstraction  # type: ignore  # noqa: E402
import concreteness  # type: ignore  # noqa: E402
import re  # noqa: E402

SCRIPT_VERSION = "0.1.0"
MIN_WORDS = 300  # substantiality floor (argument-bearing structure needs length)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]*")


def _n_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _per_1k(count: int, n_words: int) -> float:
    return round(1000.0 * count / n_words, 4) if n_words else 0.0


# ---------------------------------------------------------------- AGD marker module (net-new)
# Discounting connectives — the concession/counter-argument texture (B1-adjacent). The
# unguarded conjunctions (but/yet/still/while) are heavily polysemous; counted but flagged
# a noisy proxy (the substitution test is functional/in-context, which a regex can't do).
_DISCOUNTING = re.compile(
    r"\b(?:although|though|even\s+though|however|nevertheless|nonetheless|"
    r"even\s+if|whereas|despite|in\s+spite\s+of|admittedly|granted|but|yet|still|while)\b",
    re.IGNORECASE,
)
# Argument markers — reason vs conclusion. A B1 link-density PROXY (these words are
# ambiguous out of context: "since"/temporal, "for"/preposition, "so"/intensifier).
_REASON_MARKER = re.compile(r"\b(?:because|since|for|as)\b", re.IGNORECASE)
_CONCLUSION_MARKER = re.compile(
    r"\b(?:therefore|hence|thus|so|accordingly|consequently|ergo)\b", re.IGNORECASE)
# Abusive assuring — a distinctive booster subset. Per Understanding Arguments Ch. 3,
# assuring marks the *weakest* parts of an argument, so this density localizes thinness.
_ABUSIVE_ASSURING = re.compile(
    r"\b(?:as\s+everyone\s+knows|of\s+course|obviously|undeniably|without\s+question|"
    r"no\s+one\s+would\s+deny|it\s+is\s+just\s+common\s+sense|nobody\s+but\s+a\s+fool|"
    r"everyone\s+with\s+any\s+sense|it\s+goes\s+without\s+saying|needless\s+to\s+say)\b",
    re.IGNORECASE,
)


def agd_markers(text: str) -> dict[str, float]:
    """Net-new deterministic AGD densities (per 1,000 words). Proxies, not judge-grade."""
    n = _n_words(text)
    reason = len(_REASON_MARKER.findall(text))
    concl = len(_CONCLUSION_MARKER.findall(text))
    return {
        "discounting_per_1k": _per_1k(len(_DISCOUNTING.findall(text)), n),
        "argument_marker_per_1k": _per_1k(reason + concl, n),
        "reason_to_conclusion_ratio": round(reason / concl, 3) if concl else float(reason),
        "abusive_assuring_per_1k": _per_1k(len(_ABUSIVE_ASSURING.findall(text)), n),
    }


def mean_concreteness(text: str) -> float | None:
    """Mean Brysbaert concreteness over in-vocab words (B3); None if no word is in-vocab."""
    vals = [c for w in _WORD_RE.findall(text.lower())
            if (c := concreteness.get_concreteness(w)) is not None]
    return round(statistics.fmean(vals), 4) if vals else None


# ---------------------------------------------------------------- flat signal vector (reuse)
_STANCE_SUB = ("hedge", "booster", "evidential", "deontic_modality",
               "epistemic_modality", "first_person_stance", "refusal")
_AGENCY_SUB = ("nominalization_per_1k", "generic_institutional_per_1k",
               "concrete_detail_per_1k", "action_verb_per_1k",
               "agentless_passive_per_1k", "light_verb_per_1k", "proper_noun_per_1k")


class ContractError(RuntimeError):
    """A reused audit no longer exposes a signal this aggregator depends on."""


def argmove_vector(text: str) -> dict[str, Any]:
    """One flat signal vector for a document. Raises ContractError if a reused audit's
    output schema drifted (the drift-gate discipline, applied to an in-process dependency)."""
    s = audit_stance_modality(text)
    a = audit_agency_abstraction(text)
    sd = s.get("category_densities_per_1k") or {}
    ad = a.get("densities_per_1k") or {}
    missing = [f"stance.{k}" for k in _STANCE_SUB if k not in sd]
    missing += [f"agency.{k}" for k in _AGENCY_SUB if k not in ad]
    if missing:
        raise ContractError(
            "reused audit output schema drifted; missing signal keys: %r "
            "(update _STANCE_SUB/_AGENCY_SUB or the audit regressed)" % missing)
    vec: dict[str, Any] = {f"stance.{k}": sd[k] for k in _STANCE_SUB}
    vec["stance.hedge_booster_ratio"] = s.get("hedge_booster_ratio")
    vec["stance.entropy_bits"] = s.get("stance_entropy_bits")
    vec.update({f"agency.{k}": ad[k] for k in _AGENCY_SUB})
    vec["agency.entity_to_action_ratio"] = a.get("entity_to_action_ratio")
    mc = mean_concreteness(text)
    if mc is not None:
        vec["abstraction.mean_concreteness"] = mc
    vec.update({f"agd.{k}": v for k, v in agd_markers(text).items()})
    vec["_n_words"] = s.get("n_words") or _n_words(text)
    return vec


# ---------------------------------------------------------------- corpus walk + aggregate
def iter_docs(root: Path, min_words: int = MIN_WORDS) -> tuple[list[tuple[Path, str]], int]:
    """Recursive walk of *.txt / *.md under root, above the substantiality floor."""
    docs: list[tuple[Path, str]] = []
    skipped = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".txt", ".md"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        if _n_words(text) < min_words:
            skipped += 1
            continue
        docs.append((p, text))
    return docs, skipped


def _column(vecs: list[dict[str, Any]], key: str) -> list[float]:
    return [v[key] for v in vecs if v.get(key) is not None]


def profile_corpus(root: Path, min_words: int = MIN_WORDS) -> dict[str, Any]:
    docs, skipped = iter_docs(root, min_words)
    vecs = [argmove_vector(t) for _, t in docs]
    keys = sorted({k for v in vecs for k in v if not k.startswith("_") and v[k] is not None})
    signals: dict[str, Any] = {}
    for k in keys:
        col = _column(vecs, k)
        if len(col) < 1:
            continue
        mean = statistics.fmean(col)
        sd = statistics.pstdev(col) if len(col) > 1 else 0.0
        qs = statistics.quantiles(col, n=10) if len(col) >= 2 else None
        signals[k] = {
            "mean": round(mean, 4), "sd": round(sd, 4),
            "cv": round(sd / mean, 4) if mean else None,
            "median": round(statistics.median(col), 4),
            "band_p10_p90": [round(qs[0], 4), round(qs[-1], 4)] if qs else None,
            "n": len(col),
        }
    return {
        "tool": "argmove_profile", "version": SCRIPT_VERSION,
        "corpus": str(root), "n_docs": len(docs), "n_skipped": skipped,
        "min_words": min_words,
        "calibration_status": "empirically_oriented",  # operator corpus baseline (NOT calibrated)
        "signals": signals,
    }


# ---------------------------------------------------------------- compare (the separation test)
def cliffs_delta(a: list[float], b: list[float]) -> float:
    """Cliff's delta in [-1, 1]: rank-based, robust to skew/non-normality (the primary
    separation stat). >0 means A tends to exceed B."""
    if not a or not b:
        return 0.0
    bs = sorted(b)
    nb = len(bs)
    a_gt_b = a_lt_b = 0
    for x in a:
        a_gt_b += bisect.bisect_left(bs, x)        # #(b < x)
        a_lt_b += nb - bisect.bisect_right(bs, x)  # #(b > x)
    return round((a_gt_b - a_lt_b) / (len(a) * nb), 3)


def cohens_d(a: list[float], b: list[float]) -> float:
    """Cohen's d (secondary; assumes roughly normal/equal-variance — reported alongside d)."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    pooled = math.sqrt(((len(a) - 1) * statistics.variance(a)
                        + (len(b) - 1) * statistics.variance(b)) / (len(a) + len(b) - 2))
    return round((statistics.fmean(a) - statistics.fmean(b)) / pooled, 3) if pooled else 0.0


def compare_corpora(root_a: Path, root_b: Path, min_words: int = MIN_WORDS) -> dict[str, Any]:
    da, _ = iter_docs(root_a, min_words)
    db, _ = iter_docs(root_b, min_words)
    va = [argmove_vector(t) for _, t in da]
    vb = [argmove_vector(t) for _, t in db]
    keys = sorted({k for v in va + vb for k in v if not k.startswith("_")})
    rows = []
    for k in keys:
        ca, cb = _column(va, k), _column(vb, k)
        if len(ca) < 2 or len(cb) < 2:
            continue
        rows.append({
            "signal": k, "cliffs_delta": cliffs_delta(ca, cb), "cohens_d": cohens_d(ca, cb),
            "mean_A": round(statistics.fmean(ca), 4), "mean_B": round(statistics.fmean(cb), 4),
        })
    rows.sort(key=lambda r: -abs(r["cliffs_delta"]))
    return {
        "tool": "argmove_profile", "version": SCRIPT_VERSION, "mode": "compare",
        "A": str(root_a), "B": str(root_b), "n_A": len(da), "n_B": len(db),
        "min_words": min_words,
        "note": ("Cliff's delta is the primary (rank-based) separation stat; |delta| >= ~0.33 "
                 "is a medium+ separation. A single-author pre/post-AI split confounds AI with "
                 "topic/time drift — read those deltas as suggestive, not definitive."),
        "signals": rows,
    }


# ---------------------------------------------------------------- render
def render_profile_md(prof: dict[str, Any]) -> str:
    out = [f"# argmove profile — {prof['corpus']}",
           f"docs: {prof['n_docs']} (skipped {prof['n_skipped']}, floor {prof['min_words']}w) · "
           f"status: {prof['calibration_status']}", "",
           "| signal | mean | sd | cv | median | p10–p90 | n |",
           "|---|---|---|---|---|---|---|"]
    for k, v in prof["signals"].items():
        band = f"{v['band_p10_p90'][0]}–{v['band_p10_p90'][1]}" if v["band_p10_p90"] else "—"
        out.append(f"| {k} | {v['mean']} | {v['sd']} | {v['cv']} | {v['median']} | {band} | {v['n']} |")
    return "\n".join(out)


def render_compare_md(cmp: dict[str, Any]) -> str:
    out = [f"# argmove compare — A={cmp['A']} (n={cmp['n_A']}) vs B={cmp['B']} (n={cmp['n_B']})",
           f"_{cmp['note']}_", "",
           "| signal | Cliff's δ | Cohen's d | mean A | mean B |",
           "|---|---|---|---|---|"]
    for r in cmp["signals"]:
        out.append(f"| {r['signal']} | {r['cliffs_delta']} | {r['cohens_d']} | "
                   f"{r['mean_A']} | {r['mean_B']} |")
    return "\n".join(out)


# ---------------------------------------------------------------- self-test
def run_self_test() -> int:
    import tempfile
    import shutil
    rc = {"v": 0}

    def chk(name: str, cond: bool) -> None:
        print(f"  {name}: {'OK' if cond else 'FAIL'}")
        if not cond:
            rc["v"] = 1

    # 1) AGD marker counts on crafted text
    m = agd_markers("Although it rains we go. Therefore we stay, because of the reasons. "
                    "Of course, as everyone knows, this is fine.")
    chk("agd_discounting_counts", m["discounting_per_1k"] > 0)          # "although"
    chk("agd_argmarker_counts", m["argument_marker_per_1k"] > 0)         # therefore + because
    chk("agd_abusive_assuring_counts", m["abusive_assuring_per_1k"] > 0)  # of course / everyone knows
    chk("agd_zero_clean", agd_markers("The cat sat on the warm stone wall in the bright sun.")
        ["abusive_assuring_per_1k"] == 0.0)

    # 2) concreteness orders concrete > abstract
    chk("concreteness_order",
        (mean_concreteness("table chair stone house dog") or 0)
        > (mean_concreteness("freedom justice essence concept theory") or 0))

    # 3) vector contract + shape
    vec = argmove_vector("This clearly works, but it might be somewhat wrong. Studies show "
                         "the implementation indicates progress, although most experts agree it "
                         "is, of course, obviously correct. Therefore we proceed, because reasons.")
    chk("vector_has_stance", "stance.hedge" in vec and "stance.booster" in vec)
    chk("vector_has_agency", "agency.nominalization_per_1k" in vec)
    chk("vector_has_agd", "agd.discounting_per_1k" in vec and "agd.abusive_assuring_per_1k" in vec)

    # 4) profile + compare on synthetic corpora (hedge-heavy A vs booster/assuring-heavy B)
    d = tempfile.mkdtemp()
    try:
        hedge = (Path(d) / "hedge"); hedge.mkdir()
        boost = (Path(d) / "boost"); boost.mkdir()
        hedge_doc = ("It seems somewhat possible that the result may, to some extent, hold. "
                     "Arguably, in some sense, the finding could perhaps be roughly right, more "
                     "or less, though one cannot be sure. It might, kind of, depend. " * 3)
        boost_doc = ("Obviously this is correct. Of course, as everyone knows, it is undeniably "
                     "true. Clearly, without question, the result is definitely right. Needless "
                     "to say, it goes without saying that this is certainly the case. " * 3)
        for i in range(4):
            (hedge / f"h{i}.txt").write_text(hedge_doc, encoding="utf-8")
            (boost / f"b{i}.txt").write_text(boost_doc, encoding="utf-8")
        prof = profile_corpus(hedge, min_words=30)
        chk("profile_runs", prof["n_docs"] == 4 and "stance.hedge" in prof["signals"])
        cmp = compare_corpora(hedge, boost, min_words=30)
        by = {r["signal"]: r for r in cmp["signals"]}
        chk("compare_hedge_separates", by.get("stance.hedge", {}).get("cliffs_delta", 0) > 0.5)
        chk("compare_assuring_separates",
            by.get("agd.abusive_assuring_per_1k", {}).get("cliffs_delta", 0) < -0.5)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("Self-test: PASS" if rc["v"] == 0 else "Self-test: FAIL")
    return rc["v"]


# ---------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ArgScope deterministic argument-move baseline (B3/B4 + AGD).")
    ap.add_argument("corpus", nargs="?", help="corpus dir to profile")
    ap.add_argument("--compare", nargs=2, metavar=("DIR_A", "DIR_B"), help="separation test between two corpora")
    ap.add_argument("--min-words", type=int, default=MIN_WORDS)
    ap.add_argument("--json", metavar="OUT", help="write JSON to OUT (default: stdout)")
    ap.add_argument("--md", action="store_true", help="also print a markdown table to stderr")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()
    if args.compare:
        result = compare_corpora(Path(args.compare[0]), Path(args.compare[1]), args.min_words)
        md = render_compare_md(result)
    elif args.corpus:
        result = profile_corpus(Path(args.corpus), args.min_words)
        md = render_profile_md(result)
    else:
        ap.print_help()
        return 2

    payload = json.dumps(result, indent=2, default=str)
    if args.json:
        Path(args.json).write_text(payload, encoding="utf-8")
        print(f"argmove_profile: wrote {args.json}")
    else:
        print(payload)
    if args.md:
        print(md, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
