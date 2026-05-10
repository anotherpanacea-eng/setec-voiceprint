# Personal pre-AI baseline corpus: provenance template

A walkthrough for collecting and labeling your **own** pre-AI prose
corpus — the irreducible piece of the SETEC framework that has to
come from the user, not the internet.

## Why this matters

SETEC's most diagnostic comparison is the writer's draft against
their own pre-AI baseline. The framework can borrow corpora from
HuggingFace (Pangram EditLens via `fetch_pangram_editlens.py`),
Project Gutenberg (public-domain native-fluent prose), or PAN
authorship corpora — but those serve as *impostor pools* and
*calibration anchors*, not as the writer's identity baseline. The
identity baseline has to be the writer's own prose, written before
any AI involvement, with documented provenance.

This template tells you how to assemble that corpus.

## What you're building

A directory of plain-text files plus a `corpus_manifest.jsonl`
entry per file. The framework's voice-coherence and validation
surfaces consume this.

```
ai-prose-baselines-private/
├── <yourname>/
│   ├── 2018-blog-essay-on-attention.txt
│   ├── 2019-fiction-chapter-12.txt
│   ├── 2020-academic-paper-published.txt
│   └── ...
└── corpus_manifest.jsonl
```

`ai-prose-baselines-private/` is a **gitignored sibling** of the
SETEC repo (or anywhere you control with that directory name in
the path). The marker-based privacy guard in every framework script
refuses to write voiceprint outputs anywhere else without an
explicit `--allow-public-output` flag.

## Step 1: Gather the source files

Pull together pre-AI-era prose you wrote yourself:

- **Blog posts** from your own site, Substack, Medium, or
  WordPress export. The acquisition tools shipped 1.15.0 / 1.17.0 /
  1.19.0 (`acquire_blog.py`, `acquire_blogger_takeout.py`,
  `acquire_magazine.py`) handle the export-and-clean step.
- **Manuscripts** in any format you can read — `pdf_inventory.py`
  + `pdf_extract.py` (1.18.0) handle the PDF library; for `.docx`
  use Pandoc or LibreOffice to convert to Markdown / plain text.
- **Email drafts**, **journal entries**, **published articles**,
  **conference papers** — anything substantial enough to count as
  prose (≥ 200 words minimum, ≥ 500 ideal).
- **Personal letters** if they're prose-shaped (not just "see
  attached").

The cutoff for pre-AI: your own affirmative judgment. ChatGPT's
public release was November 2022, but adoption was uneven. If you
started using AI assistance in March 2023, every piece written
before that date is unambiguously pre-AI. If you can't remember,
treat the boundary date as `ai_status: unknown` rather than
`pre_ai_human` — the validator's `language_status` and
`ai_status` ratchets will protect you from contaminating your
baseline.

## Step 2: Write per-file manifest entries

Each file gets a JSONL entry. Required fields per the schema in
`references/manifest-schema.md`:

| Field | Value | Notes |
|---|---|---|
| `id` | stable unique handle | filename stem usually fits |
| `path` | absolute or relative to manifest | must resolve to an existing file |
| `ai_status` | `pre_ai_human` | the load-bearing field for identity-baseline |
| `use` | `["baseline", "voice_profile"]` | what this entry feeds downstream |

Recommended fields:

| Field | Value | Why |
|---|---|---|
| `corpus_role` | `identity_baseline` | the new (1.14.3+) field that distinguishes your baseline from impostor pools |
| `author` | your name (or pen name) | informational; useful for multi-author manifests |
| `persona` | a slug like `blog`, `fiction`, `academic` | one author can have multiple distinguishable voices; persona slices the manifest by voice |
| `register` | `blog_essay`, `literary_fiction`, `academic_philosophy`, `personal`, `policy_advocacy`, `literary_horror`, `testimony_policy` | the comparison bucket |
| `genre` | narrower than register | optional |
| `date_written` | `YYYY-MM-DD` or `YYYY-MM` or `YYYY` | required for `voice_drift_tracker.py`'s period grouping |
| `editing_status` | `raw_draft`, `revised_human`, `published_cleaned`, `coauthored` | sanity-checked against `ai_status` |
| `word_count` | integer | informational |
| `split` | `baseline` | identity baseline, NOT validation |
| `privacy` | `private` | voiceprint-tagged entries; the validator ratchet enforces this |
| `language_status` | `native` (or honest non-native label) | ESL ratchet protection |
| `era` | `pre_chatgpt` | finer than ai_status; useful for impostor calibration cross-checks |
| `notes` | free-text | "transcribed from notebook," "co-edited with [editor]," etc. |

