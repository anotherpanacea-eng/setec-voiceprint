# Stylometry scripts

The scripts in this directory split across four active task surfaces. Most failure modes come from confusing them.

## Active task surfaces

### Surface 1: AI-prose smoothing diagnosis

These scripts ask whether the prose has been smoothed into a narrower-than-typical region of stylometric space. They measure deviation from a *typical human-prose region*, not from a specific writer.

| Script | Scope | Use when |
|---|---|---|
| `variance_audit.py` | Single document | Diagnostic on one chapter or passage |
| `manuscript_audit.py` | Whole manuscript (multi-chapter) | Surfacing manuscript-wide patterns and outlier chapters |
| `repetition_audit.py` | Single document, vocabulary level | Layer A flagged lexical compression and you want specific candidates for restoration |
| `manuscript_repetition_audit.py` | Whole manuscript, vocabulary level | Surfacing dispersed habit-vocabulary that recurs across chapters |
| `chapter_distinctiveness_audit.py` | Whole manuscript, vocabulary level | Surfacing words distinctive to one chapter against the rest of the manuscript (leave-one-out, no external baseline) |
| `bigram_diff.py` | Single document vs. cluster, syntactic level | Variance-audit POS-bigram KL elevated and you want to see which specific bigrams are driving the divergence |
| `manuscript_bigram_diff.py` | Corpus A vs. corpus B, syntactic level | Comparing the syntactic-template footprint of two corpora at the aggregate level (e.g. AI-collaborated cohort vs. pre-AI archive) |

What these scripts cannot answer: who wrote it, whether the smoothing is an artifact of register or scene type, what to revise. The verdict they license is *"this prose shows characteristics of AI smoothing"* — not *"this prose was written by AI."*

### Surface 2: Voice-coherence comparison

These scripts ask how far a target text is from a *specific writer's or register's* baseline. They measure deviation from a writer-shaped reference, not from a typical human-prose region.

| Script | Scope | Use when |
|---|---|---|
| `voice_distance.py` | Single target vs. baseline | Ask how far a draft has drifted from a writer/register voiceprint |
| `voice_profile.py` | Baseline corpus | Produce a private human-readable voiceprint from a corpus |
| `idiolect_detector.py` | Target corpus vs. reference corpus | Extract distinctive words/phrases and a "do not normalize" preservation list |
| `voice_drift_tracker.py` | Date-tagged baseline corpus | Disaggregate the writer's baseline by time period; see which features are stable vs. drifting across the writer's history. Pairs with `voice_distance.py` to distinguish "drift between draft and baseline" (recent) from "drift across the writer's own history" (long-term) |
| `pov_voice_profile.py` | POV-tagged baseline corpus (multi-POV fiction) | Disaggregate the writer's baseline by POV character; see whether the writer differentiates POVs in voice space or has collapsed multiple characters into one neutral default. Reports pairwise POV voice-distance, distinguishing features per POV, and a heuristic voice-collapse verdict for pairs whose Burrows-Delta falls below threshold |

What these scripts cannot answer: whether the divergence is caused by AI involvement, register shift, time drift, or genuine voice change. The verdict they license is *"this draft has drifted from this baseline by this much"* — not *"AI involvement caused the drift"* and not *"the writer is no longer themselves."* `voice_drift_tracker.py` adds a temporal axis: it can tell you *which* features have been moving across the writer's history, but not *why* — natural stylistic evolution and symptomatic distortion both produce drift.

### Surface 3: Empirical validation

These scripts ask whether SETEC's signals behaved as expected on a labeled corpus.

| Script | Scope | Use when |
|---|---|---|
| `manifest_validator.py` | Corpus manifest | Refuse contaminated or contradictory validation inputs before running |
| `check_corpus.py` | Corpus files / manifest slice | Refuse HTML/CSS/code/table contamination before calibration or KL-sensitive runs |
| `validation_harness.py` | Labeled validation entries | Measure performance by register, length, AI status, and language status |

### Surface 4: Craft restoration

This surface is primarily a reference-prose surface (the Layer B/C named-pattern taxonomy and source-triage methodology live in `references/aic-flags.md`, `references/source-triage.md`, and `references/rhetorical-countermoves.md`), with one quantitative pre-pass script that surfaces candidate instances for the writer's source-triage adjudication.

