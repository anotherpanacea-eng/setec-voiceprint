#!/usr/bin/env python3
"""position_pair_register.py — the ``position_pair_register`` producer surface
(stance-consistency PR 1). SPEC: setec-scratch/apo-stance-consistency/SPEC.md,
build contract = v4 as amended by v5.

WHAT THIS SURFACE DOES (and the wall it does NOT cross)
=======================================================
Given ONE long nonfiction argument-shaped work and an LLM judge, it emits a
**register of passage PAIRS that address the same question Q** — each pair carries
a neutral interrogative ``question`` and the two passages' verbatim loci
(``{doc, start_char, end_char, quote}``), in DOCUMENT ORDER. That is all.

**It NEVER asserts a relation.** It does not say the two passages agree, conflict,
contradict, oppose, or are in tension; it does not rank pairs by disagreement; it
does not say which passage is right. The *human* reads both passages and owns 100%
of that conflict call. This is the fleet's deliberate NON-step across the content
verdict wall: the model points at two passages sharing a question; the human decides
everything about the relationship (SPEC.md v3/v4 rationale — the "difference in
kind" the re-scope closes by splitting the task so the model *cannot* assert
opposition).

THE POSTURE-CRITICAL GATES (mechanical, not rhetorical)
=======================================================
Two Python gates carry the firewall. They are written first and tested hardest:

* **F4 — the Q-string gate** (``_gate_question``): a surfaced ``question`` must be
  (a) INTERROGATIVE IN FORM (ends with ``?`` and opens with an interrogative /
  auxiliary token) and (b) FREE OF RELATION VOCABULARY (a case-folded substring
  scan against ``_BANNED_Q_VOCAB``). A Q that fails EITHER check has its pair
  REFUSED — dropped, warned, and counted in a disclosure. **Honest downgrade
  (stated in the claim surface):** the interrogative-form check is SYNTAX ONLY and
  gives ZERO protection against loaded / presuppositional questions ("Why does the
  author abandon X in Ch 9?" passes both gates); the human terminus, not the form
  gate, is the guarantee. The substring scan also conservatively false-refuses some
  legitimate topics ("counterargument", "incompatibilist", "conflict of interest",
  "encounter") — accepted by design; a word-boundary rule would weaken the guard.

* **F3 — the runtime banned-key gate** (``_assert_no_banned_keys``): before the
  envelope is returned, a recursive key walk (walk shape from PR #298's
  ``test_envelope_carries_no_verdict_keys_recursive``, NOT its key list — the
  stance set is net-new) RAISES on any relation key anywhere in the envelope, and
  on any generic verdict key inside the ``results.pairs`` subtree. This is a
  RUNTIME gate (stronger than #298's test-only walk, deliberately), plus a mirror
  test over the real mock envelope.

M1 / M2
=======
M1 = mock / manifest backends (deterministic, CI-safe). M2 = live providers
(anthropic/openai/gemini/agent_host) — pass-through via ``judge_backends``,
untested beyond registration in PR 1. No new dependencies (stdlib + the judge
seam).

REGISTER SCOPE (v1): nonfiction-argument register only. There is no ``--register``
flag — the scope is DECLARED in the claim surface + the F10(d) refusal
(declared-not-gated convention, mirroring ArgScope), not enforced by a gate.

CLI::

    python3 scripts/position_pair_register.py TARGET \
        [--judge {manifest,mock,anthropic,openai,gemini,agent_host}] \
        [--judge-manifest PATH] [--judge-model ID] [--judge-temperature 0.0] \
        [--judge-max-tokens N] [--cap-per-question 12] [--cap-per-work 60] \
        [--json] [--out PATH] [--out-md PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from claim_license import ClaimLicense  # type: ignore  # noqa: E402
from output_schema import (  # noqa: E402
    build_error_output,
    build_output,
)
from stylometry_core import word_tokens  # type: ignore  # noqa: E402
import position_pair_register_judge as ppj  # type: ignore  # noqa: E402
from position_pair_register_judge import JudgeError, PositionPair  # type: ignore  # noqa: E402

TASK_SURFACE = "position_pair_register"
TOOL_NAME = "position_pair_register"
SCRIPT_VERSION = "0.1.0"

# The single document label this single-work surface attaches to every locus.
DOC_LABEL = "target"

# Pair caps are a DISCLOSURE, never a judgment — over-cap survivors are the FIRST N
# by document order and the dropped loci are logged (F2). Operator-tunable.
DEFAULT_CAP_PER_QUESTION = 12
DEFAULT_CAP_PER_WORK = 60

# Below this word floor the "same question" pairing is unreliable on a short input;
# the surface WARNS (never refuses) — the value is still reported, never over-claimed.
LENGTH_FLOOR_WORDS = 300


# ======================================================================
# F4 — the Q-string gate (posture-critical; written first, tested hardest).
# ======================================================================

# (a) interrogative FORM: Q must end with '?' and open with an interrogative /
#     auxiliary token. SYNTAX ONLY — zero protection against loaded questions.
_INTERROGATIVE_OPENERS = (
    "what", "how", "whether", "does", "do", "is", "are", "should", "can",
    "which", "when", "where", "who", "why",
)
_INTERROGATIVE_RE = re.compile(
    r"^(?:" + "|".join(_INTERROGATIVE_OPENERS) + r")\b",
    re.IGNORECASE,
)

# (b) banned RELATION vocabulary in the Q VALUE (case-folded substring). v4 base ∪
#     v5 stems. Substring (not word-boundary) is deliberate: word-boundary would
#     weaken the guard. Known conservative false-refusals ("counterargument",
#     "incompatibilist", "conflict of interest", "encounter") are ACCEPTED by
#     design and documented in the claim surface. Note: "oppos" subsumes
#     "oppose*"/"opposite"; "revers" subsumes "reversal".
_BANNED_Q_VOCAB: frozenset[str] = frozenset({
    # v4 base
    "tension", "contradict", "contradiction", "conflicting", "conflicts",
    "conflict", "oppos", "opposing", "inconsisten", "incompatib", "at odds",
    "versus", " vs ", "reversal", "flip-flop", "disagree",
    # v5 stems
    "revers", "undercut", "undermin", "counter", "repudiat", "recant",
    "backtrack", "diverg", "discrepanc", "at variance", "square with",
})


def _gate_question(question: str) -> str | None:
    """Return ``None`` if the Q PASSES both F4 checks; otherwise a short reason
    string (why it is REFUSED). The interrogative-form check is SYNTAX ONLY (no
    protection against presuppositional questions — the human terminus is the
    guarantee). The banned-vocab scan is a case-folded SUBSTRING match (conservative
    false-refusals accepted by design)."""
    q = question.strip()
    if not q.endswith("?"):
        return "not interrogative in form (must end with '?')"
    if not _INTERROGATIVE_RE.match(q):
        return (
            "not interrogative in form (must open with an interrogative/auxiliary "
            "token)"
        )
    low = q.casefold()
    for banned in _BANNED_Q_VOCAB:
        if banned in low:
            return f"contains banned relation vocabulary {banned!r}"
    return None


# ======================================================================
# F3 — the runtime banned-key gate (posture-critical; RAISES before return).
# ======================================================================

class BannedKeyError(RuntimeError):
    """Raised by ``_assert_no_banned_keys`` when the envelope (or its pairs
    subtree) carries a relation / verdict KEY. A relation key anywhere in the
    envelope, or a generic verdict key inside ``results.pairs``, means the
    no-relation posture has been breached — the envelope must NEVER be returned."""


# (i) RELATION keys — never legitimate ANYWHERE in the envelope (whole-envelope
#     walk). Substring, case-folded (so `stance_delta`, `has_conflict`, … are all
#     caught). NOTE: the claim_license VALUES legitimately CONTAIN relation words
#     (the F10 refusals say "does NOT license that the passages are in conflict") —
#     the walk bans KEYS, not values, so those are fine.
_BANNED_RELATION_KEYS: frozenset[str] = frozenset({
    "contradiction", "contradicts", "opposes", "opposition", "conflict",
    "conflicting", "tension", "stance", "stance_delta", "polarity",
    "agreement", "disagreement", "inconsistent", "inconsistency",
})

# (ii) GENERIC verdict keys — banned only inside the ``results.pairs`` SUBTREE
#      (payload-scoped). A whole-envelope ban here would false-ERROR on
#      framework-standard metadata (calibration blocks / judge provenance carry
#      `label`/`score` legitimately). The #298 verdict set is unioned in where it
#      matters (the pairs payload), not globally.
_BANNED_VERDICT_KEYS: frozenset[str] = frozenset({
    "verdict", "label", "score", "decision", "prediction",
    "classification", "relation",
})


def _walk_keys(obj: Any, banned: frozenset[str], *, prefix: str = "") -> None:
    """Recursive key walk (shape from #298's
    ``test_envelope_carries_no_verdict_keys_recursive``): at every dict depth,
    case-folded-substring-check each KEY against ``banned``; recurse dict values
    and list/tuple items. Raises ``BannedKeyError`` on the first hit."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            low = str(k).lower()
            for b in banned:
                if b in low:
                    raise BannedKeyError(
                        f"forbidden key {b!r} found at {path!r} — the "
                        f"position-pair register asserts NO relation between "
                        f"passages (the human owns the conflict call)"
                    )
            _walk_keys(v, banned, prefix=path)
    elif isinstance(obj, (list, tuple)):
        for i, item in enumerate(obj):
            _walk_keys(item, banned, prefix=f"{prefix}[{i}]")


