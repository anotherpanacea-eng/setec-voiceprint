# Stylometry scripts

The scripts in this directory split across two task surfaces. Most failure modes come from confusing them.

## Two task surfaces, five scripts

### Surface 1: AI-prose smoothing diagnosis

These scripts ask whether the prose has been smoothed into a narrower-than-typical region of stylometric space. They measure deviation from a *typical human-prose region*, not from a specific writer.

| Script | Scope | Use when |
|---|---|---|
| `variance_audit.py` | Single document | Diagnostic on one chapter or passage |
| `manuscript_audit.py` | Whole manuscript (multi-chapter) | Surfacing manuscript-wide patterns and outlier chapters |
| `repetition_audit.py` | Single document, vocabulary level | Layer A flagged lexical compression and you want specific candidates for restoration |
| `manuscript_repetition_audit.py` | Whole manuscript, vocabulary level | Surfacing dispersed habit-vocabulary that recurs across chapters |
| `chapter_distinctiveness_audit.py` | Whole manuscript, vocabulary level | Surfacing words distinctive to one chapter against the rest of the manuscript (leave-one-out, no external baseline) |

What these scripts cannot answer: who wrote it, whether the smoothing is an artifact of register or scene type, what to revise. The verdict they license is *"this prose shows characteristics of AI smoothing"* — not *"this prose was written by AI."*

### Surface 2: Voice-coherence comparison

These scripts ask how far a target text is from a *specific writer's or register's* baseline. They measure deviation from a writer-shaped reference, not from a typical human-prose region.

| Script | Scope | Use when |
|---|---|---|
| `voice_distance.py` | Single target vs. baseline | Ask how far a draft has drifted from a writer/register voiceprint |
| `voice_profile.py` | Baseline corpus | Produce a private human-readable voiceprint from a corpus |

What these scripts cannot answer: whether the divergence is caused by AI involvement, register shift, time drift, or genuine voice change. The verdict they license is *"this draft has drifted from this baseline by this much"* — not *"AI involvement caused the drift"* and not *"the writer is no longer themselves."*

### Surface tag in script output

Every script's JSON output carries a top-level `task_surface` field, and every markdown report shows the surface near the header. The field tells downstream consumers which question the output is answering. Current values:

| Field value | Scripts |
|---|---|
| `smoothing_diagnosis` | `variance_audit.py`, `manuscript_audit.py`, `repetition_audit.py`, `manuscript_repetition_audit.py`, `chapter_distinctiveness_audit.py` |
| `voice_coherence` | `voice_distance.py`, `voice_profile.py` |
| `validation` | future `validation_harness.py` |
| `craft_restoration` | not a script; lives in `references/aic-flags.md`, `references/source-triage.md`, `references/rhetorical-countermoves.md` |

The contract is enforceable at the data layer. A future validation harness can refuse to mix scores across surfaces because the surfaces answer different questions. Reports are now self-identifying so a reader (or an automated consumer) can route by surface without reading the script's filename or guessing from output shape.

### Why the surfaces are kept distinct

The two surfaces share statistical signals (function-word distributions, lexical diversity, sentence-length variance, syntactic patterns), because RLHF-induced mode collapse, register conventions, and time-stable authorial idiolect all leave traces in the same features. But they answer different questions and license different claims. A single "is this AI" verdict would have to collapse them into one number, which the underlying math does not entitle.

When you have a target document, ask first which question you're trying to answer. If you want to know *whether the prose looks AI-smoothed*, run the audit scripts (Surface 1). If you want to know *whether this draft sounds like the writer*, run the voice scripts (Surface 2). The two surfaces can both run on the same document; their findings should be read separately, not averaged.

A third surface — empirical performance validation against a labeled corpus — is on the roadmap as `validation_harness.py`. Its job will be to report how well these signals discriminate against your labeled data, in your registers, at your text lengths. It produces claims about your corpus, not about the world. The first piece of the validation surface is shipped: `manifest_validator.py` checks the schema and integrity of `corpus_manifest.jsonl` so manifest-consuming tools can trust the manifest before running.

