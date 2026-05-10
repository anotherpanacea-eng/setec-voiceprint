# Voice insights report template

This is the canonical template for author-facing voice insights reports generated from SETEC stylometric output. Distilled from two internal reference reports produced during framework development: a single-corpus profile and a cross-corpus drift comparison.

## Two report shapes

The template covers two related but distinct report shapes. Pick one based on what the corpus supports.

**Profile-only.** A single-snapshot voiceprint of the writer at one period. Use when the corpus does not span multiple periods, or when the comparison-across-time question isn't the focus. Sections: Header → Durable voiceprint → Idiolectic vocabulary → Three observations → What this cannot say.

**Profile + drift.** Voiceprint plus drift analysis across two or more periods. Use when the corpus spans an interesting boundary (AI-availability, register shift, career stage). Sections: Header → Durable voiceprint → Idiolectic vocabulary → Era / drift → Three observations → What this cannot say.

**Profile + drift + comparison.** As above, plus comparison to a confirmed-human matched-window control. Use when a calibration anchor exists. Adds a "Comparison to [control writer]" section after the drift section.

The skeleton below covers all three; remove sections that don't apply.

---

# {Author display name}: Voice profile insights

A reading of the SETEC voiceprint output for {corpus name} ({date range, e.g. "2008-06 through 2022-10"}, {N posts}, {N words} words). Author-facing — meant to surface things that might be interesting to the writer about their own voice.

The framework that produced these numbers measures voice as patterns at the level of function words, character n-grams, punctuation cadence, paragraph structure, and pronoun/modal/negation profile. None of it asks whether the prose is good or bad; it asks what's distinctive, what's stable across time, and what has shifted.

{Optional: AI-status disclosure block. Use when the writer has affirmatively disclosed AI involvement status:}
> The writer has affirmatively disclosed: {disclosure, e.g. "no AI use on this blog at any point", "AI involvement began {date}", "uncertain — assume mixed"}. The framework treats this disclosure as ground truth for the analysis below.

## What the profile pins down as durable

Some features are extremely stable across the entire {N-year} span. These are the markers that make any given {post / essay / chapter} unmistakably theirs — the prose breathing pattern that doesn't shift even as topics, paragraph length, and rhetorical mode evolve.

{For each feature family with at least one CV<0.10 feature, write a paragraph naming the feature, its mean value, its CV, and what kind of writing the level corresponds to. Format example:}

**{Feature category}:** {feature_name} {value with units}, with a coefficient of variation of {CV_value}. {Interpretation: what this level signals about the register or rhetorical mode.}

{Repeat for each notable durable feature. Aim for 3-6 paragraphs. Examples from past reports:}

> **Punctuation density:** 15.87 marks per 100 words, with a coefficient of variation of 0.048. That's nearly invariant across {span} years. To put it in context: a stable feature with CV under 0.10 is rare across a long writing corpus, and CV under 0.05 is exceptional. This is a load-bearing identity signal.
>
> **Question density:** 6.45 questions per 100 sentences (CV 0.225). That's unusually high for nonfiction prose; typical academic-philosophical writing runs around 2-3 per 100. {Author} writes in a more genuinely interrogative mode than the philosophical-blog register usually encourages.

## Idiolectic vocabulary

Beyond the structural features, the framework can ask which specific words and phrases {Author} uses at unusual densities relative to general English (NLTK Brown reference corpus). This surfaces three things at once: topic-domain terminology, technical vocabulary the field uses, and the specific phrasings that recur often enough to function as authorial signature.

### Topic-domain phrases

The phrases below appear at the densities shown in {Author}'s corpus and almost never appear in the Brown reference. Some are conceptual frames they work with explicitly; others are technical vocabulary inherited from their field; a few are coined or refined enough that they read as theirs.

| Phrase | Per 1000 words | Reading |
|---|---:|---|
| `{phrase}` | {rate} | {one-line interpretation} |