**Example entry:**

```json
{"id":"essay_2018_03_voice_first","path":"essays/2018-03-voice-first.md","author":"Jane Q. Author","persona":"blog","register":"blog_essay","date_written":"2018-03-14","ai_status":"pre_ai_human","editing_status":"published_cleaned","word_count":1850,"use":["baseline","voice_profile"],"split":"baseline","privacy":"private","corpus_role":"identity_baseline","language_status":"native","era":"pre_chatgpt"}
```

## Step 3: Validate the manifest

```bash
python3 scripts/manifest_validator.py corpus_manifest.jsonl
```

The validator catches:

- **Missing required fields** (error)
- **Path-resolution failures** (error) — the `path` doesn't exist
- **Duplicate ids** (error)
- **Use / split contradictions** (error) — `use: validation` + `split: baseline`
- **Voiceprint privacy ratchet** (warning) — `voice_profile` entries with `privacy != private`
- **ESL ratchet** (warning) — non-native language status in baseline / voice_profile
- **AI-status / editing-status sanity** (warning) — `pre_ai_human` + `editing_status: coauthored` is contradictory
- **Era recommendation** (warning) — identity-baseline entries with impostor-relevant `use` tags benefit from an explicit `era`

## Step 4: Run the corpus-hygiene gate

```bash
python3 scripts/check_corpus.py --manifest corpus_manifest.jsonl
```

Catches non-prose contamination the schema can't see — CSS / HTML
blocks in WordPress exports, fenced code blocks in Markdown, ASCII
tables, JSON dumps. Two-thirds of voice-distance disputes the
framework has surfaced empirically traced to one of these. The 2026-
05-08 calibration session found a single CSS-contaminated WordPress
post showed KL = 0.41 against the same essay's clean version's KL
of 0.10 — a 4× false signal that vanishes after the gate runs.

## Step 5: Build the voice profile

```bash
python3 scripts/voice_profile.py \
    --manifest corpus_manifest.jsonl \
    --use voice_profile \
    --persona <slug> \
    --register <register> \
    --out ../ai-prose-baselines-private/voice_profile_<slug>.md \
    --json-out ../ai-prose-baselines-private/voice_profile_<slug>.json
```

This is your durable voiceprint. The JSON output feeds:

- `voice_distance.py` — measure draft-vs-baseline distance.
- `voice_drift_tracker.py` — track voice change across periods.
- `pov_voice_profile.py` — per-POV slicing for fiction.
- `idiolect_detector.py` — preservation lists.
- `generate_voice_report.py` — author-facing markdown reports.

## Step 6: Decide what counts as the baseline vs. what counts as drift

A common mistake: adding everything you've ever written to one
manifest with `use: voice_profile` and treating it all as "the
baseline." That's the smoothing-diagnosis equivalent of mixing
your training and test sets — anything you write in the future
will look "in distribution" because the baseline has already
absorbed all the variance.

The honest split:

- **Baseline:** prose written before whatever AI-availability
  boundary you care about. Tag `era: pre_chatgpt` AND
  `ai_status: pre_ai_human` AND `corpus_role: identity_baseline`.
- **Out-of-baseline (recent writing):** prose written after the
  boundary, *not* added to the baseline manifest. Use
  `voice_distance.py` to measure how far each new draft has moved
  from the baseline — that distance is the diagnostic.
- **Drift archive:** prose from intermediate periods, tagged with
  `date_written` and consumed by `voice_drift_tracker.py`. The
  drift tracker disaggregates by period; the baseline-as-fixed-
  reference comparison is what the diagnostic outputs need.

## Step 7: Re-survey on a schedule

Your voice changes. The baseline you build today is the right
reference for drafts you write next week. Six months from now, you
may want to rebuild the voice profile, re-derive idiolect lists,
re-check whether any feature has drifted.