def _assert_no_banned_keys(envelope: dict[str, Any]) -> None:
    """The F3 RUNTIME gate. Raises ``BannedKeyError`` if:
      (i)  any RELATION key appears ANYWHERE in the envelope (whole-envelope walk);
      (ii) any generic VERDICT key appears inside ``results.pairs`` (payload-scoped
           walk).
    Called by ``compose_envelope`` BEFORE returning — a breached envelope never
    escapes."""
    _walk_keys(envelope, _BANNED_RELATION_KEYS)
    results = envelope.get("results")
    if isinstance(results, dict):
        pairs = results.get("pairs")
        if pairs is not None:
            _walk_keys(pairs, _BANNED_VERDICT_KEYS, prefix="results.pairs")


# ======================================================================
# F2 — caps (document order → truncate; a disclosure, never a ranking).
# ======================================================================

def _apply_caps(
    pairs: list[PositionPair],
    *,
    cap_per_question: int,
    cap_per_work: int,
) -> tuple[list[PositionPair], int, list[dict[str, Any]]]:
    """Sort surviving pairs by DOCUMENT ORDER (a.start_char, then b.start_char),
    THEN truncate to the per-question and per-work caps. Returns
    ``(kept, n_dropped, dropped_loci)``. The cap is a DISCLOSURE: the dropped loci
    are logged so a human can see what was withheld. There is NO tension /
    confidence / model-order sort anywhere — truncation-by-anything-but-order is a
    ranking channel the posture forbids (F2 ≡ P2-3)."""
    ordered = sorted(pairs, key=lambda p: (p.a_start_char, p.b_start_char))
    kept: list[PositionPair] = []
    dropped: list[PositionPair] = []
    per_q_count: dict[str, int] = {}
    for p in ordered:
        if len(kept) >= cap_per_work:
            dropped.append(p)
            continue
        seen = per_q_count.get(p.question, 0)
        if seen >= cap_per_question:
            dropped.append(p)
            continue
        per_q_count[p.question] = seen + 1
        kept.append(p)
    dropped_loci = [
        {
            "question": p.question,
            "a": p.a_locus(DOC_LABEL),
            "b": p.b_locus(DOC_LABEL),
        }
        for p in dropped
    ]
    return kept, len(dropped), dropped_loci