A fourth surface — craft restoration advice — lives in the skill's reference docs (`references/aic-flags.md`, `references/source-triage.md`, `references/rhetorical-countermoves.md`). It diagnoses prose patterns that humans can read, decides whether each instance is earned in context, and recommends revision moves. It is not a script.

## Inputs

All scripts accept a baseline directory. The voice scripts also accept an optional JSONL corpus manifest so later tools can select files by register, persona, AI status, split, and intended use. With a manifest, voice-coherence runs warn about mixed registers / personas / privacy classes that would confound the comparison.

---

## variance_audit.py

Computes Layer A distributional diagnostics on a text file. Outputs a band classification (Lightly / Moderately / Heavily smoothed), per-signal statistics, and optional baseline comparison.

### Usage

```
python3 variance_audit.py INPUT.txt
python3 variance_audit.py INPUT.txt --json
python3 variance_audit.py INPUT.txt --baseline-dir ../baselines/literary-fiction/
python3 variance_audit.py INPUT.txt --no-tier2 --no-tier3
```

### Tiers and dependencies

The script runs in three tiers and degrades gracefully when optional dependencies are missing.

**Tier 1 (always available; pure Python).**

- Sentence-length stats and burstiness B = (σ − μ)/(σ + μ)
- MATTR (moving-average TTR, window 50)
- MTLD (measure of textual lexical diversity)
- Yule's K
- Shannon entropy of token distribution
- Per-sentence FKGL stats (mean and SD)
- Connective density (markers per 1000 tokens)
- Function-word fingerprint (top-100 frequencies, function-word ratio)

Optional libraries that improve Tier 1 if installed: `nltk` (better sentence tokenization), `textstat` (better syllable counting).

**Tier 2 (requires spaCy and `en_core_web_sm`).**

- POS-bigram distribution and entropy
- Mean Dependency Distance per sentence; SD across sentences

Install:

```
pip install spacy
python -m spacy download en_core_web_sm
```

**Tier 3 (requires `sentence-transformers` or `scikit-learn`).**

- Adjacent-sentence cosine similarity (mean and SD)

Install with sentence-transformers (preferred; uses `all-MiniLM-L6-v2`):

```
pip install sentence-transformers
```

Or fallback to TF-IDF cosine via scikit-learn:

```
pip install scikit-learn
```

### Output format

Default output is a human-readable summary printed to stdout. Pass `--json` for a complete JSON object suitable for piping into another tool.

The JSON shape:

```json
{
  "task_surface": "smoothing_diagnosis",
  "audit": {
    "summary": {"n_words": ..., "n_sentences": ..., "reliable": ...},
    "tier1": { ... },
    "tier2": { "available": true, "pos_bigrams": {...}, "mdd": {...} },
    "tier3": { "available": true, "adjacent_cosine": {...} }
  },
  "compression": {
    "band": "Insufficient signal" | "Lightly smoothed" | "Moderately smoothed" | "Heavily smoothed",
    "weighted_score": ...,
    "available_weight": ...,
    "compression_fraction": ...,
    "flagged_signals": [...],
    "skipped_signals": [...],
    "n_flagged": ...,
    "notes": {...},
    "thresholds_used": {...}
  },
  "baseline": { ... },
  "baseline_comparison": { ... },
  "baseline_divergences": { "pos_bigrams": {...} }
}
```

The band classification is now a fraction of available signal weight, not an absolute weighted score. `compression_fraction` is `weighted_score / available_weight`; bands threshold at 0.15 and 0.40. Documents where no signal cleared its length floor land in `Insufficient signal` rather than defaulting to `Lightly smoothed`. See "Length sensitivity" below for the per-signal floors.

### Length sensitivity

Several metrics are unreliable below certain word counts. The script skips these heuristics when the document is too short and reports the skipped set in `compression.skipped_signals`. Length floors are also carried through to `baseline_comparison`: each z-score whose target falls below the heuristic's floor is marked with `length_floor_satisfied: false` and a warning string. Current floors (kept in sync with `COMPRESSION_HEURISTICS`):