{Interpretation paragraph: what this surfaces about the writer's intellectual lineage, field, conceptual frames they work with or against. Distinguish topic-domain (inherited) vocabulary from coined/refined phrases.}

### Rhetorical-move signatures

These are bigrams and trigrams that aren't topic-vocabulary but rhetorical moves — the way the writer constructs claims, hedges them, transitions, paraphrases.

| Phrase | Count | What it signals |
|---|---:|---|
| `{phrase}` | {count} | {one-line interpretation} |

{Interpretation paragraph: which moves stand out, what they signal about the writer's rhetorical habits. Examples from past reports include: high "I think" density, frequent paraphrase markers like "in other words", framing moves like "the question of", quantifier register, blog-format-specific tics like "here and here".}

### Where idiolect signals topic vs. signals voice

A phrase like `{topic-domain example}` is high-keyness because it's {field-name}; if a different writer worked in that field, they'd use it too. But a phrase like `{voice-marker example}` is voice rather than topic. The first cluster places {Author} in their disciplinary tradition; the second cluster makes any individual {post/essay} read as theirs.

For revision purposes, the voice-rather-than-topic phrases are what the SETEC framework calls "preservation candidates" — phrases the writer's natural register uses, that an editor (human or AI) might smooth out without realizing they were carrying voice.

## Era / drift {include this section only when the corpus spans multiple periods}

The drift report disaggregates the corpus by {time unit, e.g. "year" or "custom periods at {boundary date}"} and computes voice distance between periods. {Brief sentence summarizing whether drift is large, moderate, or small.}

### Cross-period magnitudes

| Comparison | Burrows-Delta | Cosine |
|---|---:|---:|
| {Period A} → {Period B} | {BD} | {Cos} |

### What's drifting

{For each cluster of meaningfully drifting features, write a paragraph or short table. Group by feature family or by interpretation cluster. Examples from past reports:}

> **{Cluster name}:** {feature_a} {direction and magnitude}, {feature_b} {direction and magnitude}. {Interpretation: what kind of register or workflow change this is consistent with.}

### What's stable through the drift

The writer's core voice features held steady through the period: {list the CV<0.10 features with their values}. {Brief interpretation: the deep idiolect didn't shift even as surface texture moved.}

## Comparison to {control writer name} {include this section only when a confirmed-human matched-window control is available}

{Open with the headline finding. Most-likely shape:}

{Subject's} {drift type} magnitude (BD {Subject_BD}) is {comparable to / smaller than / larger than} {Control's} {control drift type} drift (BD {Control_BD}). {Interpret what this means: drift magnitude alone is or is not diagnostic.}

What IS diagnostic is **drift shape** — which features moved which way. {Compare specific signatures:}

### {Diagnostic signature 1}

**{Subject}:** {what Subject's data shows}.

**{Control}:** {what Control's data shows, ideally moving differently}.

{One-paragraph interpretation: what this divergence does or doesn't suggest about workflow differences.}

### {Diagnostic signature 2}

{Same structure.}

### {Diagnostic signature 3}

{Same structure.}

## Three observations to flag

{Pick three findings from the data that are worth the writer's attention specifically. Format as standalone paragraphs, not bullet points. Be concrete: name the feature, its value, what it signals.}

The first observation: {finding}.

The second observation: {finding}.

The third observation: {finding}.

## What this analysis cannot say

The voiceprint measures presence and pattern of features. It doesn't say whether the writing is good. It doesn't say whether drift is improvement or decline. It doesn't say whether any individual {essay / post / chapter} is "really" the writer's voice or "really" something else. Most of these features are robust to topic but vulnerable to register; a deliberate stylistic experiment will show up as drift even if the writer is fully in command of the experiment.

It also can't tell you anything about provenance — whether any {post / essay} is AI-assisted or fully hand-written. {If the corpus has documented AI status, add: "The corpus is dated through {date}, and {author's} affirmative disclosure is that {disclosure}. Any apparent AI signature in the data should be read against that disclosure rather than as independent evidence."}

The framework's deepest principle: the descriptive measurements are the framework's job; the interpretive reading is the writer's call. What follows is one informed reading of the numbers, not a verdict the math entitles.

## What's distinctive about this corpus

Three things stand out compared to typical {register} corpora:

The first thing: {distinctive feature with its value, compared to a typical reference}.

The second thing: {distinctive feature}.

The third thing: {distinctive feature}.

---

*Generated by the SETEC stylometric framework (https://github.com/anotherpanacea-eng/setec-voiceprint). The numbers are descriptive measurements; the readings are one person's interpretation. Voice is more than what stylometry can measure; what it can measure, it measures honestly.*

---

## Implementation note (for the eventual `generate_voice_report.py`)

The numerical sections of this template can be populated automatically from the JSON outputs of three existing scripts:

- `voice_profile.py --json` → durable voiceprint section (most stable features by CV per family)
- `voice_drift_tracker.py --json-out` → era/drift section (per-period distances and per-feature drift tables)
- `idiolect_detector.py --json` (run for n=1, n=2, n=3 separately) → idiolectic vocabulary section

The interpretive prose sections — "what this signals about the register," "what's distinctive," "three observations to flag" — require editorial judgment and should be filled by a human or LLM editor reading the numerical sections in context. The template marks these explicitly with placeholder text in `{curly braces}`.

Per-section automation status:

| Section | Automation status | Source |
|---|---|---|
| Header (counts, dates) | full | `voice_profile_json.baseline_summary` + filename date pattern |
| Durable voiceprint table | full | `voice_profile_json.families[*].most_stable_features` |
| Durable voiceprint interpretation | manual | LLM or human reads the numbers |
| Idiolectic topic-domain table | full | `idiolect_n1_json` + `idiolect_n2_json` + `idiolect_n3_json` filtered by content-only |
| Idiolectic interpretation | manual | LLM or human reads the n-grams |
| Drift cross-period table | full | `drift_json.cross_period_distances` |
| Drift per-cluster paragraphs | manual | LLM or human reads the drift tables |
| Comparison section | semi | numerical values automatable; interpretation manual |
| Three observations | manual | the reader's call |
| What this cannot say | template | use the boilerplate from the template directly |
| What's distinctive | manual | requires reference to "typical {register}" knowledge the script doesn't have |

Suggested invocation pattern once the script ships:

```bash
generate_voice_report.py \
    --voice-profile path/to/voice_profile.json \
    --voice-drift path/to/voice_drift.json \
    --idiolect-n1 path/to/idiolect_n1.json \
    --idiolect-n2 path/to/idiolect_n2.json \
    --idiolect-n3 path/to/idiolect_n3.json \
    --comparison-drift path/to/control_drift.json \
    --author-name "Author Name" \
    --corpus-label "author corpus label" \
    --register blog_essay \
    --ai-disclosure "no AI use on the blog at any point per author confirmation" \
    --out path/to/voice_insights.md
```

The script emits a draft markdown report with the numerical sections populated and the interpretive sections marked `{TODO: interpret}`. The user (or an LLM pass with the report as context) fills the TODOs and saves the final.
