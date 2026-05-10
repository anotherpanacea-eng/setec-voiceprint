#!/usr/bin/env python3
"""
preprocessing.py
Corpus-hygiene stripping for prose diagnostics.

The diagnostic scripts expect prose. Blog exports, Markdown drafts, and
webpage saves often carry CSS, HTML, code fences, raw JSON, or tables that
spaCy will happily POS-tag as if they were sentences. This module removes
that suspected non-prose before downstream tokenization while preserving
the original file on disk.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable


TOKEN_RE = re.compile(r"\S+")


PREPROCESSING_RULES: tuple[tuple[str, re.Pattern[str] | None, str], ...] = (
    (
        "yaml_front_matter",
        re.compile(r"\A---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL),
        "regex",
    ),
    (
        "html_block",
        re.compile(
            r"(?is)<!--.*?-->|<\s*(style|script|svg|noscript)\b[^>]*>.*?</\s*\1\s*>"
        ),
        "regex",
    ),
    (
        "fenced_code",
        re.compile(r"(?ms)^```[^\n]*\n.*?^```[ \t]*$|```.*?```"),
        "regex",
    ),
    ("indented_code", None, "line_block"),
    (
        "inline_code",
        re.compile(r"`[^`\n]+`"),
        "regex",
    ),
    ("css_at_rule", None, "line_block"),
    ("css_rule_block", None, "line_block"),
    (
        "html_tag",
        re.compile(
            r"</?(?!https?://|mailto:)[A-Za-z][A-Za-z0-9:-]*"
            r"(?:\s+[^<>]*?)?/?>"
        ),
        "regex",
    ),
    ("json_block", None, "line_block"),
    ("ascii_table", None, "line_block"),
)


# --- Masking rules (Release 1, paired-release schedule) ----------
#
# Distinct purpose from PREPROCESSING_RULES (corpus-hygiene / non-prose
# contamination) and AGGRESSIVE_RULES (URL noise, footnotes, citations
# at the markup level). Masking rules remove *prose* that isn't the
# writer's voice: quoted statutes, block quotations, headings,
# common LLM wrapper phrases. Opt-in only — these rules are aggressive
# enough that running them on a normal essay would over-strip.
#
# Used by the analytical-pass masking modes (e.g. `prose_body_only`)
# rather than as defaults. The selectable `--strip-masking` flag and
# preset profiles route here.
MASKING_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        # Markdown / setext headings — `# Title` through `###### h6`
        # plus `Title\n=====` and `Title\n-----` setext form. Headings
        # are not the writer's voice in policy / testimony / academic
        # prose where they're often standardized.
        "markdown_heading",
        re.compile(
            r"(?m)^[ \t]{0,3}#{1,6}[ \t]+[^\n]+|"
            r"^[^\n]+\n[=-]{3,}[ \t]*$"
        ),
        "regex",
    ),
    (
        # Markdown block quote — lines starting with `> `. Conservative:
        # only consumes contiguous blockquote runs (`> ` continued
        # across multiple lines).
        "block_quote",
        re.compile(
            r"(?m)(?:^[ \t]{0,3}>[^\n]*\n?)+"
        ),
        "regex",
    ),
    (
        # Inline double-quoted passages of ~8+ words (50+ characters
        # between quotes, the empirical equivalent on average prose),
        # treating long quotations as not-the-writer's-voice. Short
        # quotations stay in — they're usually phrase-borrowing
        # rather than quoted speech. Conservative on smart quotes;
        # doesn't cross line boundaries to avoid runaway matches on
        # mismatched quotes.
        "long_inline_quotation",
        re.compile(r'["“][^"”\n]{50,}["”]'),
        "regex",
    ),
    (
        # Statutory / case-law citations: U.S.C. references, Pub. L.,
        # case names with v. or vs., section markers (§), and Fed. R.
        # forms. These are quoted authority, not prose voice.
        "statutory_citation",
        re.compile(
            r"\b(?:\d+\s+U\.?\s*S\.?\s*C\.?\s*§+\s*\d+(?:[A-Za-z]+)?"
            r"(?:\([a-zA-Z0-9]+\))*"
            r"|Pub\.\s*L\.\s*No\.\s*\d+[-–]\d+"
            r"|Fed\.\s*R\.\s*(?:Civ|Crim|App|Evid|Bankr)\.\s*P\.\s*\d+"
            r"|§+\s*\d+(?:\.\d+)*(?:\([a-zA-Z0-9]+\))*"
            r"|[A-Z][A-Za-z\-']+\s+v\.\s+[A-Z][A-Za-z\-']+"
            r"(?:,\s*\d+\s+[A-Z][A-Za-z\.]+\s+\d+)?"
            r")"
        ),
        "regex",
    ),
    (
        # LLM wrapper phrases: opening / closing apologetic-or-meta
        # boilerplate that LLM outputs frequently begin or end with.
        # Pattern matches at sentence-or-paragraph boundary so we
        # don't accidentally consume mid-prose mentions of "as an
        # AI" in a discussion ABOUT AI.
        "llm_wrapper_phrase",
        re.compile(
            r"(?im)(?:^|\n)[ \t]*"
            r"(?:As an AI(?:\s+language\s+model)?[^.\n]*\.?"
            r"|I(?:'m|\s+am)?\s+(?:an?\s+)?(?:AI|language\s+model)[^.\n]*\.?"
            r"|I\s+cannot[^.\n]*(?:provide|create|assist|help|generate)[^.\n]*\.?"
            r"|I(?:'m|\s+am)\s+(?:not\s+able\s+to|unable\s+to)[^.\n]*\.?"
            r"|I\s+hope\s+this\s+helps[!.]?"
            r"|Let\s+me\s+know\s+if[^.\n]*\.?"
            r"|Please\s+(?:let\s+me\s+know|feel\s+free)[^.\n]*\.?"
            r")"
        ),
        "regex",
    ),
    (
        # Prompt remnants — if someone pastes a draft alongside the
        # prompt that produced it, we drop the prompt. Conservative
        # leading-prompt patterns at document start.
        "prompt_remnant",
        re.compile(
            r"(?im)\A[ \t]*"
            r"(?:Please\s+(?:write|create|generate|draft|compose)[^.\n]*\.?\s*"
            r"|Write\s+(?:a|an|the)\s+\w+[^.\n]*\.?\s*"
            r"|You\s+are\s+(?:a|an)\s+\w+[^.\n]*\.?\s*"
            r"|Act\s+as\s+(?:a|an)\s+\w+[^.\n]*\.?\s*"
            r"|System:\s*[^\n]*\n?"
            r")"
        ),
        "regex",
    ),
)


# --- Masking profiles -------------------------------------------
#
# Selectable analytical-pass modes that bundle masking rules with
# parts of the existing strip set. Each profile is a list of
# (rule_name, source_set) where source_set is "default" / "aggressive"
# / "masking". Routed by `apply_masking_profile()` via the
# `strip_masking` parameter on `strip_non_prose()`.
#
# Profile choices:
#   - `none` — no masking applied (default; identical to pre-1.31.0)
#   - `prose_body_only` — strip headings + block quotes + long
#     quoted passages + statutory citations. For policy / legal /
#     testimony / academic prose where the body is what matters.
#   - `exclude_quotations` — block quotes + long inline quotations.
#     For essay / fiction / blog prose where some quotation is
#     present but isn't the writer's voice.
#   - `exclude_headings` — markdown headings only. For policy /
#     newsletter prose where headings are imposed by template.
#   - `prose_strict` — every masking rule. Conservative for
#     analytical comparison; never as a default since over-strips.
MASKING_PROFILES: dict[str, tuple[str, ...]] = {
    "none": (),
    "prose_body_only": (
        "markdown_heading", "block_quote",
        "long_inline_quotation", "statutory_citation",
    ),
    "exclude_quotations": (
        "block_quote", "long_inline_quotation",
    ),
    "exclude_headings": (
        "markdown_heading",
    ),
    "prose_strict": (
        "markdown_heading", "block_quote",
        "long_inline_quotation", "statutory_citation",
        "llm_wrapper_phrase", "prompt_remnant",
    ),
}


AGGRESSIVE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "markdown_image",
        re.compile(r"(?m)^\s*!\[[^\]]*\]\([^)]+\)\s*$"),
        "regex",
    ),
    (
        "markdown_link_url",
        re.compile(r"\[([^\]\n]+)\]\((?:https?://|mailto:)[^)]+\)"),
        "link_rewrite",
    ),
    (
        "autolink_url",
        re.compile(r"<(?:https?://|mailto:)[^>\s]+>"),
        "regex",
    ),
    (
        "bare_url",
        re.compile(r"(?m)^\s*(?:https?://|mailto:)\S+\s*$|(?:https?://)\S+"),
        "regex",
    ),
    (
        "footnote_marker",
        re.compile(r"\[\^[^\]]+\]"),
        "regex",
    ),
    (
        "inline_citation",
        re.compile(
            r"\[(?:[A-Z][A-Za-z-]+(?:\s+and\s+[A-Z][A-Za-z-]+)?\s+)?\d{4}[a-z]?\]"
            r"|\([A-Z][A-Za-z-]+(?:\s+(?:and|&)\s+[A-Z][A-Za-z-]+)?"
            r",\s+\d{4}[a-z]?\)"
        ),
        "regex",
    ),
)


DEFAULT_RULE_NAMES = tuple(rule[0] for rule in PREPROCESSING_RULES)
AGGRESSIVE_RULE_NAMES = tuple(rule[0] for rule in AGGRESSIVE_RULES)
MASKING_RULE_NAMES = tuple(rule[0] for rule in MASKING_RULES)


def count_tokens(text: str) -> int:
    return len(TOKEN_RE.findall(text))


def parse_rule_names(value: str | Iterable[str] | None) -> list[str] | None:
    """Parse CLI-style rule names into an ordered unique list."""
    if value is None:
        return None
    if isinstance(value, str):
        raw = [v.strip() for v in value.split(",")]
    else:
        raw = [str(v).strip() for v in value]
    return [v for v in raw if v]


def available_rule_names(*, aggressive: bool = False) -> list[str]:
    names = list(DEFAULT_RULE_NAMES)
    if aggressive:
        names.extend(AGGRESSIVE_RULE_NAMES)
    return names


def available_masking_rules() -> list[str]:
    """Return the names of opt-in masking rules (Release 1).

    Distinct from `available_rule_names()` because masking rules
    operate on prose that isn't the writer's voice (quotations,
    headings, statutory citations, LLM boilerplate) rather than on
    contamination (HTML/CSS/code/tables). They are never on by
    default.
    """
    return list(MASKING_RULE_NAMES)


def available_masking_profiles() -> list[str]:
    """Return the masking-profile preset names (Release 1)."""
    return list(MASKING_PROFILES.keys())


def resolve_masking_rules(
    profile_or_rules: str | Iterable[str] | None,
) -> tuple[str, ...]:
    """Resolve a `--strip-masking` value into a tuple of rule names.

    Accepts:
      - ``None`` or ``""`` → no masking (empty tuple).
      - A profile preset name (e.g. ``"prose_body_only"``) → the
        rule list from `MASKING_PROFILES`.
      - A comma-separated string of rule names (e.g.
        ``"markdown_heading,block_quote"``) → those exact rules.
      - An iterable of rule names → those rules.

    Raises ``ValueError`` for unknown profile or rule names.
    """
    if profile_or_rules is None:
        return ()
    if isinstance(profile_or_rules, str):
        value = profile_or_rules.strip()
        if not value:
            return ()
        if value in MASKING_PROFILES:
            return MASKING_PROFILES[value]
        # Comma-separated rule list.
        names = [v.strip() for v in value.split(",") if v.strip()]
    else:
        names = [str(v).strip() for v in profile_or_rules if str(v).strip()]
    valid = set(MASKING_RULE_NAMES)
    unknown = [n for n in names if n not in valid]
    if unknown:
        raise ValueError(
            "Unknown masking rule(s): "
            + ", ".join(unknown)
            + ". Available rules: "
            + ", ".join(MASKING_RULE_NAMES)
            + ". Profiles: "
            + ", ".join(MASKING_PROFILES.keys())
        )
    return tuple(names)


def _blank_like(text: str) -> str:
    newlines = text.count("\n")
    return "\n" * max(1, min(newlines + 1, 2))


def _record_strip(
    removed: str,
    rule: str,
    counts: Counter[str],
    snippets: dict[str, list[str]],
    *,
    collect_stripped: bool,
) -> str:
    tokens = count_tokens(removed)
    counts[rule] += tokens
    if collect_stripped and removed.strip():
        bucket = snippets.setdefault(rule, [])
        if len(bucket) < 20:
            bucket.append(removed.strip())
    return _blank_like(removed)


def _apply_regex_rule(
    text: str,
    rule: str,
    pattern: re.Pattern[str],
    counts: Counter[str],
    snippets: dict[str, list[str]],
    *,
    collect_stripped: bool,
) -> str:
    def repl(match: re.Match[str]) -> str:
        return _record_strip(
            match.group(0), rule, counts, snippets,
            collect_stripped=collect_stripped,
        )

    return pattern.sub(repl, text)


def _apply_link_rewrite(
    text: str,
    rule: str,
    pattern: re.Pattern[str],
    counts: Counter[str],
    snippets: dict[str, list[str]],
    *,
    collect_stripped: bool,
) -> str:
    def repl(match: re.Match[str]) -> str:
        full = match.group(0)
        keep = match.group(1)
        removed = full.replace(keep, "", 1)
        counts[rule] += count_tokens(removed)
        if collect_stripped:
            snippets.setdefault(rule, []).append(removed.strip())
        return keep

    return pattern.sub(repl, text)


def _brace_delta(line: str) -> int:
    return line.count("{") - line.count("}")


CSS_AT_RE = re.compile(
    r"^\s*@(media|keyframes|import|supports|font-face|charset|page|layer)\b",
    re.I,
)
CSS_BLOCK_OPEN_RE = re.compile(
    r"^\s*[\.\#\&\*\>\+A-Za-z][\w\-\.\#\:\,\s\>\+\&\*\[\]=\"']*\s*\{"
)
JSON_START_RE = re.compile(r"^\s*\{\s*$")
JSON_KEY_RE = re.compile(r'^\s*"[^"\n]+"\s*:')
# ASCII table: a pipe-row OR a +---+---+ separator. The block-level
# detector below (in ``_strip_line_groups``) further requires that the
# run contain at least one separator OR consistent column counts, to
# avoid false-positives on three pipe-delimited prose lines that are
# not a tabular structure.
ASCII_TABLE_PIPE_RE = re.compile(r"^\s*\|.+\|\s*$")
ASCII_TABLE_SEP_RE = re.compile(r"^\s*\+(?:[-=]+\+)+\s*$|^\s*\|[\s\-:|]+\|\s*$")
ASCII_TABLE_RE = re.compile(r"^\s*(?:\|.+\|\s*|\+(?:[-=]+|\s*)\+.*)$")
# Indented-code rule (4-space or tab indent on three contiguous lines).
# Trade-off: this also catches Markdown-style indented block quotes in
# literary prose (a 3-line indented passage used for emphasis or
# epigraph). The spec accepts the false-positive risk in exchange for
# catching CommonMark indented code blocks. Users hitting this on
# literary corpora can drop the rule via ``--strip-rules`` and keep the
# rest of the pipeline (e.g. ``--strip-rules yaml_front_matter,
# html_block,fenced_code,inline_code,css_at_rule,css_rule_block,
# html_tag,json_block,ascii_table``).
INDENTED_CODE_RE = re.compile(r"^(?: {4,}|\t)\S")


def _consume_balanced_block(
    lines: list[str],
    start: int,
    *,
    max_lines: int | None = None,
) -> int | None:
    depth = 0
    saw_open = False
    limit = len(lines) if max_lines is None else min(len(lines), start + max_lines)
    for idx in range(start, limit):
        delta = _brace_delta(lines[idx])
        if "{" in lines[idx]:
            saw_open = True
        depth += delta
        if saw_open and depth <= 0 and "}" in lines[idx]:
            return idx + 1
    return None


def _strip_line_groups(
    text: str,
    active: set[str],
    counts: Counter[str],
    snippets: dict[str, list[str]],
    *,
    collect_stripped: bool,
) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if "indented_code" in active and INDENTED_CODE_RE.match(line):
            j = i
            while j < len(lines) and INDENTED_CODE_RE.match(lines[j]):
                j += 1
            if j - i >= 3:
                removed = "".join(lines[i:j])
                out.append(_record_strip(
                    removed, "indented_code", counts, snippets,
                    collect_stripped=collect_stripped,
                ))
                i = j
                continue

        if "css_at_rule" in active and CSS_AT_RE.match(line):
            end = None
            if "{" in line:
                end = _consume_balanced_block(lines, i, max_lines=80)
            if end is None:
                end = i + 1
            removed = "".join(lines[i:end])
            out.append(_record_strip(
                removed, "css_at_rule", counts, snippets,
                collect_stripped=collect_stripped,
            ))
            i = end
            continue

        if "css_rule_block" in active and CSS_BLOCK_OPEN_RE.match(line):
            end = _consume_balanced_block(lines, i, max_lines=50)
            if end is not None:
                removed = "".join(lines[i:end])
                out.append(_record_strip(
                    removed, "css_rule_block", counts, snippets,
                    collect_stripped=collect_stripped,
                ))
                i = end
                continue

        if "json_block" in active and JSON_START_RE.match(line):
            end = _consume_balanced_block(lines, i, max_lines=200)
            if end is not None and end - i >= 3:
                block = lines[i:end]
                key_lines = sum(1 for candidate in block if JSON_KEY_RE.match(candidate))
                density = key_lines / max(1, len(block))
                if key_lines >= 2 and density >= 0.30:
                    removed = "".join(block)
                    out.append(_record_strip(
                        removed, "json_block", counts, snippets,
                        collect_stripped=collect_stripped,
                    ))
                    i = end
                    continue

        if "ascii_table" in active and ASCII_TABLE_RE.match(line):
            j = i
            while j < len(lines) and ASCII_TABLE_RE.match(lines[j]):
                j += 1
            if j - i >= 3:
                block = lines[i:j]
                # Require either a header-separator row anywhere in the
                # run, or column-count consistency across pipe rows
                # (the typical Markdown table shape). Three pipe lines
                # without either are likely prose using ``|`` as a
                # delimiter rather than a tabular structure.
                has_separator = any(
                    ASCII_TABLE_SEP_RE.match(candidate) for candidate in block
                )
                pipe_rows = [
                    candidate for candidate in block
                    if ASCII_TABLE_PIPE_RE.match(candidate)
                ]
                col_counts = {row.count("|") for row in pipe_rows}
                consistent = len(col_counts) == 1 and len(pipe_rows) >= 2
                if has_separator or consistent:
                    removed = "".join(block)
                    out.append(_record_strip(
                        removed, "ascii_table", counts, snippets,
                        collect_stripped=collect_stripped,
                    ))
                    i = j
                    continue

        out.append(line)
        i += 1

    return "".join(out)


def _collapse_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def aggregate_preprocessing_metadata(
    per_file: dict[str, dict[str, Any]],
    *,
    rules_active: list[str] | None = None,
    applied: bool = True,
    opt_out: bool = False,
) -> dict[str, Any]:
    before = sum(int(m.get("input_tokens_before", 0) or 0) for m in per_file.values())
    after = sum(int(m.get("input_tokens_after", 0) or 0) for m in per_file.values())
    by_rule: Counter[str] = Counter()
    for meta in per_file.values():
        by_rule.update(meta.get("tokens_stripped_by_rule") or {})
    stripped = before - after
    dominant_rule = None
    if by_rule:
        dominant_rule = by_rule.most_common(1)[0][0]
    out: dict[str, Any] = {
        "applied": applied,
        "opt_out": opt_out,
        "rules_active": rules_active or [],
        "input_tokens_before": before,
        "input_tokens_after": after,
        "tokens_stripped": stripped,
        "tokens_stripped_by_rule": dict(by_rule),
        "strip_ratio": (stripped / before) if before else 0.0,
        "dominant_rule": dominant_rule,
        "per_file": per_file,
    }
    if opt_out:
        out["warning"] = (
            "User passed --allow-non-prose; preprocessing skipped. "
            "KL/JSD readings may include non-prose contamination."
        )
    elif before and stripped:
        out["warning"] = (
            f"Stripped {stripped} tokens of suspected non-prose "
            f"({out['strip_ratio']:.1%} of input)."
        )
    else:
        out["warning"] = None
    return out


def strip_non_prose(
    text: str,
    rules: str | Iterable[str] | None = None,
    *,
    allow_non_prose: bool = False,
    strip_aggressive: bool = False,
    strip_masking: str | Iterable[str] | None = None,
    collect_stripped: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Strip suspected non-prose and return cleaned text plus metadata.

    ``strip_masking`` (Release 1) is a separate, opt-in tier from
    the standard / aggressive rule sets. Accepts a profile preset
    name (``prose_body_only``, ``exclude_quotations``,
    ``exclude_headings``, ``prose_strict``, ``none``), a
    comma-separated list of masking rule names
    (``"markdown_heading,block_quote"``), or an iterable of rule
    names. Resolved by ``resolve_masking_rules()``. Pass ``None``
    or ``"none"`` to skip masking. Masking runs after standard /
    aggressive rules so contamination is removed first; the
    metadata records masked-tokens-by-rule alongside the standard
    counts.
    """
    before = count_tokens(text)
    parsed_rules = parse_rule_names(rules)
    default_names = available_rule_names(aggressive=strip_aggressive)
    active_names = parsed_rules if parsed_rules is not None else default_names
    active = set(active_names)

    masking_active = resolve_masking_rules(strip_masking)

    if allow_non_prose:
        return text, {
            "applied": False,
            "opt_out": True,
            "rules_active": [],
            "masking_rules_active": list(masking_active),
            "input_tokens_before": before,
            "input_tokens_after": before,
            "tokens_stripped": 0,
            "tokens_stripped_by_rule": {},
            "tokens_masked": 0,
            "strip_ratio": 0.0,
            "dominant_rule": None,
            "warning": (
                "User passed --allow-non-prose; preprocessing skipped. "
                "KL/JSD readings may include non-prose contamination."
            ),
        }

    unknown = sorted(set(active_names) - set(default_names))
    if unknown:
        raise ValueError(
            "Unknown preprocessing rule(s): "
            + ", ".join(unknown)
            + ". Available rules: "
            + ", ".join(default_names)
        )

    counts: Counter[str] = Counter()
    snippets: dict[str, list[str]] = {}
    cleaned = text

    for name, pattern, kind in PREPROCESSING_RULES:
        if name not in active:
            continue
        if kind == "regex" and pattern is not None:
            cleaned = _apply_regex_rule(
                cleaned, name, pattern, counts, snippets,
                collect_stripped=collect_stripped,
            )
        elif kind == "line_block":
            cleaned = _strip_line_groups(
                cleaned, {name}, counts, snippets,
                collect_stripped=collect_stripped,
            )

    if strip_aggressive:
        for name, pattern, kind in AGGRESSIVE_RULES:
            if name not in active:
                continue
            if kind == "link_rewrite":
                cleaned = _apply_link_rewrite(
                    cleaned, name, pattern, counts, snippets,
                    collect_stripped=collect_stripped,
                )
            else:
                cleaned = _apply_regex_rule(
                    cleaned, name, pattern, counts, snippets,
                    collect_stripped=collect_stripped,
                )

    # Masking pass (Release 1). Runs after corpus-hygiene and
    # aggressive rules so contamination is removed first; masking
    # rules then operate on the cleaned text.
    masking_set = set(masking_active)
    for name, pattern, kind in MASKING_RULES:
        if name not in masking_set:
            continue
        cleaned = _apply_regex_rule(
            cleaned, name, pattern, counts, snippets,
            collect_stripped=collect_stripped,
        )

    cleaned = _collapse_whitespace(cleaned)
    after = count_tokens(cleaned)
    stripped = max(0, before - after)
    dominant_rule = counts.most_common(1)[0][0] if counts else None
    masking_tokens = sum(counts[r] for r in masking_active if r in counts)
    meta: dict[str, Any] = {
        "applied": True,
        "opt_out": False,
        "rules_active": active_names,
        "masking_rules_active": list(masking_active),
        "input_tokens_before": before,
        "input_tokens_after": after,
        "tokens_stripped": stripped,
        "tokens_stripped_by_rule": dict(counts),
        "tokens_masked": masking_tokens,
        "strip_ratio": (stripped / before) if before else 0.0,
        "dominant_rule": dominant_rule,
        "warning": None,
    }
    if before and stripped:
        meta["warning"] = (
            f"Stripped {stripped} tokens of suspected non-prose "
            f"({meta['strip_ratio']:.1%} of input)."
        )
    if collect_stripped:
        meta["stripped_text_by_rule"] = snippets
    return cleaned, meta