| Signal | Length floor (words) |
|---|---|
| Burstiness B | 200 |
| Connective density | 200 |
| FKGL SD | 200 |
| Adjacent-cosine mean | 200 |
| MATTR | 300 |
| Adjacent-cosine SD | 300 |
| MDD-SD | 300 |
| MTLD | 500 |
| Yule's K | 500 |
| Shannon entropy | 2000 |
| Sentence-length SD | 5000 |

Below 200 words, every length-tracked signal is below its floor and the band classification is `Insufficient signal`. Below 50 words, the script returns a warning and stops.

### Baseline comparison

If `--baseline-dir DIR` is supplied, the script reads every `.txt` file in that directory, computes the same statistics on each, and aggregates per-statistic mean and SD. The target document is then reported with z-scores against the baseline. |z| > 1.0 is flagged as meaningful in the human-readable summary.

The `baselines/` directory in this skill is documented in its own README. v1 ships baseline structure but not the corpora themselves; users can populate the directory with their own prior unedited work or with public-domain texts in the relevant genre.

### Length-matched bootstrap

`--bootstrap` (with `--baseline-dir`) replaces the per-signal z-scores with empirical percentiles drawn from length-matched windows of the baseline corpus, plus BCa confidence intervals on the percentiles via `scipy.stats.bootstrap`. The motivation is that comparing a 300-word target against the mean and SD of full-file baseline statistics over- or under-estimates the expected statistic value at length 300; the empirical distribution at the right length is the right comparison.