A reasonable cadence:

- **Quarterly:** add new pre-AI-shaped writing to the manifest;
  re-run `voice_profile.py` and `idiolect_detector.py`.
- **On any major register shift** (started writing op-eds; moved
  from fiction to nonfiction; shifted academic field): build a
  fresh persona slice and add `persona: <new_slug>` to the new
  entries. The voice-coherence surface treats each persona as its
  own voiceprint.
- **Whenever the manifest validator's warnings start to cluster:**
  if you keep adding entries that fail the AI-status / editing-
  status sanity check, your "pre-AI" cutoff date is wrong.

## Step 8: Decide on impostor pools

Your identity baseline alone tells you how far each draft has
drifted from your own writing. To make the strong claim "this draft
is *consistent* with you, not just *near* you," you need an
impostor pool — other writers in matched register against whom the
General Imposters method (`general_imposters.py`) compares the
draft.

The acquisition tools build this:

- `acquire_blog.py` — pull a register-matched essayist's blog
  (Substack, WordPress, generic HTML). 1.15.0+.
- `acquire_blogger_takeout.py` — for archived Blogger / Blogspot
  exports. 1.17.0+.
- `acquire_magazine.py` — for literary horror specifically
  (Nightmare, The Dark). 1.19.0+.
- `pdf_inventory.py` + `pdf_extract.py` — for academic-paper
  libraries. 1.18.0+.

Tag every impostor entry with `corpus_role: impostor`,
`impostor_for: ["<your_persona_slug>"]`, `register_match:
{high|medium|low}`, `topic_match: {high|medium|low}`,
`consent_status` (legal posture), `era: pre_chatgpt` (post-AI
prose contaminates the impostor signal), and `acquired_via` (the
acquisition script's tag).

The impostor pool's quality gates: ≥ 5 impostor docs in matched
register (the GI harness's `MIN_IMPOSTORS` floor) is the structural
minimum. 10–20 impostor docs across 3–5 personas is the practical
target for stable bootstrap proportions.

## Common gotchas

- **`pre_ai_human` is your judgment, not the framework's.** The
  validator can't audit when you started using AI; it trusts the
  field you set. Be honest. The cost of an AI-contaminated
  baseline is silent: the framework starts treating your
  smoothing-diagnosis signal as part of your voice.
- **Heavy-edit drafts leak.** If you wrote a post in 2020 but
  rewrote it heavily in 2024 with AI assistance, the file's
  current state is *not* `pre_ai_human` — even though the original
  was. Use the original draft's date AND content. If you don't
  have the original, mark `editing_status: coauthored`;
  `manifest_validator.py`'s sanity ratchet will flag it.
- **One persona per voice, not per project.** If you write a blog
  and a novel and the voices are clearly different, two personas.
  If you write three blogs and the voice is the same, one persona
  with `notes` distinguishing the venues.
- **Privacy is structural, not optional.** Files under
  `ai-prose-baselines-private/` are voice-cloning input. Don't
  symlink them into a public repo. Don't share the `voice_profile.
  json` output. Don't paste a `voice_distance.py` JSON output into
  a public LLM chat. The framework's privacy guard makes you
  intentional about each step; the gitignore reinforces it.
- **The fingerprint risk is real.** The features the framework
  computes (function-word distribution, char-n-grams, idiolect
  phrases) are the same features a voice-cloning attacker would
  use. Treat your voice profile like a password.

## See also

- `references/manifest-schema.md` — canonical schema reference.
- `scripts/README.md` — every script the framework ships.
- `scripts/calibration/PROVENANCE.md` — calibration ledger; this
  template's older sibling (covers per-signal threshold provenance
  rather than identity-baseline corpus assembly).
- `references/calibration-findings-2026-05-10.md` — empirical
  finding that lexical-diversity signals invert polarity when the
  human comparator is ESL student writing. Relevant when picking
  impostor pools — match the comparator's language fluency to
  yours, not just the register.
- `internal/2026-05-08-impostor-corpus-spec.md` — the spec the
  acquisition tools were built from; useful when adapting the
  acquisition pattern to a new source.