# ======================================================================
# Results assembly.
# ======================================================================

def build_results(
    judge_result: ppj.JudgeResult,
    *,
    text_len: int,
    cap_per_question: int,
    cap_per_work: int,
    prompt_fingerprint: str,
) -> tuple[dict[str, Any], list[str]]:
    """Assemble the ``results`` payload from a judge result. Applies the F4 Q-gate
    (refuse + disclose), then the F2 caps (document order + disclose). Returns
    ``(results, warnings)``. Never asserts a relation."""
    warnings: list[str] = list(judge_result.warnings)

    # F4 Q-gate: refuse any pair whose Q fails the interrogative-form / banned-vocab
    # checks. Refusals are COUNTED and DISCLOSED (a refusal is a disclosure).
    surviving: list[PositionPair] = []
    refused: list[dict[str, Any]] = []
    for p in judge_result.pairs:
        reason = _gate_question(p.question)
        if reason is None:
            surviving.append(p)
        else:
            refused.append({"question": p.question, "reason": reason})
            warnings.append(f"pair refused (Q-gate): {reason}: {p.question!r}")

    # F2 caps: document order, then truncate; log the dropped loci.
    kept, pairs_dropped_cap, dropped_cap_loci = _apply_caps(
        surviving,
        cap_per_question=cap_per_question,
        cap_per_work=cap_per_work,
    )
    if pairs_dropped_cap:
        warnings.append(
            f"{pairs_dropped_cap} pair(s) dropped by the cap "
            f"(cap_per_question={cap_per_question}, cap_per_work={cap_per_work}); "
            f"dropped loci disclosed in results.pairs_dropped_cap_loci"
        )

    pairs_out = [
        {
            "question": p.question,
            "a": p.a_locus(DOC_LABEL),
            "b": p.b_locus(DOC_LABEL),
        }
        for p in kept
    ]

    results: dict[str, Any] = {
        "calibration_status": "uncalibrated",
        "pairs": pairs_out,
        # Refusal / cap DISCLOSURES (counts + reasons + dropped loci). A cap or a
        # refusal is a disclosure, never a judgment.
        "pairs_refused_q_gate": len(refused),
        "pairs_refused_q_gate_reasons": refused,
        "pairs_dropped_cap": pairs_dropped_cap,
        "pairs_dropped_cap_loci": dropped_cap_loci,
        "caps": {
            "per_question": cap_per_question,
            "per_work": cap_per_work,
        },
        "judge": {"judge_identity": judge_result.judge_identity},
        "prompt_fingerprint_sha256": prompt_fingerprint,
        "run_timestamp_utc": ppj.utc_now(),
    }
    return results, warnings