Mechanics: for each baseline file, the script samples `--bootstrap-windows-per-file` random length-N word slices (where N = the target's word count), pools the per-window statistic values into an empirical distribution at length N, then reports the target's mid-rank percentile in that distribution and a BCa CI on the percentile. Total windows are capped via `--bootstrap-max-windows` so long corpora do not dominate the pool. Files shorter than N contribute one whole-file sample.

CIs collapse to `[1.000, 1.000]` or `[0.000, 0.000]` when the target falls strictly past the extreme of the baseline distribution: every resample produces the same percentile, so there is no resampling uncertainty. The headline finding in those cases is the point estimate, not the interval. The reported `method` field carries `BCa`, `percentile` (BCa fallback on degenerate jackknife), or `degenerate_no_ci` accordingly.

Flags:

- `--bootstrap` — turn on the bootstrap pass alongside the standard z-score comparison.
- `--bootstrap-windows-per-file N` — windows per baseline file (default 50).
- `--bootstrap-max-windows N` — total cap across files (default 500).
- `--bootstrap-resamples N` — bootstrap resamples for the CI (default 9999).
- `--bootstrap-confidence X` — confidence level (default 0.95).
- `--bootstrap-seed N` — seed the window sampler and the resampler for reproducible runs.

Cost: each bootstrap window runs the full Tier 1 (and Tier 2 / Tier 3 if enabled) audit. With the full stack, expect ~0.5 second per window on commodity hardware. Pass `--no-tier3` (and `--no-tier2`) to drop the slowest tiers if the bootstrap is dominating run time and you are willing to lose those signals' percentiles.

### Calibration notes

The default thresholds (in `COMPRESSION_HEURISTICS`) are calibrated against fluent native-English fluent prose. They are heuristic fallbacks for users without a baseline corpus. With a baseline, z-scores are more reliable than absolute-threshold flagging.

Burstiness B and connective density are the most reliable single signals at short lengths. MATTR, MTLD, Yule's K, and Shannon entropy are length-sensitive and unreliable on short documents.

### Smoke test

The repository ships two test passages in `scripts/test_data/` that demonstrate expected differentiation. Run:

```
python3 variance_audit.py scripts/test_data/human_sample.txt
python3 variance_audit.py scripts/test_data/ai_sample.txt
```

The human sample should classify Lightly smoothed; the AI sample Moderately or Heavily smoothed depending on which tiers are available.

---

## manuscript_audit.py

Runs `variance_audit` logic across every chapter of a manuscript and produces a dashboard. Surfaces manuscript-wide compression patterns (signals that fire on most chapters) and outlier chapters (chapters with the most |z| > 1.5 signals against baseline). Single-chapter audits miss these patterns by construction.

### Usage

```
# Single manuscript file with chapter markers
python3 manuscript_audit.py MANUSCRIPT.md --baseline-dir BASELINE_DIR

# Directory of chapter files
python3 manuscript_audit.py --chapter-dir CHAPTERS/ --baseline-dir BASELINE_DIR

# Custom chapter-marker regex
python3 manuscript_audit.py NOVEL.md --baseline-dir B/ --chapter-pattern '^##\s*Part\s+\d+'

# JSON output
python3 manuscript_audit.py MANUSCRIPT.md --baseline-dir B/ --json --out report.json

# Markdown report to file
python3 manuscript_audit.py MANUSCRIPT.md --baseline-dir B/ --out manuscript_report.md
```

### Output

A markdown dashboard with three sections:

1. **Per-chapter signal dashboard.** A table with chapters as rows, signals as columns. Cells show z-scores. Bold cells are |z| > 1.0 in the compression direction.
2. **Manuscript-wide patterns.** Which signals fire on at least half of chapters. These are the manuscript-level compression patterns rather than chapter-specific issues.
3. **Outlier chapters.** Chapters with the most |z| > 1.5 signals, sorted by flag count. First candidates for revision.

The dashboard is the most useful single artifact for revision triage: it tells you which chapters need vocabulary restoration first, and which signals are doing the most work across the manuscript.

### Chapter detection

Default regex matches `# Chapter N` and `## Chapter N` markers (case-insensitive on chapter, requires a numeric chapter number). Override with `--chapter-pattern` for other conventions (parts, sections, lettered chapters, etc.).

If no markers are found in the manuscript, the entire file is treated as one chapter.

---

## repetition_audit.py

Surfaces specific words a writer is using more than expected against their own baseline, plus within-text clustering. Designed for the vocabulary-restoration pass when Layer A flags lexical compression (low MATTR / MTLD against personal baseline).

### Usage

```
# Basic
python3 repetition_audit.py CHAPTER.md --baseline-dir BASELINE_DIR

# With project anchors (character names, scene-anchored vocabulary to ignore)
python3 repetition_audit.py CHAPTER.md --baseline-dir BASELINE_DIR --anchors anchors.txt

# Show top 50 candidates
python3 repetition_audit.py CHAPTER.md --baseline-dir BASELINE_DIR --top 50

# JSON output
python3 repetition_audit.py CHAPTER.md --baseline-dir BASELINE_DIR --json --out report.json
```

### Output

Two ranked tables:

1. **Words over-represented vs. baseline.** Sorted by ratio (target frequency / baseline frequency). Top candidates are words used at much higher rates than the writer's baseline distribution.
2. **Words clustering within a 300-token window.** Words that recur within a single passage rather than spread evenly. Top candidates for varying within local context.

Words that appear in BOTH lists are the strongest candidates for variation in revision.

### Project anchors

Use `--anchors path/to/file.txt` to exclude words whose repetition is structurally necessary (character names, recurring objects, location nouns, named drugs, etc.). The script ships with `example_anchors.txt` showing the format.

For a real project, maintain a private anchors file outside the rebuild folder (e.g. in your private baselines directory) and pass it via `--anchors`.

### Calibration

- `--min-count` (default 3): a word must appear at least this many times to be considered.
- `--min-word-len` (default 4): skip very short words.
- `--cluster-window` (default 300): sliding window size for clustering check.
- `--include-function-words`: by default, common English function words are excluded; use this flag to include them (rarely useful).

### Reading the output

The script catches three patterns:

- **Generic repetition** (high ratio, moderate cluster). The word is doing repeat duty across the document; varying it would sharpen each instance. Examples from real revision: `arrived`, `phrase`, `version`, `answer`, `named`.
- **Thematic anchor** (high ratio AND high cluster, but the word IS the chapter's argument). Repetition is doing structural work. Examples: `lock`, `refusal`, `handle` in a chapter about containment failure. Don't vary these.
- **Local cluster** (lower ratio, high cluster_max). The word is varied across the document but recurs within a single passage. Often a good local-revision target.

The diagnostic question is the same as the source-triage layer: is this repetition earning its weight, or is the writer reaching for the same word in slots that would benefit from variation?

---

## manuscript_repetition_audit.py

Sibling to `repetition_audit.py`. Runs the same per-chapter scoring across a multi-chapter manuscript and aggregates results, surfacing words that drift in many chapters at once. A word that recurs in two or three chapters at moderate ratio is the classic dispersed habit-vocabulary pattern; single-chapter audits miss it because the per-chapter ratio is unremarkable in any one place.

### Usage

```
# Single manuscript file with chapter markers
python3 manuscript_repetition_audit.py MANUSCRIPT.md --baseline-dir BASELINE_DIR

# Directory of chapter files
python3 manuscript_repetition_audit.py --chapter-dir CHAPTERS/ --baseline-dir BASELINE_DIR

# With anchors, custom chapter pattern, JSON output
python3 manuscript_repetition_audit.py NOVEL.md \
  --baseline-dir B/ --anchors anchors.txt \
  --chapter-pattern '^##\s*Part\s+\d+' --json --out report.json
```

### Output

A markdown dashboard with three sections:

1. **Dispersed habit vocabulary.** Words flagged as over-represented in at least `min_dispersed_chapters` of `n_chapters` chapters. Default threshold is `max(3, n_chapters // 3)`. Columns show the chapter spread, total count, mean and median ratio across chapters, peak ratio, and the chapter where the peak occurs.
2. **Concentrated repetition (1-2 chapters).** Words with high over-representation but limited to one or two chapters. Often thematic anchors carrying scene weight. Verify in source-triage before treating as repetition problems.
3. **Per-chapter top over-representations.** Top-N words per chapter by ratio. A compact view of which chapters carry the strongest local lexical signature.

The dispersed-vs-concentrated distinction is the load-bearing one. A high-ratio word that recurs in nine of fifteen chapters is habit; the same word at the same ratio in one chapter is more often anchor.

### Calibration

Inherits `repetition_audit.py`'s defaults: `--min-count 3`, `--min-word-len 4`, `--cluster-window 300`, `--min-ratio 1.0`. The `--min-ratio 1.0` floor ensures only over-represented words enter the candidate list and the aggregator; pass `--min-ratio 0` for legacy all-candidates behavior. Tune `--min-dispersed-chapters` if the default threshold is wrong for your manuscript shape (default scales to a third of chapters, floor of three). Function-word filtering and `--anchors` work the same way as the single-document version.

The baseline is loaded once and shared across all chapters, so adding chapters is roughly linear. JSON output preserves the per-chapter and aggregated structures separately for downstream tooling.

### Baseline guards

The script refuses to run when `--baseline-dir` produces zero usable files or zero tokens, and drops any baseline file whose resolved path equals the manuscript file or any chapter file in `--chapter-dir`. Without these guards a manuscript pointed at its own directory becomes its own baseline and ratios collapse toward zero.

Unreadable baseline files (permission errors, missing files mid-run, encoding failures that bubble up as `OSError`) are surfaced rather than silently dropped. The report header reports both `Baseline files loaded` and `Baseline files skipped`, and a stderr warning names the skipped files. A skipped baseline file means the words it would have contributed are absent from the baseline counts, which inflates the target's ratios for those words; the warning makes that visible. The same guards apply to `repetition_audit.py`, which also exposes `baseline_files_loaded`, `baseline_files_skipped`, and `baseline_tokens` in its JSON output.

### Reading the output

Treat the dispersed list as the priority candidates for a manuscript-wide variation pass. Words that show up in the per-chapter view but not the dispersed list are usually local issues; words in the dispersed list are habit signatures that need to be addressed across the manuscript or accepted as voice. Concentrated repetition is the section to read with `aic-flags.md` Layer C source-triage in hand: high cluster_max plus thematic relevance is often earned.

---

## manifest_validator.py

Schema and integrity checks for `corpus_manifest.jsonl`. Phase 1 step 1 of the validation spine: the gatekeeper that downstream manifest-consuming tools rely on so they can trust the manifest before running. Without this check, a single AI-assisted entry mistakenly tagged `ai_status: pre_ai_human` can teach a voiceprint pipeline that smoothing is part of the writer's voice, a `use: validation` entry tagged `split: baseline` collapses the hold-out split into the training data, and a missing-on-disk path produces silent shrinkage of every downstream comparison.

### Usage

```
python3 manifest_validator.py corpus_manifest.jsonl
python3 manifest_validator.py corpus_manifest.jsonl --json
python3 manifest_validator.py corpus_manifest.jsonl --strict --out report.md
```

### What it checks

Per entry:
- Required fields present: `id`, `path`, `ai_status`, `use`.
- Enum-valued fields use known values for `ai_status`, `register`, `split`, `privacy`, `editing_status`. Unknown values are warnings (the taxonomy is extensible); typos in field names are warnings too (catches `asi_status` for `ai_status`).
- `use` must be a list (single-string `use` is a hard error per the manifest spec).
- `word_count` must be a non-negative number when present.

Cross-entry:
- Duplicate `id` is an error.
- Path must resolve to an existing file using the same resolution as `stylometry_core.resolve_manifest_path` (manifest-relative, then parent-relative, then cwd-relative).
- Two ids pointing at one file is a warning (often legitimate but worth flagging).
- `use: validation` AND `split: baseline` is an error: the holdout collapses into the training data.
- `use: baseline` AND `split: train|test|holdout` is a warning.
- `use: voice_profile` AND `privacy != private` is a warning (a voiceprint is a voice-cloning input).
- `ai_status: pre_ai_human` AND `editing_status: coauthored` is a warning (potentially contradictory provenance).

### Exit codes

| Exit | Condition |
|---|---|
| 0 | No errors. Warnings allowed unless `--strict`. |
| 1 | Errors present, OR `--strict` and warnings present. |

### Output shape

Markdown report with a summary block (counts by register, ai_status, split, use, privacy, persona) and itemized Errors and Warnings sections. JSON output preserves the same structure: a top-level `task_surface: "validation"`, plus `manifest_path`, `n_entries`, `n_errors`, `n_warnings`, an `issues` list, and a `summary` block. Importable: `validate_manifest(path) -> dict` returns the same structure for downstream tools that want to gate on manifest health before composing a run.

### Library use

```python
from manifest_validator import validate_manifest

result = validate_manifest("corpus_manifest.jsonl")
if result["n_errors"] > 0:
    raise RuntimeError("Manifest has errors; refusing to run.")
```

---

## chapter_distinctiveness_audit.py

Sibling to `manuscript_repetition_audit.py`. Different question: instead of "which words are over-represented in this chapter versus an external baseline corpus," this script asks "which words are over-represented in this chapter versus the rest of the manuscript." Internal-baseline construction is leave-one-out: for each chapter, the baseline is the union of all other chapters. No external corpus is required; the manuscript scores against itself.

The two audits surface different patterns. A habit-vocabulary word that recurs in many chapters at moderate ratio will land in the manuscript-aggregate audit but not here, because the rest-of-manuscript baseline already contains it. A word distinctive to one chapter (a thematic anchor, a setting prop, POV-specific vocabulary) will land here but may not land in the manuscript-aggregate audit if the external corpus also uses that word. Run both for full coverage.

### Usage

```
# Single manuscript file with chapter markers
python3 chapter_distinctiveness_audit.py MANUSCRIPT.md

# Directory of chapter files
python3 chapter_distinctiveness_audit.py --chapter-dir CHAPTERS/

# With anchors and stricter ratio threshold
python3 chapter_distinctiveness_audit.py NOVEL.md \
  --anchors anchors.txt --min-ratio 1.5 --top-per-chapter 10
```

### Output

A markdown dashboard with two sections:

1. **Distinctive-vocabulary load by chapter.** Number of words clearing the over-representation threshold in each chapter. Chapters with many candidates carry vocabulary the rest of the manuscript does not. Useful for identifying chapters that drift lexically from the manuscript's center of gravity.
2. **Per-chapter distinctive vocabulary.** For each chapter, a top-N table of words with their target frequency, rest-of-manuscript frequency, ratio, and within-chapter cluster_max. Chapters with no flagged candidates are still listed so absence is visible.

There is no manuscript-wide aggregator: a word's ratio against rest-of-manuscript in one chapter is not directly comparable to the same word's ratio in another chapter, because the baselines are different.

### Calibration

Inherits the per-chapter scoring defaults from `repetition_audit.py`, with one exception: `--min-ratio` defaults to 1.5 here (vs. 1.0 in the external-baseline audits) because "distinctive" is a stronger claim than "barely over-represented." The higher floor cuts noise introduced by chapters that omit otherwise-dispersed habit-vocabulary, which can drag the rest-of-manuscript baseline down enough to make borderline ratios appear in chapters that contain the word at typical density. Pass `--min-ratio 1.0` to match the external-baseline audits' threshold, or higher (2.0+) to focus on decisively distinctive words. Smaller manuscripts produce noisier ratios because the rest-of-manuscript baseline is smaller; treat short-manuscript ratios as inspection leads rather than verdicts.

The audit refuses single-chapter manuscripts because the rest-of-manuscript baseline would be empty. Use `repetition_audit.py` against an external baseline for that case.

### Reading the output

Words appearing in both this audit and `manuscript_repetition_audit.py`'s concentrated section are confirmed thematic anchors: they spike in one chapter against both the external baseline and the rest of the manuscript. Words appearing in this audit but not in `manuscript_repetition_audit.py` are distinctive within the manuscript but not unusual against the writer's broader vocabulary, often setting props or POV-specific language that is fine as-is. The opposite (in `manuscript_repetition_audit.py` but not here) is the dispersed-habit pattern.

---

## voice_distance.py

Compares a target text against a writer/register baseline using classic
stylometric families:

- function-word Burrows-style Delta and cosine distance
- character n-grams
- punctuation cadence
- paragraph and dialogue ratios
- contraction, pronoun, modal, negation, and hedge profiles
- POS trigrams and dependency-label n-grams when spaCy is available

### Usage

```
python3 voice_distance.py TARGET.md --baseline-dir BASELINE_DIR
python3 voice_distance.py TARGET.md --baseline-dir BASELINE_DIR --no-spacy
python3 voice_distance.py TARGET.md --baseline-dir BASELINE_DIR --json --out voice_distance.json
```

With a manifest:

```
python3 voice_distance.py TARGET.md \
  --manifest corpus_manifest.jsonl \
  --persona fiction_voice \
  --register literary_fiction \
  --use baseline
```

### Output

The report gives an overall weighted Delta band plus per-family distances,
top feature deviations, and a Feature Clusters section. The overall score
caps any single feature family's contribution, because paragraph preservation
and formatting artifacts can otherwise overwhelm the result.

The Feature Clusters section aggregates the function-word family deviations
into pre-defined syntactic groups (pronouns by person and number, demonstratives
and other deixis, three modal subgroups, prepositions, conjunctions, wh-words,
and so on). For each cluster the
report shows mean signed z, direction consistency (the fraction of matched
features moving the same way), and the top contributing features. Clusters
with at least three matched features and 70% direction consistency are
flagged as `directional`. The cluster view catches authorial fingerprints
that the per-feature top-N misses when each individual feature sits below
the conventional flag threshold but the cluster as a whole drifts
together. Read the two views as complements: per-feature deviations catch
template repetition and isolated topic-anchored breaks; clusters catch
register and idiolect shifts.

Skip the cluster pass with `--no-clusters`. Tune the matched-feature floor
with `--cluster-min-features` (default 2) and the table size with
`--cluster-top` (default 15).

The displayed bands are provisional until the validation harness calibrates
thresholds against labeled corpora. Reports say this explicitly. Reports also
warn when the target is below 500 words, when the baseline has fewer than five
files, when manifest-selected baselines mix registers/personas/privacy classes,
or when baseline files are very short.

Interpretation bands:

| Band | Meaning |
|---|---|
| Close to baseline | Target sits inside the supplied baseline on most measured features |
| Light drift | Recognizably related to the baseline, with meaningful departures |
| Strong drift | Multiple feature families diverge |
| Off-baseline | Far from the supplied baseline; check register mismatch first |

### Character n-gram families

Character n-grams are tracked per n: `char_ngrams_3`, `char_ngrams_4`, and `char_ngrams_5`. Each family has its own frequency space (each n's frequencies sum to 1 within that n, not across all three combined), its own selection cap (default 200 from `--char-top`, applied separately to each n), its own Burrows-Delta and cosine distance, and its own contribution weight in the overall delta (0.5 each, summing to the same 1.5 the unified family carried before).

Earlier versions mixed all three n-values in one frequency space. The result was that the much-more-numerous 3-grams dominated both selection and frequency mass, and the 4- and 5-gram signal got drowned out. Per-n separation lets the 4-gram and 5-gram distances participate in the overall comparison on their own merits.

### Calibration notes

Use register-matched baselines. A blog-essay baseline will correctly tell you
that a fiction scene is off-baseline, but that does not mean the fiction scene
has lost voice. It means you asked the wrong comparison question.

Texts under 1,000 words can produce unstable z-scores, especially for character
n-grams and paragraph features. For short passages, read the top deviations as
leads for inspection rather than verdicts.

If a per-family Delta exceeds the overall contribution cap, the report marks it
as capped in the overall score. This most often happens with paragraph/dialogue
features when source paragraph breaks differ.

---

## voice_profile.py

Produces a private Markdown or JSON voiceprint from a baseline corpus. The output
lists the highest-frequency and most stable features across the supplied files.

### Usage

```
python3 voice_profile.py --baseline-dir BASELINE_DIR --out PRIVATE_voice_profile.md
python3 voice_profile.py --baseline-dir BASELINE_DIR --no-spacy --top 30
```

With a manifest:

```
python3 voice_profile.py \
  --manifest corpus_manifest.jsonl \
  --persona essay_voice \
  --register blog_essay \
  --use voice_profile \
  --out PRIVATE_essay_voice_profile.md
```

### Privacy

Voice profiles are useful for protecting idiolect during revision, but they are
also voice-cloning inputs. Keep outputs in the private baselines folder, not in
the publishable rebuild folder.

By default, `voice_profile.py --out` refuses to write outside a path containing
`ai-prose-baselines-private/`. Pass `--allow-public-output` only when you have a
specific reason and understand the risk. JSON output still includes the privacy
warning.

---

## Corpus manifest format

A manifest is JSONL: one JSON object per file. Paths may be absolute or relative
to the manifest file.

```json
{"id":"essay_2017_public-argument","path":"../private-baselines/blog/essay_2017_public-argument.txt","project_area":"personal_blog","author":"Author Name","persona":"essay_voice","register":"blog_essay","genre":"personal_essay","date_written":"2017-07-07","ai_status":"pre_ai_human","editing_status":"published_cleaned","word_count":2200,"use":["baseline","voice_profile"],"split":"baseline","privacy":"private","source":"personal archive","notes":"Confirmed pre-routine-AI."}
{"id":"fiction_project-a_ch01","path":"../private-baselines/fiction/project-a_ch01.txt","project_area":"fiction","author":"Pen Name","persona":"fiction_voice","register":"literary_fiction","genre":"literary_horror","date_written":"pre_2023","ai_status":"pre_ai_human","editing_status":"draft","word_count":18000,"use":["baseline","voice_profile"],"split":"baseline","privacy":"private","pov":"mixed","notes":"Private draft archive."}
```

Recommended fields:

| Field | Purpose |
|---|---|
| `id` | Stable unique handle |
| `path` | Local text file |
| `project_area` | advocacy, fiction, philosophy, blog, personal |
| `author` | Human or institutional author |
| `persona` | Writing identity or voice context |
| `register` | Main comparison bucket |
| `genre` | Narrower register |
| `date_written` | Enables drift tracking |
| `ai_status` | `pre_ai_human`, `ai_generated`, `ai_assisted`, `ai_edited`, `mixed`, `unknown` |
| `editing_status` | `raw_draft`, `revised_human`, `published_cleaned`, etc. |
| `use` | Usually includes `baseline`, `voice_profile`, `validation`, or `exclude` |
| `split` | `baseline`, `train`, `test`, `holdout` |
| `privacy` | `private`, `shareable`, `public_domain` |