| Script | Scope | Use when |
|---|---|---|
| `aic_pattern_audit.py` | Single document, named-pattern level (Layer B/C) | Counting named rhetorical patterns (correctio, pseudo-aphorism, manifesto cadence, triplet, professional-parallel stack, plus four nonfiction parallel patterns) at per-thousand-word density, optionally vs. baseline corpus |
| `restoration_packet.py` | Translates Surface 1/2 diagnostic JSON into bounded revision-safe prompt packets | Turning variance audit / bigram diff / voice distance / idiolect / AIC outputs into a packet that classifies each signal as direct / translated / investigate-first / avoid-direct. Sibling to the `craft-restoration` skill, which reads prose and AIC flags; this script reads diagnostic JSON and emits revision instructions with named guardrails and required post-check commands. See `references/metric-targeted-restoration.md` for the targetability taxonomy. |
| `before_after_restoration.py` | Closes the post-check loop: compare diagnostics before vs. after a revision pass | Reading "before" and "after" diagnostic JSONs (and the original `restoration_packet.py` packet) to report per-target verdicts: improved / no_change / degraded / gamed / not_measurable. Direction-aware (uses each signal's registry direction to know which way is improvement). Metric-gaming heuristic flags any actionable target whose improvement coincides with a worsening avoid-direct aggregate (the failure mode the targetability taxonomy is designed to resist). Optional preservation-list survival check when `--original-text` and `--revised-text` are supplied: case-insensitive substring search confirms idiolectic phrases survived the revision. |

What `aic_pattern_audit.py` cannot answer: the earned/unearned verdict on any individual instance. That is a Layer C source-triage call the writer has to make in context. The script reports density and surfaces flagged sentences; the writer adjudicates per instance using `references/source-triage.md`.

What `restoration_packet.py` does NOT do: rewrite prose, claim AI provenance, or optimize metrics directly. The framework's metric-gaming resistance lives in the four-class targetability taxonomy: aggregate divergence and overall distances are explicitly `avoid_direct` and never become prompt instructions. v1 produces target packets and prompt text; the actual revision is a human- or LLM-in-the-loop pass with required SETEC post-checks.

### Surface tag in script output

Every script's JSON output carries a top-level `task_surface` field, and every markdown report shows the surface near the header. The field tells downstream consumers which question the output is answering. Current values:

| Field value | Scripts |
|---|---|
| `smoothing_diagnosis` | `variance_audit.py`, `manuscript_audit.py`, `repetition_audit.py`, `manuscript_repetition_audit.py`, `chapter_distinctiveness_audit.py` |
| `voice_coherence` | `voice_distance.py`, `voice_profile.py`, `idiolect_detector.py`, `voice_drift_tracker.py`, `pov_voice_profile.py`, `voice_validation_harness.py` |
| `validation` | `manifest_validator.py`, `check_corpus.py`, `validation_harness.py` |
| `craft_restoration` | `aic_pattern_audit.py` (named-pattern density pre-pass), `restoration_packet.py` (metric-targeted revision packets), `before_after_restoration.py` (post-check loop); the rest of the surface lives in the reference prose at `references/aic-flags.md`, `references/source-triage.md`, `references/rhetorical-countermoves.md`, and `references/metric-targeted-restoration.md` |

The contract is enforceable at the data layer. The validation harness refuses to mix scores across surfaces because the surfaces answer different questions. Reports are now self-identifying so a reader (or an automated consumer) can route by surface without reading the script's filename or guessing from output shape.

### Why the surfaces are kept distinct

The two surfaces share statistical signals (function-word distributions, lexical diversity, sentence-length variance, syntactic patterns), because RLHF-induced mode collapse, register conventions, and time-stable authorial idiolect all leave traces in the same features. But they answer different questions and license different claims. A single "is this AI" verdict would have to collapse them into one number, which the underlying math does not entitle.

When you have a target document, ask first which question you're trying to answer. If you want to know *whether the prose looks AI-smoothed*, run the audit scripts (Surface 1). If you want to know *whether this draft sounds like the writer*, run the voice scripts (Surface 2). The two surfaces can both run on the same document; their findings should be read separately, not averaged.

A third surface — empirical performance validation against a labeled corpus — is shipped in three pieces. `manifest_validator.py` checks the schema and integrity of `corpus_manifest.jsonl` so manifest-consuming tools can trust the manifest before running. `check_corpus.py` checks the files themselves for HTML/CSS/code/table contamination that a valid manifest cannot see. `validation_harness.py` reports how well smoothing-diagnosis scores discriminate against labeled validation entries, in the manifest's registers, text lengths, AI-status classes, and language-status classes. It produces claims about your corpus, not about the world.

A fourth surface — craft restoration advice — lives primarily in the skill's reference docs (`references/aic-flags.md`, `references/source-triage.md`, `references/rhetorical-countermoves.md`). It diagnoses prose patterns that humans can read, decides whether each instance is earned in context, and recommends revision moves. The earned/unearned verdict is irreducibly a writer's call. `aic_pattern_audit.py` provides a quantitative pre-pass that counts named-pattern density and surfaces candidate instances for that adjudication; the rest of the surface stays in prose.

## Inputs

Most audit scripts accept a baseline directory. The voice scripts accept an optional JSONL corpus manifest so tools can select files by register, persona, AI status, split, and intended use. The validation scripts operate on the manifest directly. With a manifest, voice-coherence runs warn about mixed registers / personas / privacy classes that would confound the comparison.

---

## variance_audit.py

Computes Layer A distributional diagnostics on a text file. Outputs a band classification (Lightly / Moderately / Heavily smoothed), per-signal statistics, and optional baseline comparison.

### Usage

```
python3 variance_audit.py INPUT.txt
python3 variance_audit.py INPUT.txt --json
python3 variance_audit.py INPUT.txt --baseline-dir ../baselines/literary-fiction/
python3 variance_audit.py INPUT.txt --no-tier2 --no-tier3
python3 variance_audit.py INPUT.txt --allow-non-prose
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

### Corpus-hygiene preprocessing

By default, `variance_audit.py` strips suspected non-prose before tokenization and POS-tagging: HTML/CSS/JS blocks, Markdown code fences, indented code blocks, inline code, loose CSS rules, conservative HTML tags, JSON-shaped blocks, ASCII tables, and YAML front matter. The same preprocessing rules apply symmetrically to every baseline file, because target-only cleanup would make the baseline comparison dishonest.

Relevant flags:

- `--allow-non-prose` — opt out of stripping. JSON records the opt-out because KL/JSD readings may include markup or code contamination.
- `--strip-rules a,b,c` — enable only the named conservative rules.
- `--strip-aggressive` — additionally strip URL-only lines, Markdown image URLs, link wrappers, footnote markers, and high-confidence citations.
- `--strip-warn-threshold X` — warn on stderr when target or any baseline file loses more than this fraction of whitespace tokens (default `0.05`).
- `--show-stripped [path]` — write stripped target fragments to stderr or to the supplied path for debugging.

JSON carries a top-level `preprocessing` block for the target and `baseline.preprocessing` for the corpus aggregate, including `per_file` detail so contaminated baseline sources can be identified.

### Output format

Default output is a human-readable summary printed to stdout. Pass `--json` for a complete JSON object suitable for piping into another tool.

The JSON shape:

```json
{
  "task_surface": "smoothing_diagnosis",
  "preprocessing": {
    "applied": true,
    "rules_active": [...],
    "input_tokens_before": ...,
    "input_tokens_after": ...,
    "tokens_stripped": ...,
    "tokens_stripped_by_rule": {...},
    "strip_ratio": ...,
    "dominant_rule": "css_rule_block",
    "warning": null
  },
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
  "baseline": { "n_files": ..., "aggregate": {...}, "preprocessing": {...} },
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

## bigram_diff.py

Per-bigram POS-bigram diff between one target document and a comparison cluster. Use after `variance_audit.py`'s POS-bigram KL signal elevates against a baseline and you want to know *which* bigrams are driving the divergence — the granular evidence that aggregate KL hides.

### Usage

```
python3 bigram_diff.py target.txt --cluster-dir comparators/
python3 bigram_diff.py target.md --cluster-files a.txt b.txt c.txt
python3 bigram_diff.py target.md --cluster-dir comparators/ \
    --cluster-mode mean --top 25 --min-count 5 --json
```

### Cluster aggregation modes

`--cluster-mode` toggles three strategies:

- `pooled`: sum POS-bigram counts across cluster files, normalize once. Long files dominate the cluster distribution. Use when the cluster represents one source or you want "what is the cluster doing on aggregate."
- `mean`: average per-file probability distributions (each file weighted equally regardless of length). Use when cluster file lengths vary and you want "what is the cluster typically doing."
- `both` (default): run both, report side-by-side.

The two modes can disagree meaningfully when cluster files differ in length: a short essay with idiosyncratic syntax has equal weight in `mean` but is drowned out in `pooled`. Reading both lets you see whether a flagged divergence is robust across aggregations.

### Output

Markdown by default with two ranked tables per mode (over-represented in target, under-represented in target). Each row carries the bigram in `POS+POS` form, raw probabilities (target % and cluster %), `Δ pp`, `log₂(p/q)`, the per-bigram KL contribution, and up to two example token pairs. The aggregate KL for the chosen mode is a sum of all per-bigram contributions and matches the variance audit's `pos_bigrams.kl_to_baseline` for the pooled-counts case.

`--json` switches to machine-readable output with `task_surface: smoothing_diagnosis` and per-row dicts containing the same fields.

### Smoothing and frequency floor

`--smoothing-alpha` defaults to 1.0 (Laplace add-1, matching `variance_audit.py`'s POS-bigram KL convention) and applies to the pooled-counts path. The mean path uses ε smoothing on the averaged probabilities because count-level smoothing of an averaged distribution is not well-defined.

`--min-count` filters out bigrams where neither the target nor the cluster reaches the count threshold. Suppresses tail noise from rare bigrams. Default 1 (no filter); 5 to 10 is a reasonable starting point for typical-length documents. Note: the floor only fires in pooled-counts mode in this single-document script. The corpus-vs-corpus `manuscript_bigram_diff.py` applies the floor in both modes.

---

## manuscript_bigram_diff.py

Same per-bigram math as `bigram_diff.py`, lifted to compare two corpora at the aggregate level. Use for register-shaped questions ("what does my AI-collaborated cohort do differently than my pre-AI archive at the syntactic-template level?") rather than document-level outlier questions.

### Usage

```
python3 manuscript_bigram_diff.py \
    --corpus-a-dir post_ai/ --label-a "post-ai" \
    --corpus-b-dir pre_ai/  --label-b "pre-ai"
python3 manuscript_bigram_diff.py \
    --corpus-a-files a1.txt a2.txt --corpus-b-files b1.txt b2.txt \
    --label-a "personas A" --label-b "personas B" \
    --aggregation pooled --top 25 --min-count 10 --json
```

### Aggregation modes

`--aggregation` mirrors `bigram_diff.py`'s `--cluster-mode`: `pooled`, `mean`, or `both`. Same trade-offs apply within each corpus.

### Output

Markdown by default with two ranked tables per aggregation mode (over-represented in corpus A, over-represented in corpus B). Same row schema as `bigram_diff.py`. Header reports loaded/skipped file counts per corpus and the labels used.

The `kl_total` reported per aggregation is the sum of per-bigram contributions over the union of bigrams that passed the `min_count` floor; it can differ from `variance_audit.py`'s baseline KL because the audit treats one document as target and the corpus as baseline, while this script aggregates within each corpus before comparing.

### When to reach for it

The variance audit and `bigram_diff.py` operate at the document level. `manuscript_bigram_diff.py` is the cohort-level companion: when you have a labeled corpus split (pre-AI vs. post-AI, native vs. ESL, voice A vs. voice B) and want to know what the syntactic signature of the difference actually is, this script surfaces the bigram-level evidence behind the aggregate KL number.

---

## aic_pattern_audit.py

The framework's first scriptable Layer B/C tool. Counts named rhetorical patterns from `references/aic-flags.md` and `references/source-triage.md` in a target document, reports per-thousand-word density, and (with `--baseline-dir`) compares against a baseline corpus to flag patterns whose density exceeds the writer's voice envelope.

The variance audit and bigram diffs operate at Layer A distributional signals. This script operates one level up at the rhetorical-figure level: it counts the same kinds of patterns the source-triage skill catches manually. Layer C source triage (earned vs. unearned per instance) still requires the writer's judgment; the script's role is to surface candidates and quantitative density signals so the writer can adjudicate efficiently.

### Usage

```
python3 aic_pattern_audit.py target.md
python3 aic_pattern_audit.py target.md --baseline-dir personal_pre_ai/
python3 aic_pattern_audit.py target.md --pattern correctio --pattern pseudo_aphorism --top 30
python3 aic_pattern_audit.py target.md --baseline-dir personal_pre_ai/ --json
```

### Patterns detected (v1)

Fiction patterns (per `source-triage.md`):

- **negation_hedge**: `Not X.` followed by an affirming sentence. Earned when the writer is actively sorting; unearned when the negation is narrator pose.
- **correctio**: inline `not X, but Y` plus the `It is not X. It is Y` frame. Cuts on the payoff test if the affirm sentence repeats the negate.
- **pseudo_aphorism**: gnomic generalization frames (`X as Y`, `is the Y of Z`, `There is a kind of X in every Y`).
- **manifesto_cadence**: 3+ consecutive sentences with the same anaphoric head. Earned when each escalates, restricts, or reveals.

Structural / craft patterns:

- **triplet**: 3- or 4-item comma-and lists. Classical figure but at high density reads as rhythmic fill.
- **professional_parallel_stack**: 3+ adjacent paragraphs with the same opening clause structure (`A X may use them`, `A Z may use them`).

Nonfiction parallel patterns (per `source-triage.md`):

- **false_balance**: `while reasonable people may disagree`, both-sidesing without specifying the disagreement.
- **hedge_and_affirm**: `while X is generally true, in some cases Y` performs caution while saying nothing definite.
- **recommendation_template**: `DC must commit to`, `we urge X`, generic-actor + modal + generic-verb.
- **authority_laundering**: `research has shown`, `experts agree` without naming the research or the experts.

### Output

Markdown by default. Header reports target word count, total pattern hits, and (with baseline) the loaded baseline file count. Summary table shows per-pattern hit count, target density per 1k words, baseline density per 1k words (if supplied), Δ per 1k, and a heuristic severity flag (above 2× baseline, +5/k above baseline, or absent in baseline).

For each pattern with one or more hits, the report renders the flagged instances with sentence indexes, full sentence text, and the regex-matched substring. The writer reviews these for Layer C source-triage adjudication.

`--json` switches to machine-readable output preserving the same structure with `task_surface: craft_restoration`. Useful for piping into a revision pass: a downstream tool can read the JSON, extract flagged sentences, and ask an LLM for revision suggestions on those specific sentences.

### Markdown blockquote handling

By default the script strips lines starting with `>` (markdown blockquote lines) before processing, on the assumption that quoted passages usually contain other writers' prose and should not inflate the writer's own pattern density. Pass `--keep-quotes` to disable this stripping.

### Known v1 limitations

The disguised-correctio detector matches only the explicit `not X, but Y` inline form and the `It is not X. It is Y` frame. Subtler multi-sentence correctios (`Detection measures X. What it cannot do is Y` and similar non-pronominal-subject pivots) are not captured. v2 will add a sentence-pair detector that looks for negation-then-affirmation patterns across two adjacent sentences with semantic overlap.

Two patterns from the source-triage taxonomy are deferred to v2 because they require richer analysis: **abstraction shielding** (needs named-entity recognition + abstractness scoring to distinguish "stakeholders, communities of color" gestures from earned class-noun usage) and **indefinite-pronoun gesture** (needs context analysis to distinguish narrator-tic from cognitively-loaded indefinite reference).

### Layer C verdict still belongs to the writer

The script reports candidate instances. It does not tell the writer which instances are earned. The framework's deepest principle is that source triage is the writer's call per instance; the script is a diagnostic that surfaces patterns to triage, not an automated triage verdict.

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
- `use: voice_profile` or `use: idiolect` AND `privacy != private` is a warning (a voiceprint is a voice-cloning input).
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

## check_corpus.py

Content-level corpus hygiene gate. Where `manifest_validator.py` checks labels,
paths, and provenance, `check_corpus.py` checks whether the text files contain
suspected non-prose contamination that would be stripped by SETEC preprocessing:
HTML/CSS/JS scaffolding, Markdown code, loose CSS blocks, JSON-shaped blocks,
ASCII tables, YAML front matter, and related markup.

### Usage

```
# Check a manifest-selected baseline slice
python3 check_corpus.py --manifest corpus_manifest.jsonl --filter use=baseline

# Check a loose directory or individual file
python3 check_corpus.py --dir BASELINE_DIR
python3 check_corpus.py --path draft.md --path companion.md

# JSON for validation dashboards
python3 check_corpus.py --manifest corpus_manifest.jsonl \
  --filter use=baseline --json
```

### Thresholds

Each file is classified by the fraction of tokens that preprocessing would
strip:

| status | condition |
|---|---|
| `clean` | below `--warn-threshold` |
| `warning` | at or above `--warn-threshold`, below `--fail-threshold` |
| `fail` | at or above `--fail-threshold` |

Defaults are `--warn-threshold 0.01` and `--fail-threshold 0.05`. The fail
threshold matches the 5% warning used in the audit scripts. The command exits
1 when any file fails or cannot be read; otherwise it exits 0. This makes it
usable as a preflight gate before validation or KL-sensitive baseline runs.

### Output shape

Markdown by default; JSON carries top-level `task_surface: "validation"`,
aggregate stripped-token counts, rule totals, thresholds, and one record per
file. Pass `--show-stripped` to include representative stripped snippets in
JSON for debugging. The script never rewrites the source files.

Importable:

```python
from check_corpus import check_corpus_paths

result = check_corpus_paths(["baseline/file.md"])
if result["status"] == "fail":
    raise RuntimeError("Corpus hygiene gate failed")
```

---

## validation_harness.py

Empirical validation over labeled manifest entries. MVP scope evaluates the
`smoothing_diagnosis` surface by running `variance_audit.py` logic on entries
tagged `use: validation` and scoring each document by
`compression.compression_fraction`.

### Usage

```
# Ranking metrics only: no thresholded rates without an operating point
python3 validation_harness.py corpus_manifest.jsonl

# Set an explicit operating point and report FPR/TPR/FNR/precision
python3 validation_harness.py corpus_manifest.jsonl --fpr-target 0.01

# JSON output for downstream dashboards
python3 validation_harness.py corpus_manifest.jsonl --fpr-target 0.01 --json

# Fail fast if selected validation entries contain HTML/CSS/code/table contamination
python3 validation_harness.py corpus_manifest.jsonl --check-corpus

# Faster Tier-1-only validation pass
python3 validation_harness.py corpus_manifest.jsonl --no-tier2 --no-tier3

# Reproducible smoke fixture
python3 validation_harness.py test_data/validation_smoke_manifest.jsonl \
  --no-tier2 --no-tier3 --fpr-target 0.01 --seed 7

# Unicode-layer adversarial fixture slice
python3 validation_harness.py test_data/adversarial/validation_adversarial_manifest.jsonl \
  --no-tier2 --no-tier3 --metric-bootstrap-resamples 0
```

### Labels

Default positive `ai_status` values: `ai_generated`, `ai_assisted`,
`ai_edited`. Default negative/control value: `pre_ai_human`. `mixed` is not
part of the default binary frame; it remains visible in the per-`ai_status`
slice and record output unless you explicitly map it.
Entries with other `ai_status` values are included in record output but
excluded from binary metrics unless you map them with repeated
`--positive-status` or `--negative-status` flags.

### Metrics

The harness reports:

- ROC AUC and average precision, with paired bootstrap CIs, when a slice has at least one positive and one negative scored record.
- Score distributions by label.
- Optional thresholded confusion/rate metrics when `--fpr-target` is supplied.
- Wilson confidence intervals for FPR, TPR/recall, FNR, specificity, and precision.
- Slices by register, length bucket, language status, adversarial class, and AI status.
- Optional corpus-hygiene preflight when `--check-corpus` is supplied.

Ranking CIs use a paired bootstrap over `(label, score)` rows. Set
`--metric-bootstrap-resamples` to control the resample count (default 2000;
pass 0 to disable), and `--seed` for reproducible resampling.

`--fpr-target` is a fraction, not a percent: `0.01` means 1% FPR, while
`0.0001` means 0.01% FPR. The latter is the accusation-grade target
discussed in the roadmap; small smoke fixtures will usually be too small to
make such a target informative.

Thresholded rates are in-sample in the MVP: the threshold is selected and
evaluated on the same validation entries. Treat those rates as calibration
leads until a separate calibration/test split lands.

Markdown reports show the first 100 records by default. Use
`--records-limit 0` to show all records, `--no-records-table` to omit the
table, or `--json` for complete structured output.

Pass `--check-corpus` to run `check_corpus.py`'s content gate on the selected
validation entries before scoring. The harness exits 1 if any selected file
hits the corpus fail threshold (`--corpus-fail-threshold`, default 0.05), and
otherwise includes a `corpus_hygiene` block in JSON/Markdown. This is opt-in
for now so historical validation runs stay reproducible; use it for any run
where POS-bigram KL or other preprocessing-sensitive signals are part of the
claim.

### Adversarial fixtures

`test_data/adversarial/` contains public-safe Unicode-layer stress fixtures
derived from the bundled `ai_sample.txt`: zero-width-space insertion,
Cyrillic homoglyph substitution, and soft-hyphen insertion. Their labels inherit
the source sample's `ai_status`; the adversarial metadata lives in
`adversarial_class`, `source_id`, and `transform` fields. The harness reports a
`by_adversarial_class` slice so these stress cases do not disappear into the
aggregate. The helper script `adversarial_fixtures.py` generates the deterministic
Unicode transforms; paraphrase and humanizer fixtures remain a private/research
follow-up.

`scikit-learn` supplies the ranking metrics when installed; `statsmodels`
supplies proportion intervals. The script has stdlib fallbacks for smoke tests
in a minimal environment, but the recommended install path is
`pip install -r requirements.txt`.

### Claim license

The report deliberately does not publish a single aggregate accuracy number.
Thresholded rates appear only when the caller supplies an explicit FPR target.
The claim it licenses is narrow: performance on this labeled manifest, at this
operating point, sliced by the manifest metadata. It does not prove provenance
for any individual document and does not generalize outside the validation set.

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

## idiolect_detector.py

Extracts unusually characteristic words and phrases from a target corpus against
a reference corpus. This is a voice-coherence tool: its output is a preservation
list for revision prompts ("do not normalize these phrases"), not an authorship
or AI-provenance verdict.

### Usage

```
# Target directory against a local reference directory
python3 idiolect_detector.py \
  --target-dir TARGET_CORPUS/ \
  --reference-dir REFERENCE_CORPUS/ \
  --out ../ai-prose-baselines-private/target_idiolect.md

# Manifest-driven target and reference selections
python3 idiolect_detector.py \
  --manifest corpus_manifest.jsonl \
  --filter use=idiolect,persona=essay_voice \
  --reference-manifest corpus_manifest.jsonl \
  --reference-filter use=negative_baseline,register=blog_essay \
  --preservation-output ../ai-prose-baselines-private/essay_preserve.txt

# Built-in broad reference, when NLTK Brown is installed/downloaded
python3 idiolect_detector.py \
  --target-dir TARGET_CORPUS/ \
  --reference-corpus brown
```

### What it reports

The script scores 1-, 2-, and 3-grams by default. Each row includes raw counts,
per-1k rates, a smoothed log2 ratio, a keyness score, and (for multiword
phrases) a collocation score. The default keyness method is log-likelihood
(`G2`); alternatives are `chi_square`, `pmi`, and `fisher_exact`.

Multiword candidates must pass both keyness and phrase-cohesion filters by
default. Bigram cohesion uses a likelihood-ratio association score plus a PMI
floor (`--min-collocation-lr 10.83`, `--min-collocation-pmi 3.0`). Trigrams use
PMI fallback in the stdlib path. Pass `--no-collocation-filter` if you want the
raw keyness list even when phrase association is weak.

The preservation list uses per-n quotas by default: `--preservation-quotas
20,20,10` for unigrams, bigrams, and trigrams, then backfills by score up to
`--preservation-top` (default 50).

### Privacy

Idiolect output is voice-cloning-grade data. By default, `--out` and
`--preservation-output` refuse to write outside a path containing
`ai-prose-baselines-private/`. Pass `--allow-public-output` only for synthetic
fixtures or an intentionally shareable corpus. Stdout is allowed for interactive
work, but the script prints a privacy warning to stderr.

The manifest validator recognizes `use: idiolect` and applies the same privacy
ratchet as `use: voice_profile`: any value other than `privacy: private`,
including a missing field, produces a warning.

---

## acquire_blog.py

Acquires a single author's blog or Substack archive into the impostor pool that
the future General Imposters validation harness will consume. Auto-detects which
extraction path to use based on the URL pattern and probe responses:

- **Substack** (`*.substack.com` or Substack-shaped feed at `<url>/feed`) — RSS
  for recent posts (full text) plus `sitemap.xml` for the full archive.
- **WordPress / Ghost** (responds with WP/Ghost-shaped feed at `/feed/` or
  `/rss/`) — feed parse plus a per-post HTML fetch when the feed body is short.
- **Generic HTML archive** (no recognizable feed) — requires `--archive-pattern`
  pointing at the index page; default link heuristic catches `/YYYY/MM/`-style
  and `/posts/` URLs.
- **Wayback Machine** (`--wayback`) — uses the CDX API to enumerate snapshots
  within the date window; for shut-down blogs.

### Usage

```
# Substack (auto-detected from hostname):
python3 scripts/acquire_blog.py https://jehsmith.substack.com \
    --persona smith_jeh_substack \
    --impostor-for blog \
    --register blog_essay \
    --consent-status fair_use_research \
    --era pre_chatgpt \
    --since 2018-01-01 --until 2022-11-01 \
    --max-posts 25

# WordPress / Ghost:
python3 scripts/acquire_blog.py https://example.com \
    --wordpress \
    --persona example_author_blog \
    --impostor-for blog \
    --register blog_essay \
    --consent-status fair_use_research

# Generic HTML archive (e.g., Marginal Revolution):
python3 scripts/acquire_blog.py https://marginalrevolution.com \
    --html-archive \
    --archive-pattern 'https://marginalrevolution.com/marginalrevolution/2019/03' \
    --persona cowen_tyler_blog \
    --impostor-for blog \
    --register blog_essay \
    --consent-status fair_use_research

# Wayback Machine for shut-down blogs:
python3 scripts/acquire_blog.py https://slatestarcodex.com \
    --wayback \
    --persona alexander_scott_blog \
    --impostor-for blog \
    --register blog_essay \
    --consent-status fair_use_research \
    --since 2014-01-01 --until 2020-06-01
```

### Output

Per acquired post: `<output-dir>/<YYYY-MM-DD>_<title-slug>.txt` (cleaned text)
plus a `<...>.meta.json` sidecar (URL, date, hash, raw byte length, scraper
version, full preprocessing metadata block). Default output dir is
`<baselines>/impostors/<register>/<persona>/`; the baselines root resolves
through `$SETEC_BASELINES_DIR`, then a sibling `ai-prose-baselines-private/`
next to the repo, then a fallback under `~/Documents/`.

Draft manifest written to `<output>/draft_manifest.jsonl` by default. Each entry
carries `corpus_role: "impostor"`, `use: ["voice_impostor"]`, `split: "baseline"`,
`privacy: "private"`, plus all five impostor-required fields (`impostor_for`,
`register_match`, `topic_match`, `consent_status`, `era`) and `acquired_via`
keyed by source-type and date. After review, the user merges the draft into
`corpus_manifest.jsonl`.

### Privacy

Acquired text is voice-cloning input from someone else's prose. By default, the
output dir, manifest, and `--out` summary all live under a path containing
`ai-prose-baselines-private/`; the privacy guard refuses non-private paths
unless `--allow-public-output` is set (rare; only for non-personal corpora).
Impostor entries are never published or distributed; future public-report
harnesses must anonymize impostor identities by default.

### Robots.txt

The script honors robots.txt by default and ships no `--ignore-robots` flag in
v1. Per-host rate limiting (`--rate-limit SECONDS`, default 2.0) prevents
hammering a single archive.

### Paid Substack content

Paid posts come excerpt-only and are detected via class markers (`paywall`,
`subscriber-only`) and the `audience: only_paid` feed field. v1 always skips
them; the run summary records `Skipped (paid-only): N`.

### Dependencies

Install the opt-in acquisition layer:

```
pip install -r requirements-acquisition.txt
```

This pulls `requests`, `feedparser`, `beautifulsoup4`, `lxml`, `python-dateutil`,
and `pypdf`. Ordinary diagnostics, validation, voice distance, and plugin
installation do NOT need this layer.

### Manual live-smoke

CI tests run against fixture HTTP responses (`scripts/test_data/acquisition_blog_fixture/`)
for reproducibility. The maintainer's manual live-smoke command is documented
in `internal/2026-05-08-impostor-corpus-spec.md`; run it after any change to
the Substack extraction path.

---

## acquire_blogger_takeout.py

Imports a Google Takeout Blogger export into the same impostor-pool format as
`acquire_blog.py`, without touching the live site. This is the preferred path
when an author has shared their Blogger/Blogspot Takeout archive: it is more
complete than Blogger's public feed caps, avoids network scraping, and preserves
stable Blogger post IDs in sidecar metadata.

By default, the importer only reads `Blogger/Blogs/*/feed.atom`. It excludes
`Blogger/Comments/*/feed.atom` unless `--include-comments` is passed, because
comment feeds are a different register and may contain conversational context or
other people's prose.

### Usage

```
python3 scripts/acquire_blogger_takeout.py /path/to/Takeout \
    --persona critical_animal_blog \
    --author "Critical Animal" \
    --impostor-for anotherpanacea_blog \
    --register blog_essay \
    --consent-status author_consent \
    --era pre_chatgpt \
    --until 2022-11-01 \
    --min-words 250
```

The positional path may be the Takeout root, a `Blogger/` directory, or a single
`feed.atom` file. Output defaults to
`<baselines>/impostors/<register>/<persona>/`; pass `--output-dir` and
`--emit-manifest` to override. Untitled Blogger posts are retained with stable
`untitled-<post-id>` filenames so same-day titleless posts do not overwrite each
other.

---

## pdf_inventory.py

Inventories an existing PDF library so the user can review which files should
join the impostor pool before extraction. Walks `--root`, opens every `.pdf`
found, samples the first 5 pages, and emits a JSONL row per file:

- `text_extractable` — first-five-pages sample > 100 chars; `pdf_extract.py`
  will use the text layer.
- `image_only` — sample is empty; `pdf_extract.py` will need OCR (or skip).
- `mixed` — sample is 1–100 chars; partial OCR layer or partial text. OCR
  recommended for completeness.
- `corrupted` — `pypdf` failed to open the file; `notes` carries the exception
  class so the user can spot category failures.

Each row also reports `metadata_quality` (`good` / `partial` / `none` based on
title / author / creation_date completeness), an `estimated_words` count
extrapolated from the sample, a `has_ocr_layer` heuristic (text + images on
the same page suggest a prior OCR pass), and the file's SHA-256 for inter-row
deduplication.

The inventory is the **review surface**. It never writes cleaned text and
never emits a manifest entry. The user keeps the rows that should join the
pool, annotates them with persona / register / consent metadata (see below),
and feeds the filtered file to `pdf_extract.py`.

### Usage

```
python3 scripts/pdf_inventory.py \
    --root ~/Documents/papers \
    --output ../ai-prose-baselines-private/pdf_inventory.jsonl

# Restrict to subset:
python3 scripts/pdf_inventory.py \
    --root ~/Documents/papers \
    --include-glob '**/honig*.pdf' \
    --include-glob '**/arendt*.pdf' \
    --max-files 50 \
    --output draft_inventory.jsonl

# Verbose progress on a large library:
python3 scripts/pdf_inventory.py \
    --root ~/Documents/papers \
    --workers 4 \
    --verbose \
    --output ../ai-prose-baselines-private/pdf_inventory.jsonl
```

### Filtering between inventory and extraction

After `pdf_inventory.py`, the user edits the JSONL to:

1. Drop rows that shouldn't join the impostor pool (corrupted files, unwanted
   topics, image-only PDFs that aren't worth the OCR cost).
2. Add the impostor metadata fields the manifest validator requires:
   `persona`, `register`, `register_match` (`high` / `medium` / `low`),
   `topic_match`, `consent_status`, `era`, `impostor_for` (list of target
   personas).

A small `jq` recipe to add the same metadata to every row:

```
jq -c '. + {persona: "honig_bonnie_personal", register: "academic_philosophy",
            register_match: "high", topic_match: "medium",
            consent_status: "fair_use_research", era: "pre_chatgpt",
            impostor_for: ["philosophy"]}' \
   pdf_inventory.jsonl > pdf_inventory_filtered.jsonl
```

Privacy: PDF metadata can leak personal information. The privacy guard treats
the `--output` path the same way `voice_profile.py` treats voice profiles —
must live under any directory named `ai-prose-baselines-private` unless
`--allow-public-output` is set.

### Dependencies

`pypdf` from `requirements-acquisition.txt`. The inventory step does not
require OCR.

---

## pdf_extract.py

Extracts plain text from PDFs flagged in a filtered inventory. Text-extractable
files go through `pypdf`; image-only / mixed files go through `ocrmypdf` (when
available). Each successful extraction produces:

- `<output-dir>/<persona-slug>/<YYYY-MM-DD>_<title-slug>.txt` (cleaned text,
  same `preprocessing.py` corpus-hygiene gate as identity baselines and live
  blog acquisition).
- A `.meta.json` sidecar (source path, raw byte length, content hash, full
  preprocessing metadata block, `acquired_via: pdf_extract_<text_layer|
  ocrmypdf>_<date>`).
- A draft manifest entry with `corpus_role: impostor`,
  `use: ["voice_impostor"]`, `split: "baseline"`, `privacy: "private"`, and
  every impostor-required field copied from the filtered inventory row.

### Usage

```
# Fast first pass: only text-extractable entries, no OCR.
python3 scripts/pdf_extract.py \
    --inventory pdf_inventory_filtered.jsonl \
    --output-dir ../ai-prose-baselines-private/impostors/academic_philosophy/ \
    --skip-ocr

# Full pass with OCR (requires ocrmypdf + tesseract + ghostscript + qpdf).
python3 scripts/pdf_extract.py \
    --inventory pdf_inventory_filtered.jsonl \
    --output-dir ../ai-prose-baselines-private/impostors/academic_philosophy/ \
    --workers 2

# Dry run to see what would be extracted:
python3 scripts/pdf_extract.py \
    --inventory pdf_inventory_filtered.jsonl \
    --output-dir ../ai-prose-baselines-private/impostors/academic_philosophy/ \
    --dry-run
```

### Skips

`pdf_extract.py` is conservative about what it writes:

- Inventory rows with `classification: corrupted` are skipped silently.
- Rows missing any impostor-required field (`persona`, `register`,
  `register_match`, `topic_match`, `consent_status`, `era`, `impostor_for`)
  are skipped with a stderr notice — the validator would reject the resulting
  manifest, so we catch it earliest.
- Files that hash-match an already-acquired entry in the same author subdir
  are skipped as duplicates. Two PDFs of the same essay (a journal preprint
  and a republished collection version, for example) hash the same after
  preprocessing and one wins.
- Image-only / mixed entries are skipped when `--skip-ocr` is set or when the
  OCR dependency layer (`ocrmypdf` + tesseract + ghostscript + qpdf) is
  unavailable. The first row that needs OCR triggers a one-time stderr notice
  explaining how to install the layer; subsequent skips are quiet.

### OCR notes

`ocrmypdf` is the best wrapper around tesseract because it handles deskew,
despeckle, and image preprocessing automatically. For academic PDF photocopies
the default settings produce 85–95% character accuracy — sufficient for
stylometric work where POS-bigram and function-word distributions are robust
to occasional errors.

OCR is slow: figure 30–90 seconds per 20-page paper depending on image quality
and DPI. For thousands of files, batched processing with `--workers N` helps.
Realistic throughput on a modern Mac: 100–200 papers per hour.

Install the OCR layer on macOS:

```
pip install ocrmypdf
brew install tesseract ghostscript qpdf
```

If any of those is missing, `pdf_extract.py` reports the missing component and
either skips the OCR-needing rows or refuses cleanly when `--skip-ocr` is not
set.

### Privacy

Default output goes under
`ai-prose-baselines-private/impostors/<register>/<persona>/`. The privacy
guard refuses non-private output paths unless `--allow-public-output` is set.
Extracted PDF text is voice-cloning input from someone else's prose; treat it
exactly like the user's own baseline corpus.

---

## acquire_magazine.py

Acquires literary-horror short fiction from online magazine archives. Site-
specific scraper modules behind a uniform CLI. v1 ships with two working
magazines (Nightmare and The Dark); both run on WordPress with similar
issue-archive shapes. Additional magazines are deferred to v2 unless trivial.

The intended use case is impostor-pool acquisition for the General Imposters
validation harness: a register-matched sample of contemporary literary-horror
prose from named writers (`--filter-author Brian Evenson Kelly Link`) that
the harness can compare against the user's own fiction baseline.

### Usage

```
# All Nightmare stories by Brian Evenson and Kelly Link, 2014–2022:
python3 scripts/acquire_magazine.py \
    --magazine nightmare \
    --persona-from-author \
    --register literary_horror \
    --consent-status fair_use_research \
    --era pre_chatgpt \
    --filter-author "Brian Evenson" "Kelly Link" \
    --since 2014-01-01 --until 2022-11-01 \
    --impostor-for fiction

# Everything in The Dark since 2018, capped at 30 stories:
python3 scripts/acquire_magazine.py \
    --magazine the_dark \
    --persona-from-author \
    --register literary_horror \
    --consent-status fair_use_research \
    --since 2018-01-01 \
    --max-stories 30 \
    --impostor-for fiction
```

### Per-magazine modules

`MAGAZINE_MODULES` registry in `acquire_magazine.py` keys each module by its
CLI choice; the entry holds CSS selectors for the issue archive, issue TOC,
story permalink, story body, byline, title, date, and a `strip_after_selector`
that removes post-body cruft (the Nightmare "Author Spotlight" interview
block, The Dark's ebook-purchase widget). Adding a new magazine is a one-entry
extension once you've identified the right selectors.

### Persona slug rule

`--persona-from-author` (default) mints one persona slug per author following
the documented `lastname_firstname_personal` rule: normalize to ASCII,
lowercase, strip punctuation, split on whitespace/hyphen, then emit
`<lastname>_<firstname>_personal` for two-or-more-token names. Same author →
same slug across runs, so the impostor pool stays per-author sliceable.

`--persona STRING` overrides the rule and lumps every acquired story under
one slug. Rarely useful for impostor work; included per spec.

### Author filter

`--filter-author` is a case-insensitive substring match against the byline
text (after stripping the leading `By ` prefix magazines often add). Pass
multiple author names to match any of them. Filter is applied both at
issue-TOC discovery (when bylines are present in the TOC) and again on the
story page (the canonical byline source) so a TOC truncation can't slip a
filtered-out story through.

### Output and manifest

Per-piece output: `<output-dir>/<persona-slug>/<YYYY-MM-DD>_<title-slug>.txt`
plus a `.meta.json` sidecar; manifest entries carry
`acquired_via: acquire_magazine_<magazine-name>_<date>`. Default output dir is
`<baselines>/impostors/<register>/<magazine>/`; pass `--output-dir` to
override.

Same `preprocessing.py` corpus-hygiene gate as identity baselines and live
blog acquisition. Within-persona dedupe by content hash (a story republished
in two issues hashes the same and only the first wins).

### Privacy and robots

Output path checked against the marker-based privacy guard
(`ai-prose-baselines-private/...`); `--allow-public-output` to override. v1
honors robots.txt and ships no override flag. Per-host rate limit
(`--rate-limit SECONDS`, default 2.0) prevents hammering a single archive.

### Manual live-smoke

CI tests run against fixture HTML responses (`scripts/test_data/
acquisition_magazine_fixture/`) for reproducibility. The maintainer's
documented manual live-smoke command from the spec: Nightmare filtered to
Brian Evenson and Kelly Link, since 2014, until 2022-11. Expected
historically: 5–15 stories total across both authors, subject to archive
drift.

---

## generate_voice_report.py

Consumes the JSON outputs of `voice_profile.py`, `voice_drift_tracker.py`, and
`idiolect_detector.py` and emits an author-facing markdown report shaped like
the canonical template at `references/templates/voice_insights_report.template.md`.

The report follows an architectural split the framework considers load-
bearing:

- **Numerical sections** are populated programmatically — header counts,
  durable voiceprint tables (CV-filtered features per family), idiolect
  tables (topic-domain + rhetorical-move signatures), cross-period distance
  matrix, drifting / stable feature lists, comparison-to-control headline
  magnitudes.
- **Interpretive sections** are emitted as `{TODO: interpret: <hint>}`
  markers with enough context (which feature, which direction, which
  magnitude) for an LLM/human pass to write the prose downstream. The script
  does not try to auto-generate the interpretive readings; the framework's
  deepest principle is that the writer's local read decides.

Three report shapes are auto-selected by which inputs are present: profile-
only (`--voice-profile` only), profile + drift (adds drift section), profile
+ drift + comparison (adds comparison-to-control section).

### Usage

```
# Profile + drift + idiolect, no comparison:
python3 scripts/generate_voice_report.py \
    --voice-profile path/to/voice_profile.json \
    --voice-drift path/to/drift.json \
    --idiolect-n1 path/to/idiolect_n1.json \
    --idiolect-n2 path/to/idiolect_n2.json \
    --idiolect-n3 path/to/idiolect_n3.json \
    --author-name "Author Name" \
    --corpus-label "Author's blog" \
    --register blog_essay \
    --ai-disclosure "no AI use on this blog at any point" \
    --out ../ai-prose-baselines-private/voice_insights.md

# With a confirmed-human matched-window control for comparison:
python3 scripts/generate_voice_report.py \
    --voice-profile subject_profile.json \
    --voice-drift subject_drift.json \
    --comparison-drift control_drift.json \
    --idiolect-n1 idiolect_n1.json \
    --idiolect-n2 idiolect_n2.json \
    --author-name "Subject" \
    --corpus-label "Subject's blog" \
    --control-writer-name "Control Writer" \
    --register blog_essay \
    --out ../ai-prose-baselines-private/cross_boundary_report.md
```

### LLM editorial pass

The emitted report is a draft. Run an LLM pass over it (or write the
interpretations by hand) to fill the `{TODO: interpret}` markers. The hints
in each marker carry enough context that an LLM with the report as input
can produce reasonable interpretive prose without needing the source JSON.
Save the populated report alongside the original draft for diff review.

### Privacy

Reports contain voiceprint signatures — voice-cloning input. Default `--out`
paths must live under `ai-prose-baselines-private/`. Stdout is allowed
without the override flag for interactive use; piping into a file outside
the private root requires `--allow-public-output`.

### Reference reports

Three reference reports produced during framework development sit (privately,
under the user's `ai-prose-baselines-private/`) at `impostors/blog_essay/
critical_animal_blog/_analysis/critical_animal_voice_insights.md` (single-
corpus profile shape), `joshua_voice_drift/joshua_drift_insights.md` (profile
+ drift shape), and `scu_voice_drift/scu_drift_insights_and_comparison.md`
(profile + drift + comparison shape). These are not committable but pin what
the populated report should look like after the LLM pass.

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
| `use` | Usually includes `baseline`, `voice_profile`, `idiolect`, `negative_baseline`, `validation`, or `exclude` |
| `split` | `baseline`, `train`, `test`, `holdout` |
| `privacy` | `private`, `shareable`, `public_domain` |