# ======================================================================
# F10 — claim license (four greppable refusals + honest caveats).
# ======================================================================

DEFAULT_LICENSES = (
    "a register of passage PAIRS in one nonfiction argument-shaped work that "
    "address the SAME question Q, each pair carrying a neutral interrogative Q and "
    "both passages' verbatim loci (doc, start_char, end_char, quote), emitted in "
    "DOCUMENT ORDER. It licenses ONLY the observation that the two passages both "
    "speak to the same question — a pointer for a human to read both, nothing "
    "more."
)

DEFAULT_DOES_NOT_LICENSE = (
    "It does NOT license any claim that the two passages ARE in conflict, "
    "contradiction, tension, or opposition — the register asserts no relation "
    "whatsoever; the human reads both passages and owns 100% of that call. It does "
    "NOT license which passage is correct, better, or which the author 'really' "
    "holds. It is NOT exhaustive: the absence of a pair is NOT evidence of "
    "consistency and confers no clean bill of health. It does NOT license "
    "application to fiction or a narrator/character's beliefs — v1 is the "
    "nonfiction-argument register only (a fiction arc can license a stated change, "
    "making false positives severe). "
    "The Q-string gate is SYNTAX ONLY: it requires interrogative form and refuses "
    "relation vocabulary, but gives ZERO protection against a loaded or "
    "presuppositional question (e.g. 'Why does the author abandon X in Ch 9?' "
    "passes both gates) — the human terminus, not the form gate, is the guarantee. "
    "There is no stance / relation / tension / conflict / agreement / polarity / "
    "verdict / label / score key anywhere in the envelope. "
    "Refusal disclosures (results.pairs_refused_q_gate_reasons) echo the REJECTED "
    "question verbatim in the JSON envelope so the gate is auditable; a rejected "
    "question may itself be framed as a relation — that echo is a record of what "
    "was refused, NOT an assertion by this surface, and it is omitted from the "
    "markdown report."
)


