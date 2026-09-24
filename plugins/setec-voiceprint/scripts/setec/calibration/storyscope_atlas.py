#!/usr/bin/env python3
"""storyscope_atlas.py -- operator-side StoryScope runs over public-domain
long-form fiction, built for the Victorian craft atlas.

This is manifest CONSTRUCTION, the step the narrative-decision audit leaves
outside the framework ("the recommended pipeline for cross-corpus work runs
the judge outside this script and feeds pre-computed values in via the
``manifest`` backend"). It never emits a SETEC envelope, a band, or a
verdict. Everything it writes is descriptive and lives in one run directory.

Pipeline (each step writes files; every network step is resumable):

  fetch     download Project Gutenberg ebooks, strip the PG wrapper
  plan      chapters, spec-79 segments, a position-stratified segment sample,
            stdlib counts per chapter, and a USD estimate per step
  build     write Message Batches request files for one step
  submit    send one step's requests (refuses above --max-usd)
  collect   poll and download a step's results, recording token usage
  headless  run one step's requests through headless Claude Code on the
            operator's subscription instead of submit/collect
  emit      keyed spec-79 manifests (30 core features), all feature values,
            chapter cards, Sonnet/Opus agreement per feature, and actual cost

Steps and default models:

  cards     Sonnet 5, every chapter: a compact structured card
  features  Sonnet 5, sampled segments: core + adopted + new segment features
  gold      Opus 5.5, a subset of the sampled segments, with evidence spans
  works     Opus 5.5, one call per work over its chapter cards: work features

Run from ``plugins/setec-voiceprint/scripts``::

  python3 -m setec.calibration.storyscope_atlas fetch --run RUN --pilot
  python3 -m setec.calibration.storyscope_atlas plan --run RUN \\
      --taxonomy taxonomy.json
  python3 -m setec.calibration.storyscope_atlas build --run RUN --step cards
  python3 -m setec.calibration.storyscope_atlas submit --run RUN --step cards \\
      --max-usd 5
  python3 -m setec.calibration.storyscope_atlas collect --run RUN --step cards
  ...
  python3 -m setec.calibration.storyscope_atlas emit --run RUN

Transport: ``submit``/``collect`` bill the Message Batches API.
``headless`` sends the same requests through ``claude -p`` with the step's
model, effort and system prompt pinned, all tools off and all local
customizations off (``--safe-mode``), so a subscription can carry the run.
Claude Code adds a short environment preamble of its own; its version is
folded into the prompt version so the two transports never share one.

Data boundary: ``plan`` refuses any work that did not come from
``fetch`` (Project Gutenberg) unless ``--attest-public-domain`` is passed for
a locally added file. Private corpus text must never be sent.

The ``anthropic`` SDK is imported only by ``submit`` and ``collect``; all
other steps are stdlib-only (``headless`` needs the ``claude`` CLI).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import narrative_feature_schema as nfs  # type: ignore
import narrative_longform_segment as nls  # type: ignore

from setec.paths import references_dir

ATLAS_VERSION = "storyscope-atlas/1"
SCHEMA_FILE = "atlas-schema-v1.json"

STEPS = ("cards", "features", "gold", "works")

# Anthropic first-party prices, USD per million tokens, checked 2026-09-23.
# The Message Batches API bills half of these. Cache writes (5-minute TTL)
# bill 1.25x input, cache reads 0.1x input. Verify before a large run.
PRICES = {
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0},
}
BATCH_DISCOUNT = 0.5
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.1

STEP_DEFAULTS = {
    #          model              effort    answer tokens  thinking allowance
    "cards":    ("claude-sonnet-5", "low",    900,  700),
    "features": ("claude-sonnet-5", "low",    1400, 800),
    "gold":     ("claude-opus-5-5", "medium", 3500, 2500),
    "works":    ("claude-opus-5-5", "medium", 2500, 3000),
}
MAX_TOKENS = 16000

# Four public-domain novels for the pilot: Dickens, Eliot, Hardy, and the
# Collins control the Dickens umbrella names. Title fragments are checked
# against the downloaded header so a wrong ebook number refuses.
PILOT = (
    (786, "Hard Times"),
    (550, "Silas Marner"),
    (143, "Mayor of Casterbridge"),
    (155, "Moonstone"),
)
GUTENBERG_URL = "https://www.gutenberg.org/cache/epub/{n}/pg{n}.txt"

_PG_START = re.compile(r"^\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.M | re.I)
_PG_END = re.compile(r"^\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*$", re.M | re.I)

# Chapter headings: the segmenter's chapter-tier vocabulary, plus a lone
# roman numeral line (Hardy). Front-matter tables of contents produce runs
# of tiny units, which split_chapters() folds away.
_ROMAN = r"(?=[IVXLCDM])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_CHAPTER_HEAD = re.compile(
    r"^[ \t]*(?:(?:CHAPTER|STAVE|BOOK|PART)\b[^\n]{0,60}|" + _ROMAN + r"\.?)[ \t]*\r?$",
    re.M | re.I,
)
MIN_CHAPTER_WORDS = 300


class AtlasError(RuntimeError):
    """A refusal the operator must act on. Printed, exit code 2."""


# ----------------------------------------------------------------- io

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_work(run: Path, work_id: str) -> str:
    """A work's text exactly as `_add_work` wrote it. Bytes, not text mode,
    so no platform newline translation can change what was hashed."""
    return (run / "works" / f"{work_id}.txt").read_bytes().decode("utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(path)


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ------------------------------------------------------------- fetch

def strip_gutenberg(raw: str) -> tuple[str, dict]:
    """Return (body, meta) with the Project Gutenberg wrapper removed.

    Refuses text without both START and END markers, so a truncated
    download or a non-PG file cannot pass as a clean work. Gutenberg serves
    CRLF; newlines are normalized to LF before anything is hashed or saved.
    """
    raw = _normalize_newlines(raw)
    start = _PG_START.search(raw)
    end = _PG_END.search(raw)
    if not start or not end or end.start() <= start.end():
        raise AtlasError("no Project Gutenberg START/END markers; not a complete PG text")
    header = raw[: start.start()]
    meta = {}
    for field in ("Title", "Author", "Release date", "Language"):
        m = re.search(rf"^{field}:\s*(.+)$", header, re.M)
        if m:
            meta[field.lower().replace(" ", "_")] = m.group(1).strip()
    body = raw[start.end(): end.start()].strip("\n") + "\n"
    return body, meta


def _inventory_path(run: Path) -> Path:
    return run / "works.json"


def _load_inventory(run: Path) -> list[dict]:
    p = _inventory_path(run)
    return _read_json(p) if p.exists() else []


def _add_work(run: Path, work_id: str, body: str, meta: dict, source: str) -> dict:
    works = [w for w in _load_inventory(run) if w["work_id"] != work_id]
    path = run / "works" / f"{work_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8"))
    row = {
        "work_id": work_id,
        "source": source,
        "title": meta.get("title"),
        "author": meta.get("author"),
        "n_words": nls.count_words(body),
        "text_sha256": _sha256_bytes(body.encode("utf-8")),
    }
    works.append(row)
    _write_json(_inventory_path(run), sorted(works, key=lambda w: w["work_id"]))
    return row


def cmd_fetch(args: argparse.Namespace) -> int:
    ids: list[tuple[int, str | None]] = []
    if args.pilot:
        ids.extend(PILOT)
    ids.extend((n, None) for n in args.ebook)
    if not ids:
        raise AtlasError("nothing to fetch: pass --pilot or --ebook N")
    for n, expect in ids:
        url = GUTENBERG_URL.format(n=n)
        _log(f"fetch {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "setec-storyscope-atlas"})
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (fixed https host)
            raw = resp.read().decode("utf-8-sig", errors="replace")
        body, meta = strip_gutenberg(raw)
        if expect and expect.lower() not in (meta.get("title") or "").lower():
            raise AtlasError(f"ebook {n}: title {meta.get('title')!r} does not contain {expect!r}")
        row = _add_work(args.run, f"pg{n}", body, meta, f"gutenberg:{n}")
        _log(f"  {row['work_id']}: {row['title']} ({row['author']}), {row['n_words']:,} words")
    return 0


def cmd_add_local(args: argparse.Namespace) -> int:
    if not args.attest_public_domain:
        raise AtlasError("add-local needs --attest-public-domain: only public-domain text may be sent")
    body = _normalize_newlines(Path(args.file).read_bytes().decode("utf-8-sig"))
    row = _add_work(args.run, args.work_id, body,
                    {"title": args.title, "author": args.author}, "local-attested-public-domain")
    _log(f"added {row['work_id']} ({row['n_words']:,} words)")
    return 0


# -------------------------------------------------------------- plan

def split_chapters(text: str) -> list[dict]:
    """Chapter units as {index, start, end, n_words}.

    Headings open units. Units under MIN_CHAPTER_WORDS fold into the next
    unit (a BOOK heading directly above a CHAPTER heading, or a table of
    contents), and a trailing short unit folds into the previous one. A text
    with no headings is one unit.
    """
    starts = sorted({m.start() for m in _CHAPTER_HEAD.finditer(text)} | {0})
    spans = [(s, e) for s, e in zip(starts, starts[1:] + [len(text)]) if e > s]
    merged: list[list[int]] = []
    carry: int | None = None
    for s, e in spans:
        s0 = carry if carry is not None else s
        if nls.count_words(text[s0:e]) < MIN_CHAPTER_WORDS:
            carry = s0
            continue
        merged.append([s0, e])
        carry = None
    if carry is not None:
        if merged:
            merged[-1][1] = len(text)
        else:
            merged.append([carry, len(text)])
    return [
        {"index": i, "start": s, "end": e, "n_words": nls.count_words(text[s:e])}
        for i, (s, e) in enumerate(merged)
    ]


def sample_positions(n: int, k: int) -> list[int]:
    """k indices spread evenly over range(n), always including the ends."""
    if k <= 0 or n <= 0:
        return []
    if k >= n:
        return list(range(n))
    if k == 1:
        return [n // 2]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


_QUOTE = re.compile(r"[\"“][^\"“”]{0,4000}?[\"”]", re.S)
_SENT = re.compile(r"[^.!?]+[.!?]+")
_GENERIC_SUBJ = re.compile(
    r"^\s*(?:we|men|women|people|nobody|no one|every(?:one|body)?|all|there (?:is|are)|it is|"
    r"a man|a woman|one|those who|he who|she who|most|few|nothing|life|love|human)\b", re.I)
_PRESENT = re.compile(r"\b(?:is|are|has|have|does|do|can|cannot|must|will|makes?|seems?)\b", re.I)
_READER = re.compile(r"\b(?:reader|readers)\b", re.I)
_WE = re.compile(r"\b(?:we|us|our|ourselves)\b", re.I)
_LETTER = re.compile(r"^\s*(?:(?:my )?dear\s+\w+|sir|madam|yours\s+(?:truly|faithfully|sincerely|affectionately))\b[,.]?\s*$",
                     re.I | re.M)
_TIME = re.compile(r"\b(?:o'clock|morning|evening|night|noon|midnight|dawn|dusk|monday|tuesday|wednesday|"
                   r"thursday|friday|saturday|sunday|january|february|march|april|may|june|july|august|"
                   r"september|october|november|december|spring|summer|autumn|winter|years? ago|"
                   r"next day|the day after)\b", re.I)


def stdlib_counts(text: str) -> dict:
    """Descriptive per-chapter counts. Heuristic, stdlib-only, no model.

    Dialogue is text inside double quotes (straight or curly); a text that
    marks speech with single quotes reports quote_style 'single_or_none' and
    its dialogue share is not measured.
    """
    words = nls.count_words(text)
    quoted = "".join(m.group(0) for m in _QUOTE.finditer(text))
    narr = _QUOTE.sub(" ", text)
    n_double = text.count('"') + text.count("“")
    sents = [s.strip() for s in _SENT.findall(narr.replace("\n", " ")) if s.strip()]
    gnomic = [s for s in sents if _GENERIC_SUBJ.search(s) and _PRESENT.search(s)
              and not re.search(r"\b(?:said|was|were|had)\b", s, re.I)]
    per_k = (lambda c: round(1000 * c / words, 2)) if words else (lambda c: 0.0)
    lens = [nls.count_words(s) for s in sents]
    mean = sum(lens) / len(lens) if lens else 0.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in lens) / len(lens)) if lens else 0.0
    return {
        "n_words": words,
        "quote_style": "double" if n_double >= 4 else "single_or_none",
        "dialogue_share": round(nls.count_words(quoted) / words, 3) if words and n_double >= 4 else None,
        "reader_mentions_per_1k": per_k(len(_READER.findall(narr))),
        "narrator_we_per_1k": per_k(len(_WE.findall(narr))),
        "asides_per_1k": per_k(narr.count("(") + narr.count("—") // 2 + narr.count("--") // 2),
        "letter_markers": len(_LETTER.findall(text)),
        "time_markers_per_1k": per_k(len(_TIME.findall(text))),
        "gnomic_candidates_per_1k_sentences": round(1000 * len(gnomic) / len(sents), 1) if sents else 0.0,
        "narration_sentence_mean_words": round(mean, 1),
        "narration_sentence_sd_words": round(sd, 1),
    }


@dataclass(frozen=True)
class Feature:
    id: str
    kind: str              # "single" | "multi"
    options: tuple[str, ...]
    question: str
    scope: str             # "segment" | "work"
    origin: str            # "core" | "taxonomy" | "new"
    detection: str = ""

    def prompt_row(self) -> dict:
        row = {"feature_id": self.id, "select": "one" if self.kind == "single" else "all_that_apply",
               "options": list(self.options), "question": self.question}
        if self.detection:
            row["how_to_judge"] = self.detection
        return row


def load_atlas_schema() -> dict:
    return _read_json(references_dir() / "storyscope-victorian" / SCHEMA_FILE)


def build_features(taxonomy: dict, schema: dict | None = None) -> list[Feature]:
    """Core 30 + adopted taxonomy features + new dimensions, in that order.

    Refuses if an adopted id is missing from the taxonomy, so a changed
    upstream file cannot silently shrink the feature set.
    """
    schema = schema or load_atlas_schema()
    feats = [
        Feature(f.key, "multi" if f.feature_type == "multi" else "single",
                tuple(f.response_options), f.question, "segment", "core")
        for f in nfs.CORE_FEATURES
    ]
    index: dict[str, dict] = {}
    for dim in taxonomy.get("feature_taxonomy", {}).values():
        for aspect in dim.get("aspects", {}).values():
            for f in aspect.get("features", []):
                index[f["id"]] = f
    missing = [a["id"] for a in schema["adopted"] if a["id"] not in index]
    if missing:
        raise AtlasError(f"taxonomy lacks adopted feature ids: {missing}")
    for a in schema["adopted"]:
        f = index[a["id"]]
        feats.append(Feature(
            a["id"], "multi" if f["type"] == "multi_select" else "single",
            tuple(str(v) for v in f["values"]), f["question"], a["scope"], "taxonomy",
            f.get("detection_method", "")))
    for d in schema["new_dimensions"]:
        feats.append(Feature(d["id"], d["type"], tuple(d["options"]), d["question"],
                             d["scope"], "new"))
    ids = [f.id for f in feats]
    if len(ids) != len(set(ids)):
        raise AtlasError("duplicate feature ids across core, adopted and new")
    return feats


def estimate_tokens(chars: int) -> int:
    """Rough token estimate for English prose: 1 token per 3.8 characters.

    Deliberately a little high for Victorian prose; ``submit`` uses the
    uncached ceiling built on it.
    """
    return math.ceil(chars / 3.8)


def step_cost(model: str, input_tokens: int, cached_prefix_tokens: int, n_requests: int,
              output_tokens: int) -> dict:
    """Estimated batch allowance and cached expectation.

    The allowance reserves maximum output and a cold cache write per request.
    Input token counts and configured prices remain estimates, so this is not
    an absolute provider billing cap.
    """
    p = PRICES[model]
    base_in = input_tokens * p["input"] / 1e6
    prefix_all = cached_prefix_tokens * n_requests * p["input"] / 1e6
    out = output_tokens * p["output"] / 1e6
    ceiling = (base_in + prefix_all * CACHE_WRITE_MULT
               + n_requests * MAX_TOKENS * p["output"] / 1e6) * BATCH_DISCOUNT
    if n_requests:
        cached_prefix = (cached_prefix_tokens * p["input"] / 1e6) * (
            CACHE_WRITE_MULT + CACHE_READ_MULT * (n_requests - 1))
    else:
        cached_prefix = 0.0
    expected = (base_in + cached_prefix + out) * BATCH_DISCOUNT
    return {"ceiling_usd": round(ceiling, 4), "expected_usd": round(expected, 4)}


def cmd_plan(args: argparse.Namespace) -> int:
    run: Path = args.run
    if any((run / "requests").glob("*.meta.json")):
        raise AtlasError("requests already built; use a new run directory to replan")
    works = _load_inventory(run)
    if not works:
        raise AtlasError("no works; run fetch first")
    for w in works:
        if not (w["source"].startswith("gutenberg:") or w["source"] == "local-attested-public-domain"):
            raise AtlasError(f"{w['work_id']}: source {w['source']!r} is outside the public-domain boundary")
    taxonomy = _read_json(Path(args.taxonomy))
    taxonomy_sha = _sha256_bytes(Path(args.taxonomy).read_bytes())
    feats = build_features(taxonomy)
    plan_works = []
    counts_rows = []
    for w in works:
        text = _read_work(run, w["work_id"])
        if _sha256_bytes(text.encode("utf-8")) != w["text_sha256"]:
            raise AtlasError(f"{w['work_id']}: work text changed since fetch")
        chapters = split_chapters(text)
        seg = nls.segment_text(text, segment_target_words=args.segment_target_words)
        sample = sample_positions(seg.n_segments, args.sample_per_work)
        gold = [sample[i] for i in sample_positions(len(sample), args.gold_per_work)]
        for c in chapters:
            counts_rows.append({"work_id": w["work_id"], "chapter": c["index"],
                                **stdlib_counts(text[c["start"]:c["end"]])})
        plan_works.append({
            "work_id": w["work_id"], "title": w["title"], "author": w["author"],
            "text_sha256": w["text_sha256"],
            "chapters": chapters,
            "segmentation": nls.segmentation_dict(seg),
            "segment_spans": [[s.start, s.end] for s in seg.segments],
            "sample": sample, "gold": gold,
        })
    plan = {
        "atlas_version": ATLAS_VERSION,
        "taxonomy_sha256": taxonomy_sha,
        "features": [f.__dict__ for f in feats],
        "works": plan_works,
    }
    _write_json(run / "plan.json", plan)
    _write_jsonl(run / "out" / "stdlib_counts.jsonl", counts_rows)
    estimates = {s: estimate_step(run, plan, s) for s in STEPS}
    _write_json(run / "estimates.json", estimates)
    total_c = sum(e["ceiling_usd"] for e in estimates.values())
    total_e = sum(e["expected_usd"] for e in estimates.values())
    for s, e in estimates.items():
        _log(f"{s:9s} {e['n_requests']:4d} requests  {e['model']:16s} "
             f"expected ${e['expected_usd']:.2f}  ceiling ${e['ceiling_usd']:.2f}")
    _log(f"{'total':9s} {'':27s} expected ${total_e:.2f}  ceiling ${total_c:.2f}")
    return 0


# ------------------------------------------------------------ prompts

_COMMON = (
    "You are annotating public-domain Victorian and nineteenth-century fiction for a "
    "descriptive craft atlas. Report only what is on the page. You are not judging "
    "quality, period authenticity or authorship. Answer every feature. Return one JSON "
    "object and nothing else."
)


def _schema_block(feats: list[Feature]) -> str:
    return json.dumps([f.prompt_row() for f in feats], ensure_ascii=False, indent=1)


def system_prompt(step: str, feats: list[Feature], card_fields: dict) -> str:
    seg_feats = [f for f in feats if f.scope == "segment"]
    if step == "cards":
        return (
            f"{_COMMON}\n\nYou will read one chapter and write a compact card for it. "
            "Fields:\n" + json.dumps(card_fields, indent=1) +
            "\n\nReturn {\"card\": {...}} with exactly these fields. Keep every string short; "
            "quotes must be verbatim and 15 words or fewer."
        )
    if step in ("features", "gold"):
        tail = (
            "Return {\"values\": {feature_id: value}} where value is one option string for "
            "select=one and a list of option strings for select=all_that_apply."
        )
        if step == "gold":
            tail += (
                " Also return \"evidence\": {feature_id: a verbatim quote of 25 words or fewer} "
                "for every feature whose value is not the first option or an empty list."
            )
        return (
            f"{_COMMON}\n\nYou will read one passage of about 5,000 words from a longer work. "
            "Judge each feature on this passage alone.\n\nFeatures:\n" + _schema_block(seg_feats) +
            "\n\n" + tail
        )
    if step == "works":
        return (
            f"{_COMMON}\n\nYou will read the chapter cards for one whole work, in order. "
            "Cards are summaries written by another annotator from the full text. Judge each "
            "feature for the whole work.\n\nFeatures:\n" +
            _schema_block([f for f in feats if f.scope == "work"]) +
            "\n\nReturn {\"values\": {feature_id: value}, \"notes\": one sentence on anything the "
            "cards could not settle}."
        )
    raise AtlasError(f"unknown step {step!r}")


def prompt_version(system_text: str) -> str:
    return f"{ATLAS_VERSION}:{_sha256_bytes(system_text.encode('utf-8'))[:16]}"


def _plan_features(plan: dict) -> list[Feature]:
    return [Feature(**{**f, "options": tuple(f["options"])}) for f in plan["features"]]


def step_requests(run: Path, plan: dict, step: str, model: str | None = None) -> tuple[str, list[dict]]:
    """(system_text, requests) for a step. Each request carries its own
    custom_id, a `unit` locator, and batch params."""
    feats = _plan_features(plan)
    schema = load_atlas_schema()
    sys_text = system_prompt(step, feats, schema["card_fields"])
    mdl, effort, answer, thinking = STEP_DEFAULTS[step]
    mdl = model or mdl
    if mdl not in PRICES:
        raise AtlasError(f"no price record for model {mdl!r}")
    reqs: list[dict] = []

    def params(user_text: str) -> dict:
        return {
            "model": mdl,
            "max_tokens": MAX_TOKENS,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
            "system": [{"type": "text", "text": sys_text, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user_text}],
        }

    for w in plan["works"]:
        text = _read_work(run, w["work_id"])
        if _sha256_bytes(text.encode("utf-8")) != w.get("text_sha256"):
            raise AtlasError("work text differs from plan; use a new run directory")
        head = f"Work: {w['title']} by {w['author']}."
        if step == "cards":
            n = len(w["chapters"])
            for c in w["chapters"]:
                body = text[c["start"]:c["end"]]
                reqs.append({"custom_id": f"cards-{w['work_id']}-c{c['index']:03d}",
                             "unit": {"work_id": w["work_id"], "chapter": c["index"]},
                             "params": params(f"{head} Chapter unit {c['index'] + 1} of {n}.\n\n{body}")})
        elif step in ("features", "gold"):
            idxs = w["sample"] if step == "features" else w["gold"]
            segs = w["segmentation"]["segments"]
            for i in idxs:
                s, e = w["segment_spans"][i]
                reqs.append({"custom_id": f"{step}-{w['work_id']}-s{i:03d}",
                             "unit": {"work_id": w["work_id"], "segment": i,
                                      "content_sha256": segs[i]["content_sha256"]},
                             "params": params(f"{head} Passage {i + 1} of {len(segs)}.\n\n{text[s:e]}")})
        elif step == "works":
            cards = {(r["unit"]["work_id"], r["unit"]["chapter"]): r
                     for r in _parsed_rows(run, "cards")}
            lines = []
            for c in w["chapters"]:
                r = cards.get((w["work_id"], c["index"]))
                card = ((r or {}).get("parsed") or {}).get("card")
                if not r or r["type"] != "succeeded" or not isinstance(card, dict) or set(card) != set(schema["card_fields"]):
                    raise AtlasError("works needs a successful complete card for every chapter")
                lines.append(json.dumps({"chapter": c["index"] + 1, "card": card}, ensure_ascii=False))
            reqs.append({"custom_id": f"works-{w['work_id']}",
                         "unit": {"work_id": w["work_id"]},
                         "params": params(f"{head} {len(lines)} chapter cards follow.\n\n" + "\n".join(lines))})
    return sys_text, reqs


def estimate_step(run: Path, plan: dict, step: str, model: str | None = None) -> dict:
    mdl, _effort, answer, thinking = STEP_DEFAULTS[step]
    mdl = model or mdl
    if step == "works" and not (run / "results" / "cards.jsonl").exists():
        # Cards do not exist yet: estimate each work's card text from chapter count.
        feats = _plan_features(plan)
        sys_text = system_prompt(step, feats, load_atlas_schema()["card_fields"])
        n_ch = sum(len(w["chapters"]) for w in plan["works"])
        user_tokens = n_ch * STEP_DEFAULTS["cards"][2]
        n = len(plan["works"])
    else:
        sys_text, reqs = step_requests(run, plan, step, mdl)
        user_tokens = sum(estimate_tokens(len(r["params"]["messages"][0]["content"])) for r in reqs)
        n = len(reqs)
    prefix = estimate_tokens(len(sys_text))
    cost = step_cost(mdl, user_tokens, prefix, n, n * (answer + thinking))
    return {"step": step, "model": mdl, "n_requests": n, "user_tokens": user_tokens,
            "prefix_tokens": prefix, **cost}


def cmd_build(args: argparse.Namespace) -> int:
    run: Path = args.run
    if args.step in _state(run)["steps"] or (run / "results" / f"{args.step}.jsonl").exists():
        raise AtlasError("step already started; use a new run directory to rebuild")
    plan = _read_json(run / "plan.json")
    if args.step == "works" and not (run / "results" / "cards.jsonl").exists():
        raise AtlasError("works needs collected cards; submit and collect cards first")
    sys_text, reqs = step_requests(run, plan, args.step, args.model)
    path = run / "requests" / f"{args.step}.jsonl"
    _write_jsonl(path, reqs)
    est = estimate_step(run, plan, args.step, args.model)
    meta = {"step": args.step, "prompt_version": prompt_version(sys_text),
            "plan_sha256": _sha256_bytes((run / "plan.json").read_bytes()),
            "model": reqs[0]["params"]["model"] if reqs else None,
            "n_requests": len(reqs), "requests_sha256": _sha256_bytes(path.read_bytes()),
            "estimate": est}
    _write_json(run / "requests" / f"{args.step}.meta.json", meta)
    _log(f"{args.step}: {len(reqs)} requests, prompt {meta['prompt_version']}, "
         f"expected ${est['expected_usd']:.2f}, ceiling ${est['ceiling_usd']:.2f}")
    return 0


# ------------------------------------------------ submit and collect

def _state(run: Path) -> dict:
    p = run / "state.json"
    return _read_json(p) if p.exists() else {"steps": {}}


def committed_usd(run: Path) -> float:
    """Actual cost of collected steps plus the ceiling of submitted,
    uncollected ones: the most this run may already have spent."""
    total = 0.0
    for step, s in _state(run)["steps"].items():
        if s.get("collected"):
            total += actual_cost(_read_jsonl(run / "results" / f"{step}.jsonl"))["usd"]
        else:
            total += s["ceiling_usd"]
    return total


def _verified_meta(run: Path, step: str) -> dict:
    meta_p = run / "requests" / f"{step}.meta.json"
    if not meta_p.exists():
        raise AtlasError(f"no built requests for {step}; run build first")
    meta = _read_json(meta_p)
    req_p = run / "requests" / f"{step}.jsonl"
    if _sha256_bytes(req_p.read_bytes()) != meta["requests_sha256"]:
        raise AtlasError(f"{step}: request file changed after build; rebuild")
    if _sha256_bytes((run / "plan.json").read_bytes()) != meta.get("plan_sha256"):
        raise AtlasError("plan changed after build; use a new run directory")
    state = _state(run)["steps"].get(step)
    if state and state.get("requests_sha256") != meta["requests_sha256"]:
        raise AtlasError("requests differ from started step; use a new run directory")
    return meta


def check_budget(run: Path, step: str, max_usd: float) -> dict:
    if not math.isfinite(max_usd) or max_usd < 0:
        raise AtlasError("--max-usd must be finite and nonnegative")
    meta = _verified_meta(run, step)
    if step in _state(run)["steps"]:
        raise AtlasError(f"{step} was already submitted; collect it instead")
    already = committed_usd(run)
    ceiling = meta["estimate"]["ceiling_usd"]
    if already + ceiling > max_usd:
        raise AtlasError(
            f"{step}: ceiling ${ceiling:.2f} plus ${already:.2f} already committed exceeds "
            f"--max-usd ${max_usd:.2f}; nothing was sent")
    return meta


def cmd_submit(args: argparse.Namespace) -> int:
    run: Path = args.run
    meta = check_budget(run, args.step, args.max_usd)
    import anthropic  # deferred: only the network steps need the SDK

    reqs = _read_jsonl(run / "requests" / f"{args.step}.jsonl")
    client = anthropic.Anthropic()
    batch = client.messages.batches.create(
        requests=[{"custom_id": r["custom_id"], "params": r["params"]} for r in reqs])
    st = _state(run)
    st["steps"][args.step] = {"batch_id": batch.id, "n_requests": len(reqs),
                              "requests_sha256": meta["requests_sha256"],
                              "prompt_version": meta["prompt_version"],
                              "ceiling_usd": meta["estimate"]["ceiling_usd"],
                              "submitted_unix": int(time.time()), "collected": False}
    _write_json(run / "state.json", st)
    _log(f"{args.step}: submitted batch {batch.id} ({len(reqs)} requests)")
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    run: Path = args.run
    _verified_meta(run, args.step)
    st = _state(run)
    s = st["steps"].get(args.step)
    if not s:
        raise AtlasError(f"{args.step} was never submitted")
    import anthropic

    client = anthropic.Anthropic()
    while True:
        b = client.messages.batches.retrieve(s["batch_id"])
        c = b.request_counts
        _log(f"{args.step}: {b.processing_status} processing={c.processing} "
             f"succeeded={c.succeeded} errored={c.errored} expired={c.expired}")
        if b.processing_status == "ended":
            break
        if not args.wait:
            return 3
        time.sleep(args.poll_seconds)
    units = {r["custom_id"]: r["unit"] for r in _read_jsonl(run / "requests" / f"{args.step}.jsonl")}
    rows = []
    for res in client.messages.batches.results(s["batch_id"]):
        row: dict[str, Any] = {"custom_id": res.custom_id, "unit": units.get(res.custom_id),
                               "type": res.result.type}
        if res.result.type == "succeeded":
            msg = res.result.message
            u = msg.usage
            row.update({
                "model": msg.model,
                "stop_reason": msg.stop_reason,
                "text": "".join(b.text for b in msg.content if b.type == "text"),
                "usage": {
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                    "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                },
            })
        rows.append(row)
    rows.sort(key=lambda r: r["custom_id"])
    _validate_result_rows(run, args.step, rows)
    _write_jsonl(run / "results" / f"{args.step}.jsonl", rows)
    s["collected"] = True
    _write_json(run / "state.json", st)
    cost = actual_cost(rows)
    _log(f"{args.step}: {len(rows)} results, ${cost['usd']:.2f} actual")
    return 0


# ------------------------------------------- headless (subscription)

HEADLESS = "claude-code"
_HEADLESS_FLAGS = ("--safe-mode", "--tools", "", "--strict-mcp-config",
                   "--no-session-persistence", "--output-format", "json")
_LIMIT_MARKERS = ("usage limit", "rate limit", "rate_limit", "overloaded")
# Answers received per request before an unusable one is left for the operator
# instead of re-sent, so a request that fails the same way cannot spend quota on
# every rerun. Errors that returned no answer (limits, timeouts) do not count.
MAX_ATTEMPTS = 3


def headless_argv(claude: str, model: str, effort: str, system_file: Path) -> list[str]:
    return [claude, "-p", "--model", model, "--effort", effort,
            "--system-prompt-file", str(system_file), *_HEADLESS_FLAGS]


def parse_headless(stdout: str, model: str) -> dict:
    """A results row body from ``claude -p --output-format json`` output.

    The answering model comes from ``modelUsage``; Claude Code may make a
    small side call on another model, so the requested model must be among
    the entries or the row is an error (a fallback model is not the judge
    this step declared).
    """
    try:
        obj = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return {"type": "errored", "error": f"unparseable CLI output: {stdout[:300]!r}"}
    if not isinstance(obj, dict):
        return {"type": "errored", "error": "CLI output is not a result object"}
    if obj.get("is_error") or obj.get("subtype") != "success":
        detail = obj.get("api_error_status") or obj.get("result") or obj.get("subtype")
        return {"type": "errored", "error": str(detail)[:500]}
    used = obj.get("modelUsage", {})
    if not isinstance(used, dict) or any(not isinstance(u, dict) for u in used.values()):
        return {"type": "errored", "error": "CLI model usage is malformed"}
    answered = next((m for m, u in used.items()
                     if model in (m, u.get("canonicalModel"))), None)
    if answered is None:
        return {"type": "errored", "error": f"answered by {sorted(used)}, not {model}"}
    u = obj.get("usage", {})
    if not isinstance(u, dict) or any(type(u.get(k, 0)) is not int or u.get(k, 0) < 0 for k in
                                    ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")):
        return {"type": "errored", "error": "CLI token usage is malformed"}
    if not isinstance(obj.get("result"), str):
        return {"type": "errored", "error": "CLI result text is malformed"}
    cost = obj.get("total_cost_usd")
    if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
        return {"type": "errored", "error": "CLI cost is malformed"}
    return {
        "type": "succeeded",
        "model": answered,
        "stop_reason": obj.get("stop_reason"),
        "text": obj.get("result") or "",
        "usage": {k: int(u.get(k) or 0) for k in
                  ("input_tokens", "output_tokens",
                   "cache_creation_input_tokens", "cache_read_input_tokens")},
        "transport": HEADLESS,
        "list_usd": obj.get("total_cost_usd"),
    }


def _limit_hit(row: dict) -> bool:
    err = str(row.get("error", "")).lower()
    return row["type"] == "errored" and (
        err.startswith(("429", "529")) or any(m in err for m in _LIMIT_MARKERS))


def _unusable_reason(step: str, row: dict | None, card_fields: dict) -> str | None:
    """None for a result later steps can use: it succeeded, its answer
    parses, and a chapter card carries every card field (works refuses
    anything less). Otherwise, why not. headless reruns rows that fail this,
    so one malformed answer cannot strand a run that is not allowed to be
    rebuilt."""
    if not row:
        return "not run"
    if row.get("type") != "succeeded":
        return "errored"
    parsed = extract_json(row.get("text", ""))
    if parsed is None:
        return "answer has no JSON object"
    if step == "cards":
        card = parsed.get("card")
        if not isinstance(card, dict):
            return "answer has no card object"
        missing, extra = set(card_fields) - set(card), set(card) - set(card_fields)
        if missing or extra:
            return f"card fields differ (missing {sorted(missing)}, extra {sorted(extra)})"
    return None


def _spend_records(row: dict | None) -> list[dict]:
    """Every answered attempt a result row accounts for: the attempts it
    superseded, then itself. A rerun must never drop what an earlier answer
    spent, and the attempt cap counts these."""
    if not row:
        return []
    recs = list(row.get("superseded") or [])
    if row.get("usage"):
        recs.append(row)
    return recs


def _supersede(prev: dict | None, row: dict) -> dict:
    """``row`` carrying forward the spend records of the row it replaces."""
    recs = [{k: r.get(k) for k in ("model", "transport", "stop_reason", "usage", "list_usd")}
            for r in _spend_records(prev)]
    return {**row, "superseded": recs} if recs else row


def cmd_headless(args: argparse.Namespace) -> int:
    run: Path = args.run
    step = args.step
    meta = _verified_meta(run, step)
    st = _state(run)
    s = st["steps"].get(step)
    if s and s.get("transport") != HEADLESS:
        raise AtlasError(f"{step} was submitted as a batch; collect it instead")
    if args.max_attempts < 1:
        raise AtlasError("--max-attempts must be at least 1")
    claude = shutil.which(args.claude)
    if not claude:
        raise AtlasError(f"no claude CLI found as {args.claude!r}")
    version = subprocess.run([claude, "--version"], capture_output=True, text=True,
                             encoding="utf-8", timeout=120).stdout.strip()
    if not version:
        raise AtlasError(f"{claude} --version printed nothing")
    if s and s["claude_code"] != version:
        raise AtlasError(f"{step} started under {s['claude_code']!r}, now {version!r}; "
                         f"delete results/{step}.jsonl and its state entry to restart it")
    reqs = _read_jsonl(run / "requests" / f"{step}.jsonl")
    sys_file = (run / "requests" / f"{step}.system.txt").resolve()
    sys_file.write_bytes(reqs[0]["params"]["system"][0]["text"].encode("utf-8"))
    cwd = run / "headless-cwd"  # empty, so no project files sit beside the call
    cwd.mkdir(exist_ok=True)
    res_p = run / "results" / f"{step}.jsonl"
    previous = _read_jsonl(res_p)
    _validate_result_rows(run, step, previous)
    done = {r["custom_id"]: r for r in previous}
    card_fields = load_atlas_schema()["card_fields"]

    def usable(cid: str) -> bool:
        return _unusable_reason(step, done.get(cid), card_fields) is None

    def capped(cid: str) -> bool:
        return not usable(cid) and len(_spend_records(done.get(cid))) >= args.max_attempts

    todo = []
    for r in reqs:
        cid = r["custom_id"]
        if capped(cid):
            _log(f"{cid}: {_unusable_reason(step, done[cid], card_fields)} after "
                 f"{len(_spend_records(done[cid]))} answers; not re-sent (--max-attempts "
                 f"{args.max_attempts})")
        elif not usable(cid):
            todo.append(r)
    if args.limit:
        todo = todo[:args.limit]
    st["steps"][step] = {"transport": HEADLESS, "claude_code": version,
                         "requests_sha256": meta["requests_sha256"],
                         "n_requests": len(reqs), "prompt_version": meta["prompt_version"],
                         "ceiling_usd": 0.0, "collected": False}
    _write_json(run / "state.json", st)
    _log(f"{step}: {len(todo)} of {len(reqs)} requests to run through {version}")

    def one(r: dict) -> dict:
        p = r["params"]
        argv = headless_argv(claude, p["model"], p["output_config"]["effort"], sys_file)
        try:
            # Bytes both ways: text-mode pipes would turn LF into CRLF on Windows
            # and change the prompt the recorded prompt_version describes.
            proc = subprocess.run(argv, input=p["messages"][0]["content"].encode("utf-8"),
                                  capture_output=True, cwd=cwd, timeout=args.timeout)
            body = parse_headless(proc.stdout.decode("utf-8", errors="replace"), p["model"])
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            if body["type"] == "errored" and err:
                body["error"] += f" | stderr: {err[:300]}"
        except subprocess.TimeoutExpired:
            body = {"type": "errored", "error": f"timed out after {args.timeout}s"}
        return {"custom_id": r["custom_id"], "unit": r["unit"],
                "transport": HEADLESS, **body}

    stopped = None
    seen = good = 0
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        futs = [ex.submit(one, r) for r in todo]
        for fut in as_completed(futs):
            if fut.cancelled():
                continue
            row = fut.result()
            cid = row["custom_id"]
            row = done[cid] = _supersede(done.get(cid), row)
            _write_jsonl(res_p, sorted(done.values(), key=lambda x: x["custom_id"]))
            seen += 1
            good += row["type"] == "succeeded"
            if row["type"] != "succeeded":
                _log(f"{cid}: {row['error']}")
            elif not usable(cid):
                _log(f"{cid}: answer {len(_spend_records(row))} of at most "
                     f"{args.max_attempts} unusable: {_unusable_reason(step, row, card_fields)}")
            if stopped is None and _limit_hit(row):
                stopped = f"a usage or rate limit ({row['error']})"
            elif stopped is None and seen >= 3 and good == 0:
                stopped = f"the first three calls all failed ({row['error']})"
            if stopped:
                for f in futs:
                    f.cancel()
    ok = sum(1 for r in reqs if usable(r["custom_id"]))
    n_capped = sum(1 for r in reqs if capped(r["custom_id"]))
    st["steps"][step]["collected"] = ok == len(reqs)
    _write_json(run / "state.json", st)
    _log(f"{step}: {ok} of {len(reqs)} usable"
         + (f"; {n_capped} left unusable at --max-attempts {args.max_attempts}" if n_capped else ""))
    if stopped:
        raise AtlasError(f"{step}: stopped on {stopped}; "
                         f"rerun the same command to resume once it is fixed")
    return 0 if ok == len(reqs) else 3


def actual_cost(rows: list[dict]) -> dict:
    """USD billed through the API, plus what subscription-carried rows would
    have cost at list price (reported, never billed). Answers a headless
    rerun superseded are counted: they were spent even though unused."""
    usd = 0.0
    list_usd = 0.0
    tok = Counter()
    for r in (rec for row in rows for rec in _spend_records(row)):
        u = r["usage"]
        tok.update(u)
        if r.get("transport") == HEADLESS:
            list_usd += r.get("list_usd") or 0.0
            continue
        p = PRICES.get(r.get("model", ""), PRICES["claude-opus-5-5"])
        usd += (u["input_tokens"] * p["input"]
                + u["cache_creation_input_tokens"] * p["input"] * CACHE_WRITE_MULT
                + u["cache_read_input_tokens"] * p["input"] * CACHE_READ_MULT
                + u["output_tokens"] * p["output"]) / 1e6 * BATCH_DISCOUNT
    return {"usd": round(usd, 4), "subscription_list_usd": round(list_usd, 4), "tokens": dict(tok),
            "superseded_answers": sum(len(row.get("superseded") or []) for row in rows)}


# --------------------------------------------------------------- emit

def extract_json(text: str) -> dict | None:
    """The outermost JSON object in a reply, or None."""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    cand = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    try:
        obj = json.loads(cand)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def validate_values(values: Any, feats: list[Feature]) -> tuple[dict, list[str]]:
    """Keep only in-vocabulary answers; anything else becomes None."""
    out: dict[str, Any] = {}
    warns: list[str] = []
    values = values if isinstance(values, dict) else {}
    for f in feats:
        v = values.get(f.id)
        if f.kind == "multi":
            if isinstance(v, list) and all(isinstance(x, str) and x in f.options for x in v):
                out[f.id] = sorted(set(v), key=f.options.index)
            else:
                out[f.id] = None
                warns.append(f"{f.id}: invalid {v!r}")
        else:
            v = str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
            if isinstance(v, str) and v in f.options:
                out[f.id] = v
            else:
                out[f.id] = None
                warns.append(f"{f.id}: invalid {v!r}")
    return out, warns


def _validate_result_rows(run: Path, step: str, rows: list[dict]) -> None:
    if not rows:
        return
    _verified_meta(run, step)
    requests = {r["custom_id"]: r for r in _read_jsonl(run / "requests" / f"{step}.jsonl")}
    seen = set()
    transport = _state(run)["steps"].get(step, {}).get("transport")
    for row in rows:
        cid = row.get("custom_id") if isinstance(row, dict) else None
        if not isinstance(cid, str) or cid not in requests or cid in seen:
            raise AtlasError("results contain duplicate or unknown request IDs")
        seen.add(cid)
        request = requests[cid]
        if row.get("unit") != request["unit"]:
            raise AtlasError("result unit differs from its built request")
        if row.get("transport") != transport:
            raise AtlasError("result transport differs from its recorded step")
        if row.get("type") == "succeeded":
            model = row.get("model")
            requested = request["params"]["model"]
            if not isinstance(model, str) or not (model == requested or model.startswith(requested + "-")):
                raise AtlasError("result model differs from its built request")


def _parsed_rows(run: Path, step: str) -> list[dict]:
    rows = []
    raw_rows = _read_jsonl(run / "results" / f"{step}.jsonl")
    _validate_result_rows(run, step, raw_rows)
    for r in raw_rows:
        r = dict(r)
        r["parsed"] = extract_json(r.get("text", "")) if r["type"] == "succeeded" else None
        rows.append(r)
    return rows


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if pe == 1.0:
        return None
    return (po - pe) / (1 - pe)


def agreement(sonnet: dict[str, dict], opus: dict[str, dict], feats: list[Feature],
              min_pairs: int = 8) -> list[dict]:
    """Per-feature Sonnet/Opus agreement on the segments both judged.

    Single-select: percent agreement and Cohen's kappa. Multi-select: mean
    Jaccard. routing_hint is a suggestion for which model carries a feature;
    it reads only agreement, never any feature's value.
    """
    keys = sorted(set(sonnet) & set(opus))
    rows = []
    for f in feats:
        if f.scope != "segment":
            continue
        pairs = [(sonnet[k][f.id], opus[k][f.id]) for k in keys
                 if sonnet[k].get(f.id) is not None and opus[k].get(f.id) is not None]
        row: dict[str, Any] = {"feature_id": f.id, "origin": f.origin, "n_pairs": len(pairs)}
        if f.kind == "single":
            a, b = [p[0] for p in pairs], [p[1] for p in pairs]
            row["percent_agreement"] = round(sum(x == y for x, y in pairs) / len(pairs), 3) if pairs else None
            k = cohen_kappa(a, b)
            row["kappa"] = round(k, 3) if k is not None else None
            score = row["kappa"]
        else:
            jac = [len(set(x) & set(y)) / len(set(x) | set(y)) if (x or y) else 1.0 for x, y in pairs]
            row["mean_jaccard"] = round(sum(jac) / len(jac), 3) if jac else None
            score = row["mean_jaccard"]
        if len(pairs) < min_pairs or score is None:
            row["routing_hint"] = "insufficient_pairs"
        elif score >= 0.6:
            row["routing_hint"] = "sonnet"
        elif score >= 0.4:
            row["routing_hint"] = "review_definition"
        else:
            row["routing_hint"] = "opus_or_redefine"
        rows.append(row)
    return rows


def cmd_emit(args: argparse.Namespace) -> int:
    run: Path = args.run
    plan = _read_json(run / "plan.json")
    feats = _plan_features(plan)
    seg_feats = [f for f in feats if f.scope == "segment"]
    work_feats = [f for f in feats if f.scope == "work"]
    core_ids = {f.key for f in nfs.CORE_FEATURES}
    out = run / "out"
    # Validate every present result set before replacing derived products.
    for step in STEPS:
        _parsed_rows(run, step)
    # An empty/current result set must retire an earlier successful export.
    for step in ("features", "gold"):
        _write_json(out / f"manifest-core-{step}.json", {})
    _write_json(out / "agreement.json", [])
    _write_jsonl(out / "cards.jsonl", [])
    all_rows: list[dict] = []
    by_step: dict[str, dict[str, dict]] = {}
    cost: dict[str, dict] = {}
    for step in STEPS:
        rows = _parsed_rows(run, step)
        if not rows:
            continue
        meta = _verified_meta(run, step)
        pv = meta.get("prompt_version")
        ran = _state(run)["steps"].get(step, {})
        if ran.get("transport") == HEADLESS and pv:
            pv = f"{pv};{HEADLESS}/{ran['claude_code']}"
        requested_model = meta.get("model")
        cost[step] = actual_cost(rows)
        if step == "cards":
            _write_jsonl(out / "cards.jsonl", [
                {"unit": r["unit"], "model": r.get("model"),
                 "card": (r["parsed"] or {}).get("card"), "ok": bool(r["parsed"])} for r in rows])
            continue
        use = seg_feats if step in ("features", "gold") else work_feats
        keyed: dict[str, dict] = {}
        for r in rows:
            parsed = r["parsed"] or {}
            vals, warns = validate_values(parsed.get("values"), use)
            rec = {"step": step, "unit": r["unit"], "model": r.get("model"), "prompt_version": pv,
                   "result_type": r["type"], "stop_reason": r.get("stop_reason"),
                   "values": vals, "warnings": warns}
            if step == "gold":
                rec["evidence"] = parsed.get("evidence") if isinstance(parsed.get("evidence"), dict) else {}
            if step == "works":
                rec["notes"] = parsed.get("notes")
            all_rows.append(rec)
            if step in ("features", "gold") and r["type"] == "succeeded":
                by_step.setdefault(step, {})[r["unit"]["content_sha256"]] = vals
                core_vals = {k: v for k, v in vals.items() if k in core_ids and v is not None}
                if len(core_vals) == len(core_ids):
                    keyed[r["unit"]["content_sha256"]] = {
                        "values": core_vals,
                        "judge_identity": {"model": requested_model,
                                           "model_revision": r.get("model"),
                                           "prompt_version": pv},
                    }
        if keyed:
            _write_json(out / f"manifest-core-{step}.json", keyed)
    _write_jsonl(out / "features.jsonl", all_rows)
    if "features" in by_step and "gold" in by_step:
        _write_json(out / "agreement.json", agreement(by_step["features"], by_step["gold"], seg_feats))
    total = round(sum(c["usd"] for c in cost.values()), 4)
    sub = round(sum(c["subscription_list_usd"] for c in cost.values()), 4)
    _write_json(out / "cost.json", {"per_step": cost, "total_usd": total,
                                    "subscription_list_usd": sub})
    _log(f"emit: {len(all_rows)} judged units, billed ${total:.2f}, "
         f"subscription list-price equivalent ${sub:.2f}; see {out}")
    return 0


# ---------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="storyscope_atlas", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def with_run(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sp.add_argument("--run", type=Path, required=True, help="run directory")
        return sp

    f = with_run(sub.add_parser("fetch", help="download Project Gutenberg ebooks"))
    f.add_argument("--pilot", action="store_true", help="the four pilot novels")
    f.add_argument("--ebook", type=int, action="append", default=[], help="a PG ebook number")
    f.set_defaults(func=cmd_fetch)

    a = with_run(sub.add_parser("add-local", help="add a local public-domain text"))
    a.add_argument("--file", required=True)
    a.add_argument("--work-id", required=True)
    a.add_argument("--title", default=None)
    a.add_argument("--author", default=None)
    a.add_argument("--attest-public-domain", action="store_true")
    a.set_defaults(func=cmd_add_local)

    pl = with_run(sub.add_parser("plan", help="chapters, segments, sample, counts, estimates"))
    pl.add_argument("--taxonomy", required=True, help="StoryScope data/taxonomy.json")
    pl.add_argument("--segment-target-words", type=int, default=nls.DEFAULT_TARGET_WORDS)
    pl.add_argument("--sample-per-work", type=int, default=6)
    pl.add_argument("--gold-per-work", type=int, default=4)
    pl.set_defaults(func=cmd_plan)

    b = with_run(sub.add_parser("build", help="write batch requests for a step"))
    b.add_argument("--step", choices=STEPS, required=True)
    b.add_argument("--model", default=None, help="override the step's default model")
    b.set_defaults(func=cmd_build)

    s = with_run(sub.add_parser("submit", help="send a step's batch"))
    s.add_argument("--step", choices=STEPS, required=True)
    s.add_argument("--max-usd", type=float, required=True,
                   help="refuse above estimated allowance (input tokens/prices are estimates, not a billing cap)")
    s.set_defaults(func=cmd_submit)

    c = with_run(sub.add_parser("collect", help="download a step's results"))
    c.add_argument("--step", choices=STEPS, required=True)
    c.add_argument("--wait", action="store_true", help="poll until the batch ends")
    c.add_argument("--poll-seconds", type=int, default=60)
    c.set_defaults(func=cmd_collect)

    h = with_run(sub.add_parser("headless", help="run a step through claude -p on a subscription"))
    h.add_argument("--step", choices=STEPS, required=True)
    h.add_argument("--claude", default="claude", help="the Claude Code executable")
    h.add_argument("--parallel", type=int, default=4, help="concurrent claude processes")
    h.add_argument("--limit", type=int, default=0, help="run at most N pending requests")
    h.add_argument("--timeout", type=int, default=900, help="seconds per request")
    h.add_argument("--max-attempts", type=int, default=MAX_ATTEMPTS,
                   help="answers to accept per request before an unusable one stops being re-sent "
                        "(errors without an answer, such as a usage limit, do not count)")
    h.set_defaults(func=cmd_headless)

    e = with_run(sub.add_parser("emit", help="manifests, feature table, agreement, cost"))
    e.set_defaults(func=cmd_emit)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AtlasError as exc:
        _log(f"refused: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
