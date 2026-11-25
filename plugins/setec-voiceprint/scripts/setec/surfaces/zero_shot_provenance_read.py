#!/usr/bin/env python3
"""zero_shot_provenance_read.py — one frontier reader's zero-shot provenance account (no verdict).

Runs the prompts from *Frontier models need only one text to unmask its human or AI author*
(Desai, Bölöni-Turgut et al., arXiv:2611.26216), vendored verbatim under
``references/unmasking-authors-prompts/`` (MIT). Two tasks are wired:

* ``ideas-prose`` (``RQ1/joint.txt``): the reader judges separately who originated the IDEAS and
  who wrote the PROSE, citing quoted evidence, and states an AI share (0-1) for each.
* ``which-model`` (``RQ2/holistic.txt``): the reader names the developer, family and version it
  thinks wrote the text, with a stated confidence.

The paper reports that frontier readers given the ideas/prose prompt match a trained detector
(Pangram 4.1) on its benchmarks and that three readers name the right lab for 98%+ of texts from
the three largest labs. Those figures belong to the paper's corpora; this surface does not inherit
them.

How a read is produced (operator's choice, ``--reader``):

1. ``response`` (default for zero-spend work): render the prompt with ``--emit-prompt``, run it on
   any model outside SETEC, save the plain-text answer, and pass it with ``--response``.
2. ``anthropic``: the shared ``judge_backends`` plumbing sends the prompt (one call per run).
   The OpenAI, Gemini and agent-host backends are not offered: they force a JSON reply, and the
   paper's answer format is plain text, so using them would change the prompt being measured.

Posture (no verdict): the envelope reports the reader's own stated shares and labels, the
evidence it cited, and a mechanical check of whether each cited quote occurs in the target. The
paper's 0.5 cut is NOT applied. A reader from the same lab as a model that wrote or edited the
target is refused when the operator declares ``--generator-lab`` (self-assessment). Naming a human
author (the paper's RQ3) is deliberately not wired.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from setec.paths import references_dir, scripts_dir

SCRIPT_DIR = scripts_dir()

from setec.contract.claim_license import from_legacy  # noqa: E402
from setec.contract.output_schema import build_error_output, build_output  # noqa: E402
from setec.core import judge_backends  # noqa: E402
from setec.core.textprims import count_words_alpha as count_words  # noqa: E402

TASK_SURFACE = "zero_shot_provenance_read"
TOOL_NAME = "zero_shot_provenance_read"
SCRIPT_VERSION = "1.0"

PROMPTS_SUBDIR = "unmasking-authors-prompts"
PAPER = "arXiv:2611.26216 (Desai, Bölöni-Turgut et al. 2026)"
TASKS: dict[str, str] = {
    "ideas-prose": "RQ1/joint.txt",
    "which-model": "RQ2/holistic.txt",
}
READERS = ("response", "anthropic")
PROVIDER_LAB = {"anthropic": "anthropic"}

MIN_WORDS = 25
SHORT_WARN_WORDS = 300
LABELS = ("human", "human-leaning", "mixed", "ai-leaning", "ai")

# Model-name tokens -> lab. Token match (split on non-alphanumerics), so "sol" never matches
# inside another word. Unknown names map to None; the caller decides what that means.
_LAB_TOKENS: dict[str, str] = {
    **{t: "anthropic" for t in ("anthropic", "claude", "opus", "sonnet", "haiku", "fable")},
    **{t: "openai" for t in ("openai", "gpt", "chatgpt", "codex", "sol", "astra", "luna")},
    **{t: "google" for t in ("google", "gemini", "gemma", "deepmind", "argon", "carbon",
                             "neon")},
    "deepseek": "deepseek", "kimi": "moonshot", "moonshot": "moonshot", "qwen": "alibaba",
    "alibaba": "alibaba", "glm": "zhipu", "zhipu": "zhipu", "grok": "xai", "xai": "xai",
    "minimax": "minimax", "llama": "meta", "meta": "meta", "mistral": "mistral",
}


class ReadError(RuntimeError):
    """A reader answer could not be produced or parsed."""


# ----------------------------------------------------------------- prompts --------------------

def prompts_root() -> Path:
    return references_dir() / PROMPTS_SUBDIR


def load_template(task: str) -> tuple[str, str, str]:
    """Return ``(relative_name, template_text, sha256)`` for ``task``."""
    rel = TASKS[task]
    raw = (prompts_root() / rel).read_bytes()
    return rel, raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()


def render_prompt(task: str, document: str) -> dict[str, str]:
    """Split the vendored template into system/user messages and insert ``document``.

    Templates with a ``===== USER =====`` block carry the document inside it (``{doc}``); a
    template with system instructions alone (RQ2) takes the document as the whole user message,
    as the upstream README describes.
    """
    rel, text, sha = load_template(task)
    sys_marker, user_marker = "===== SYSTEM =====", "===== USER ====="
    if user_marker in text:
        head, _, user = text.partition(user_marker)
        system = head.replace(sys_marker, "", 1).strip("\n")
        user = user.strip("\n").replace("{doc}", document).replace("{document}", document)
    else:
        system, user = text.strip("\n"), document
    return {"template": rel, "sha256": sha, "system": system, "user": user}


# ----------------------------------------------------------------- parsing --------------------

def lab_of(model_name: str | None) -> str | None:
    if not model_name:
        return None
    for tok in re.split(r"[^a-z0-9]+", model_name.lower()):
        if tok in _LAB_TOKENS:
            return _LAB_TOKENS[tok]
    return None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                                   "—": "-", "–": "-", " ": " "}))
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_text(quote: str, norm_text: str) -> bool:
    """True when every fragment of ``quote`` (split at ellipses) occurs in the target."""
    parts = [p.strip(" .\"'") for p in re.split(r"\[\s*(?:\.\.\.|…)\s*\]|\.\.\.|…", quote)]
    parts = [_norm(p) for p in parts if p.strip(" .\"'")]
    return bool(parts) and all(p in norm_text for p in parts)


_FIELD_RE = r"(LEANS|POINTS TO|STRENGTH|QUOTE|WHY):"


def _parse_items(block: str, head: str) -> list[dict[str, Any]]:
    # Items end where the reasoning section begins, so the last item's WHY stays its own.
    end = re.search(r"(?m)^\s*REASONING\b", block)
    block = block[: end.start()] if end else block
    starts = list(re.finditer(rf"(?m)^\s*(?:\d+[.)]\s*)?{head}:", block))
    items = []
    for i, m in enumerate(starts):
        chunk = block[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(block)]
        fields: dict[str, list[str]] = {"CLAIM": []}
        key = "CLAIM"
        pos = 0
        for fm in re.finditer(_FIELD_RE, chunk):
            fields.setdefault(key, []).append(chunk[pos: fm.start()].strip())
            key, pos = fm.group(1), fm.end()
        fields.setdefault(key, []).append(chunk[pos:].strip())
        quotes = [q.strip().strip('"“”').strip() for q in fields.get("QUOTE", [])]
        item: dict[str, Any] = {
            "claim": " ".join(fields["CLAIM"]).strip(),
            "strength": (fields.get("STRENGTH", [""])[0].split() or [None])[0],
            "quotes": [q for q in quotes if q],
            "why": " ".join(fields.get("WHY", [])).strip() or None,
        }
        if "LEANS" in fields:
            item["leans"] = (fields["LEANS"][0].split() or [None])[0]
        if "POINTS TO" in fields:
            item["points_to"] = fields["POINTS TO"][0].strip() or None
        for k in ("strength", "leans"):
            if isinstance(item.get(k), str):
                item[k] = item[k].lower().strip(".,;")
        items.append(item)
    return items


def _section(block: str, header: str, stop: str) -> str | None:
    m = re.search(rf"(?ms)^\s*{header}\s*:?\s*$(.*?)(?=^\s*{stop})", block)
    return m.group(1).strip() if m else None


def _answer_line(block: str, key: str) -> str | None:
    found = re.findall(rf"(?mi)^\s*{key}\s*:\s*(.+?)\s*$", block)
    return found[-1] if found else None


def _share(block: str, key: str) -> float:
    raw = _answer_line(block, key)
    if raw is None:
        raise ReadError(f"reader answer has no '{key}:' line (refusal or format drift)")
    m = re.match(r"[0-9]*\.?[0-9]+", raw)
    if not m or not 0.0 <= float(m.group(0)) <= 1.0:
        raise ReadError(f"'{key}: {raw}' is not a number from 0 to 1")
    return float(m.group(0))


def _label(block: str, key: str) -> str:
    raw = _answer_line(block, key)
    label = (raw or "").strip().strip(".").lower()
    if label not in LABELS:
        raise ReadError(f"'{key}: {raw}' is not one of {', '.join(LABELS)}")
    return label


def parse_ideas_prose(answer: str) -> dict[str, Any]:
    split = re.search(r"(?mi)^\s*PART 2\b", answer)
    if not split:
        raise ReadError("reader answer has no 'PART 2' section (refusal or format drift)")
    ideas_block, prose_block = answer[: split.start()], answer[split.start():]
    out = {}
    for name, block, head, tag in (("ideas", ideas_block, "IDEA", "IDEAS"),
                                   ("prose", prose_block, "OBSERVATION", "PROSE")):
        out[name] = {
            "ai_share": _share(block, f"{name}_ai_share"),
            "reader_label": _label(block, f"{name}_prediction"),
            "evidence": _parse_items(block, head),
            "reasoning": _section(block, rf"REASONING \({tag}\)", r"DECISIVE FACTOR"),
            "decisive_factors": _section(block, rf"DECISIVE FACTOR\(S\) \({tag}\)",
                                         rf"{name}_ai_share"),
        }
    return out


def parse_which_model(answer: str) -> dict[str, Any]:
    model = _answer_line(answer, "model")
    if not model:
        raise ReadError("reader answer has no 'model:' line (refusal or format drift)")
    return {
        "model_named": model,
        "lab_named": lab_of(model),
        "confidence": _share(answer, "confidence"),
        "evidence": _parse_items(answer, "OBSERVATION"),
        "reasoning": _section(answer, "REASONING", r"DECISIVE FACTOR"),
        "decisive_factors": _section(answer, r"DECISIVE FACTOR\(S\)", r"confidence"),
    }


def check_quotes(parsed: dict[str, Any], text: str) -> dict[str, Any]:
    norm_text = _norm(text)
    blocks = [parsed[k] for k in ("ideas", "prose") if k in parsed] or [parsed]
    n = found = 0
    for b in blocks:
        for item in b.get("evidence", []):
            item["quotes_found"] = [quote_in_text(q, norm_text) for q in item["quotes"]]
            n += len(item["quotes"])
            found += sum(item["quotes_found"])
    return {"n_quotes": n, "n_found": found, "found_rate": (found / n) if n else None}


# ----------------------------------------------------------------- running --------------------

def run_reader(reader: str, prompt: dict[str, str], *, model: str | None,
               max_tokens: int) -> tuple[str, dict[str, Any]]:
    """Send the prompt through a ``judge_backends`` provider; return ``(answer, identity)``.

    ``extract_json`` is replaced by a pass-through: the paper's answer is plain text, parsed
    afterwards by :func:`parse_ideas_prose` / :func:`parse_which_model`.
    """
    def build_user_content(_unused: str, judge_input: Any) -> str:
        return judge_input

    def build_result(payload: dict, raw: str, identity: dict, _input: Any) -> Any:
        return raw, identity

    call = judge_backends.make_api_judge(
        reader, model=model, system_preamble=prompt["system"],
        user_prompt="", temperature=1.0, max_tokens=max_tokens,
        build_user_content=build_user_content, build_result=build_result,
        judge_error=ReadError, extract_json=lambda text: {"text": text},
    )
    return call(prompt["user"])


def _claim_license() -> dict[str, str]:
    return {
        "licenses": (
            "One named reader's zero-shot account of the target under the vendored prompt of "
            f"{PAPER}: its stated AI share and label for the ideas and for the prose (or the "
            "model and lab it names, with its stated confidence), the evidence it cites, and a "
            "mechanical check of whether each cited quote occurs in the target."
        ),
        "does_not_license": (
            "Any determination that a person used AI, misrepresented authorship or committed "
            "misconduct. Any accuracy claim for this reader on this register: the paper's "
            "figures (parity with Pangram 4.1, 98% lab naming) come from its own mostly English "
            "2024-26 benchmarks, and frontier readers still flagged 0.5-5% of fresh human-labelled "
            "news. Text a model polished counts as AI prose under this prompt however light the "
            "edit. The paper's 0.5 cut is not applied; the labels are the reader's own words. A "
            "reader from a lab that wrote or edited the target is grading its own family unless "
            "--generator-lab excluded it. Naming a human author is not wired."
        ),
    }


def _error(reason: str, category: str, target: Path | None, words: int = 0) -> dict[str, Any]:
    return build_error_output(task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
                              target_path=target, target_words=words, reason=reason,
                              reason_category=category)


def _reader_lab(args: argparse.Namespace) -> str | None:
    if args.reader_lab:
        return args.reader_lab.lower()
    return PROVIDER_LAB.get(args.reader) or lab_of(args.reader_model)


def build_envelope(args: argparse.Namespace) -> dict[str, Any]:
    target: Path = args.target
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _error(f"cannot read target: {exc}", "bad_input", target)
    words = count_words(text)
    if words < MIN_WORDS:
        return _error(f"target too short ({words} words, need >= {MIN_WORDS})",
                      "text_too_short", target, words)

    reader_lab = _reader_lab(args)
    generator_labs = sorted({g.lower() for g in (args.generator_lab or [])})
    if generator_labs and (reader_lab is None or reader_lab in generator_labs):
        why = ("the reader's lab is unknown, so it cannot be shown to differ"
               if reader_lab is None else f"the reader is from that lab ({reader_lab})")
        return _error(f"refused: target was written or edited by {', '.join(generator_labs)} "
                      f"and {why}. Use a reader from another lab, or name it with --reader-lab.",
                      "policy_refused", target, words)

    prompt = render_prompt(args.task, text)
    if args.reader == "response":
        if args.response is None:
            return _error("--reader response needs --response FILE (a saved plain-text answer)",
                          "bad_input", target, words)
        try:
            answer = args.response.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return _error(f"cannot read --response: {exc}", "bad_input", target, words)
        identity: dict[str, Any] = {"kind": "response", "model": args.reader_model}
    elif not args.reader_model:
        return _error(f"--reader {args.reader} needs --reader-model", "bad_input", target, words)
    else:
        try:
            answer, identity = run_reader(args.reader, prompt, model=args.reader_model,
                                          max_tokens=args.max_tokens)
        except ReadError as exc:
            category = "missing_dependency" if "SDK" in str(exc) else "internal_error"
            return _error(f"reader call failed: {exc}", category, target, words)

    try:
        parsed = (parse_ideas_prose(answer) if args.task == "ideas-prose"
                  else parse_which_model(answer))
    except ReadError as exc:
        return _error(str(exc), "bad_input", target, words)
    quote_check = check_quotes(parsed, text)

    warnings = []
    if words < SHORT_WARN_WORDS:
        warnings.append(f"target has {words} words; the paper found attribution weaker on "
                        "short texts (13.8% under 500 words vs 43.3% over 4,000 for author naming)")
    if quote_check["n_quotes"] and quote_check["n_found"] < quote_check["n_quotes"]:
        warnings.append(f"{quote_check['n_quotes'] - quote_check['n_found']} of "
                        f"{quote_check['n_quotes']} cited quotes do not occur in the target")
    if not generator_labs:
        warnings.append("no --generator-lab declared: if the reader's own lab wrote or edited "
                        "this text, the read is self-assessment")

    results = {
        "task": args.task,
        "prompt": {"template": prompt["template"], "sha256": prompt["sha256"], "source": PAPER},
        "reader": {"kind": identity.get("kind"), "model": identity.get("model"),
                   "lab": reader_lab},
        "generator_labs_excluded": generator_labs,
        **parsed,
        "quote_check": quote_check,
    }
    return build_output(
        task_surface=TASK_SURFACE, tool=TOOL_NAME, version=SCRIPT_VERSION,
        target_path=str(target), target_words=words, baseline=None, results=results,
        claim_license=from_legacy(_claim_license(), task_surface=TASK_SURFACE),
        warnings=warnings or None,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target", type=Path, help="UTF-8 text to read")
    p.add_argument("--task", choices=sorted(TASKS), default="ideas-prose")
    p.add_argument("--emit-prompt", action="store_true",
                   help="print the rendered {system, user} prompt as JSON and exit")
    p.add_argument("--reader", choices=READERS, default="response",
                   help="where the answer comes from (default: a saved --response file)")
    p.add_argument("--response", type=Path, default=None,
                   help="plain-text answer saved from running the --emit-prompt output")
    p.add_argument("--reader-model", default=None,
                   help="the reader's model id (sent to an API reader; recorded otherwise)")
    p.add_argument("--reader-lab", default=None,
                   help="the reader's lab, when it cannot be inferred from the provider or model")
    p.add_argument("--generator-lab", action="append", default=None,
                   help="a lab whose model wrote or edited the target (repeatable); a reader "
                        "from that lab, or of unknown lab, is refused")
    p.add_argument("--max-tokens", type=int, default=8192)
    p.add_argument("--json", action="store_true", help="print the envelope to stdout")
    p.add_argument("--out", type=Path, default=None, help="write the envelope to this path")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.emit_prompt:
        try:
            text = args.target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            sys.stderr.write(f"[{TOOL_NAME}] cannot read target: {exc}\n")
            return 2
        print(json.dumps(render_prompt(args.task, text), indent=2, ensure_ascii=False))
        return 0
    envelope = build_envelope(args)
    out = json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False)
    if args.out:
        args.out.write_text(out + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(out)
    return 0 if envelope.get("available", True) else 3


if __name__ == "__main__":
    raise SystemExit(main())