def _claim_license(results: dict[str, Any]) -> ClaimLicense:
    judge_kind = (
        results.get("judge", {}).get("judge_identity", {}).get("kind")
    )
    caveats = [
        # F6 — honest determinism (no "substantially similar" pseudo-claim).
        "Determinism: there is NO run-to-run determinism guarantee for live "
        "backends; an LLM extraction surface is not bit-deterministic. Human "
        "re-review absorbs the drift. CI determinism rides the mock/manifest "
        "backend (deterministic by construction).",
        "Caps are a DISCLOSURE, not a judgment: over-cap survivors are the FIRST N "
        "pairs by document order and every dropped pair's loci are logged "
        "(results.pairs_dropped_cap_loci). There is no tension/confidence/"
        "model-order ranking anywhere.",
        "The Q-gate's banned-vocabulary scan is a case-folded SUBSTRING match, so "
        "it conservatively refuses some legitimate topics that contain a banned "
        "stem ('counterargument', 'incompatibilist', 'conflict of interest', "
        "'encounter'). This over-refusal is accepted by design — a word-boundary "
        "rule would weaken the guard.",
    ]
    if judge_kind == "mock":
        caveats.append(
            "Judge backend is `mock` — a deterministic TEST stub, not a real "
            "reader. Do not infer anything about the work from a mock run."
        )
    elif judge_kind == "manifest":
        caveats.append(
            "Judge backend is `manifest` — the pairs are only as good as whatever "
            "produced the manifest, which this surface cannot verify."
        )

    return ClaimLicense(
        task_surface=TASK_SURFACE,
        licenses=DEFAULT_LICENSES,
        does_not_license=DEFAULT_DOES_NOT_LICENSE,
        comparison_set={
            "mode": "same_question_pairs_uncalibrated",
            "register_scope": "nonfiction-argument only (v1)",
            "judge_kind": judge_kind,
            "judge_model": (
                results.get("judge", {}).get("judge_identity", {}).get("model")
                or "(unspecified)"
            ),
            "prompt_fingerprint_sha256": results.get("prompt_fingerprint_sha256"),
        },
        register_match=["argument-shaped nonfiction (op-ed / policy / testimony)"],
        additional_caveats=caveats,
        references=[
            "ContraDoc: Understanding Self-Contradictions in Documents with Large "
            "Language Models (Li, Raheja & Kumar), arXiv:2311.09182 — the "
            "contradiction-type taxonomy informs what 'same question' pairing must "
            "catch; https://arxiv.org/abs/2311.09182",
            "BeliefShift (opinion-drift / belief-consistency benchmark), "
            "arXiv:2603.23848 — the position-drift framing; "
            "https://arxiv.org/abs/2603.23848 (sweep-sourced id, Mar 2026).",
        ],
    )


# ======================================================================
# Envelope composition (F3 gate fires here, before return).
# ======================================================================

def compose_envelope(
    *,
    target_path: Path | str | None,
    target_words: int,
    results: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the schema_version 1.0 envelope, run the F3 runtime banned-key gate,
    and only THEN return. A relation/verdict key anywhere raises ``BannedKeyError``
    before the envelope can escape."""
    envelope = build_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        target_path=target_path,
        target_words=target_words,
        baseline=None,
        results=results,
        claim_license=_claim_license(results),
        available=True,
        warnings=warnings,
    )
    _assert_no_banned_keys(envelope)
    return envelope


# ======================================================================
# Markdown renderer.
# ======================================================================

def render_markdown(envelope: dict[str, Any]) -> str:
    r = envelope["results"]
    target = envelope["target"]
    pairs = r.get("pairs", [])
    lines: list[str] = [
        "# Position-Pair Register (same-question passage pairs)",
        "",
        f"- **Target:** `{target.get('path')}` ({target.get('words')} words)",
        f"- **Calibration:** `{r.get('calibration_status')}`",
        f"- **Pairs surfaced:** {len(pairs)}  "
        f"**Refused (Q-gate):** {r.get('pairs_refused_q_gate', 0)}  "
        f"**Dropped (cap):** {r.get('pairs_dropped_cap', 0)}",
        "",
        "_This register points at passage pairs that address the SAME question. It "
        "asserts NO relation between them — not agreement, not conflict, not "
        "tension. Read both passages; YOU decide whether they conflict, evolved, "
        "or were mischaracterized._",
        "",
        "## Pairs (document order)",
        "",
    ]
    if not pairs:
        lines.append("_No same-question pairs surfaced._")
    for i, p in enumerate(pairs, 1):
        a = p["a"]
        b = p["b"]
        lines.extend([
            f"### {i}. Q: {p['question']}",
            "",
            f"- **A** [{a['start_char']}:{a['end_char']}]: {a['quote']}",
            f"- **B** [{b['start_char']}:{b['end_char']}]: {b['quote']}",
            "",
        ])
    lines.extend([
        "## Claim license",
        "",
        (envelope.get("claim_license_rendered") or "").rstrip(),
        "",
    ])
    return "\n".join(lines)


# ======================================================================
# CLI.
# ======================================================================

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Position-pair register: surface passage PAIRS in one nonfiction "
            "argument-shaped work that address the SAME question Q, relation-free, "
            "in document order. Asserts NO relation — the human owns the conflict "
            "call. Uncalibrated, experimental."
        ),
    )
    p.add_argument("target", help="Path to the target text file (UTF-8).")
    p.add_argument(
        "--judge",
        choices=("manifest", "mock", "anthropic", "openai", "gemini", "agent_host"),
        default="manifest",
        help="Judge backend for the same-question pairing. `agent_host` delegates "
             "to the host runtime's model (no API key).",
    )
    p.add_argument("--judge-manifest", type=Path, default=None,
                   help="JSON manifest of pre-computed pairs (required for --judge manifest).")
    p.add_argument("--judge-model", default=None, help="Model ID for API judges.")
    p.add_argument("--judge-temperature", type=float, default=0.0)
    p.add_argument("--judge-max-tokens", type=int, default=4096)
    p.add_argument(
        "--cap-per-question", type=int, default=DEFAULT_CAP_PER_QUESTION,
        help=f"Max pairs per question (default {DEFAULT_CAP_PER_QUESTION}). A "
             "disclosure: over-cap survivors are the first by document order; "
             "dropped loci are logged.",
    )
    p.add_argument(
        "--cap-per-work", type=int, default=DEFAULT_CAP_PER_WORK,
        help=f"Max pairs per work (default {DEFAULT_CAP_PER_WORK}).",
    )
    p.add_argument("--out", type=Path, default=None,
                   help="Write output to this path instead of stdout.")
    p.add_argument("--out-md", type=Path, default=None,
                   help="Also write a markdown report to this path.")
    p.add_argument("--json", action="store_true",
                   help="Emit the JSON envelope to stdout instead of markdown.")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    target_path = Path(args.target).expanduser()
    if not target_path.is_file():
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"target file not found at {target_path}",
            reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3
    try:
        text = target_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path),
            reason=f"cannot read target: {exc}", reason_category="bad_input",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    tgt_words = len(word_tokens(text))
    if tgt_words == 0:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=0,
            reason="target has no countable word tokens",
            reason_category="text_too_short",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    warnings: list[str] = []
    if tgt_words < LENGTH_FLOOR_WORDS:
        warnings.append(
            f"target is {tgt_words} words; below the {LENGTH_FLOOR_WORDS}-word "
            "floor the same-question pairing is unreliable — reported but not "
            "over-claimed"
        )

    # Build the judge (bad SETUP input -> bad_input via parser.error, mirroring
    # argument_decision_audit, so setec_run categorizes exit-2 correctly).
    try:
        judge = ppj.build_judge(
            args.judge, manifest_path=args.judge_manifest, model=args.judge_model,
            temperature=args.judge_temperature, max_tokens=args.judge_max_tokens,
        )
    except JudgeError as exc:
        parser.error(f"judge construction failed: {exc}")

    try:
        judge_result = judge(text)
    except JudgeError as exc:
        envelope = build_error_output(
            task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
            target_path=str(target_path), target_words=tgt_words,
            reason=f"judge execution failed: {exc}",
            reason_category="internal_error",
        )
        _emit(envelope, args, as_markdown=False)
        return 3

    results, result_warnings = build_results(
        judge_result,
        text_len=len(text),
        cap_per_question=args.cap_per_question,
        cap_per_work=args.cap_per_work,
        prompt_fingerprint=ppj.fingerprint_prompt(),
    )
    warnings.extend(result_warnings)

    envelope = compose_envelope(
        target_path=target_path,
        target_words=tgt_words,
        results=results,
        warnings=warnings or None,
    )

    if args.out_md is not None:
        Path(args.out_md).write_text(
            render_markdown(envelope) + "\n", encoding="utf-8"
        )
        sys.stderr.write(f"Wrote markdown report to {args.out_md}\n")

    _emit(envelope, args, as_markdown=not args.json)
    return 0


def _emit(envelope: dict[str, Any], args: argparse.Namespace, *, as_markdown: bool) -> None:
    if as_markdown:
        text_out = render_markdown(envelope)
    else:
        text_out = json.dumps(envelope, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(
            text_out + ("\n" if not text_out.endswith("\n") else ""),
            encoding="utf-8",
        )
        sys.stderr.write(f"Wrote output to {args.out}\n")
    if not args.out or args.json:
        sys.stdout.write(text_out + ("\n" if not text_out.endswith("\n") else ""))


if __name__ == "__main__":
    sys.exit(main())
