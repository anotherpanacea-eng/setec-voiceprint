# Metric-targeted restoration

This is the bridge between SETEC's diagnostic surfaces (Surfaces 1
and 2) and its craft-restoration surface (Surface 4). The diagnostics
say *what* drifted in distributional terms; this reference says *which
of those signals can responsibly become a revision instruction* and
*how to translate the ones that can't be prompted in raw form into
prose-level moves a writer or LLM-revision pass can act on*.

The framework's resistance to metric gaming lives in this taxonomy.
Some signals are direct craft targets; some are translatable; some
should trigger a deeper diagnostic read before any revision; some
should never become prompt targets at all because optimizing them
directly invites prose damage and a false sense of having fixed the
problem.

## The four targetability classes

Every candidate signal SETEC computes falls into one of these four
classes. The class determines what the restoration packet does with
the signal, not the signal's importance for diagnosis.

### 1. Direct target

The signal maps cleanly to a promptable prose move. The packet may
issue a revision instruction directly, with one or two specific
moves the writer or model can apply locally.

| Signal | Good prompt target |
|---|---|
| High connective density | Cut explicit discourse markers; let adjacency carry transitions |
| Low sentence-length variance / `burstiness_B` | Add fragments and a few longer syntactic runs; avoid middle-length uniformity |
| Low FKGL standard deviation | Let technical/analytical sentences spike and anecdotal sentences drop |
| High adjacent-sentence cosine mean with low SD | Remove over-explained transitions; permit sharper turns and asides |
| Repetition audit: over-represented generic words | Replace or cut generic repetition, preserving project anchors |
| Idiolect detector: preservation list | Preserve these words/phrases verbatim during revision |
| AIC named-pattern density (negation hedge, manifesto cadence, etc.) | Apply source triage and rhetorical countermoves from `references/source-triage.md` |

### 2. Translated target

The signal is not promptable in raw form, but its contributors can
be translated into a prose-level move. POS bigrams and trigrams live
here. So do dependency n-grams and function-word clusters when the
contributors are interpretable.

| Raw signal | Translation |
|---|---|
| `DET+ADJ+NOUN` elevated | Too many prepackaged evaluative noun phrases; replace generic adjective+noun labels with concrete actors, objects, or verbs |
| `ADJ+NOUN` elevated | Adjective-heavy description; test whether adjectives are doing sensory/argument work or just smoothing |
| `NOUN+NOUN` elevated | Noun-stack / institutional-label density; unpack into relations or actions where clarity improves |
| `ADP+DET` and `ADP+NOUN` elevated | Prepositional scaffolding; cut nested "of/in/for" structures or recast with stronger verbs |
| `PRON+AUX` or `PRON+VERB` depressed | Agent/action layer may be thinned; restore named actors and lived actions where the draft has abstract nouns |
| `ADV+ADJ` elevated | Booster/evaluator language; cut intensifiers or replace with evidence |
| `VERB+DET+NOUN` depressed | Concrete action-object frames may be missing; add actual acts, not explanatory labels |

### 3. Investigate-first target

The signal says "something is off" but not which revision should
happen. The packet asks a diagnostic question and points to local
evidence rather than issuing a revision instruction.

| Signal | Diagnostic prompt |
|---|---|
| Low MATTR / MTLD | Are repeated words thematic anchors, closed-scene constraints, or synonym poverty? |
| High Yule's K | Which high-frequency words are load-bearing, and which are generic glue? |
| Low Shannon entropy | Is the distribution narrow because the topic is narrow, or because the prose has been normalized? |
| Function-word cluster drift | Is this a legitimate register/persona shift, or assistant-register connective/default-pronoun drift? |
| Dependency n-gram drift (without explicit translation) | Which syntactic constructions are repeated locally, and are they earned by genre/argument? |

### 4. Avoid direct targeting

The signal should not become a prompt target. The packet may mention
it as evidence but must translate through a safer proxy or ask for
deeper diagnosis. Optimizing these directly is what produces
metric-chased prose.

| Signal | Why not target directly |
|---|---|
| Overall POS-bigram KL/JSD | Aggregate divergence; optimizing it directly encourages syntactic gaming |
| Overall Burrows Delta / cosine distance | Voice-distance summaries; too easy to overfit function words |
| Character n-gram distance | Mostly orthographic/morphological residue; not a craft instruction |
| Raw dependency n-gram distance | Parse-feature abstraction; unreliable as a writer-facing target unless localized and translated |
| AUC / validation metrics | Performance metrics, not revision goals |
| Compression band (e.g., "Heavily smoothed") | A summary judgment; the band is for the writer to read, not for the model to optimize |

## POS-bigram translation table

The packet generator's `POS_BIGRAM_TRANSLATIONS` constant uses this
mapping. Direction is signed: a bigram can be over-represented (target
fires too much relative to baseline) or under-represented (target
fires too little). The translation tells the writer what each
direction implies for the prose, not what they "should do" — the
revision move always requires inspecting local examples first.

| POS bigram | If over-represented | If under-represented | Revision move |
|---|---|---|---|
| `DET+ADJ` | Formulaic noun-phrase setup | Sparse descriptive setup | Keep only adjectives that change reader inference |
| `ADJ+NOUN` | Evaluative label clusters | Thin sensory/conceptual naming | Replace generic modifiers with concrete nouns/verbs; preserve earned epithets |
| `NOUN+NOUN` | Institutional or abstract noun stacks | Less compressed labeling | Unpack relation with a verb or preposition only when clarity improves |
| `NOUN+ADP` | "X of/for/in..." scaffolding | Less relational explanation | Cut nested abstract relations; make actors and actions visible |
| `ADP+DET` | Prepositional padding | Choppier, less scaffolded syntax | Collapse weak prepositional phrases; vary sentence architecture |
| `ADP+NOUN` | Topic/register nouns carried by prepositions | Fewer abstract anchors | Check for bureaucratic abstraction or topic terms before revising |
| `PRON+AUX` | Hedged/assistant-like stance or dialogue-heavy mode | Agent layer may be missing | Inspect examples; restore or cut depending on voice |
| `PRON+VERB` | Personal narration or dialogue pressure | Human actors may be hidden | Restore named agents where abstractions dominate |
| `AUX+VERB` | Modal/passive/periphrastic verb frames | Direct finite verbs dominate | Prefer direct verbs unless modality is analytically needed |
| `ADV+ADJ` | Booster language | Less evaluative smoothing | Cut intensifiers; replace evaluation with evidence or image |
| `VERB+DET` | Action-object frames | Action layer may be thin | Add concrete actions/objects where the draft only explains |
| `VERB+ADP` | Phrasal/prepositional verb scaffolding | More direct verb-object syntax | Check for repeated "looked at / worked through / moved toward" drift |
| `CCONJ+DET` | Listy connective rhythm | Fewer additive structures | Break list cadence; vary coordination |
| `PUNCT+PUNCT`, `PUNCT+SYM`, `SYM+NOUN` | Likely markup/code contamination | N/A | Do not revise prose; run corpus hygiene first (`scripts/check_corpus.py`) |

A POS tag pattern is not inherently bad. It is a pointer to inspect
local examples. The translation says what the pattern *can mean*;
the writer's local read is what determines whether the pattern is
earned in this passage or symptomatic.

## POS-trigram translation table

Trigrams are often more promptable than bigrams because they preserve
a small syntactic shape. The packet generator's
`POS_TRIGRAM_TRANSLATIONS` constant uses this mapping.

| POS trigram | Translation | Revision move |
|---|---|---|
| `DET+ADJ+NOUN` | Polished descriptor package | Replace generic descriptor packages with concrete actors, sensory specifics, or a verb phrase |
| `ADJ+NOUN+NOUN` | Noun-stack compression | Unpack institutional labels when they hide agency; preserve domain terms |
| `NOUN+ADP+DET` | Abstract relation scaffolding | Cut "X of the Y" chains where the relation is obvious |
| `NOUN+AUX+VERB` | Predicate mediated by auxiliary | Check passive/modal drift; use direct verbs where commitment is safe |
| `PRON+AUX+VERB` | Personal stance/action mediated by auxiliary | In dialogue/interiority, decide whether hesitation is character work or assistant hedging |
| `AUX+VERB+ADP` | Modal/passive setup into relation | Replace with direct action when the sentence is explaining rather than showing |
| `VERB+DET+NOUN` | Concrete action-object unit | If under-represented, restore embodied action or concrete policy actors |
| `ADV+ADJ+NOUN` | Intensified evaluative package | Cut booster adverbs and make the noun/adjective carry the meaning |
| `ADP+DET+ADJ` | Preposition-led description chain | Break stacked prepositional modifiers into a cleaner sentence shape |
| `CCONJ+DET+NOUN` | List continuation rhythm | Break mechanical enumeration or make the list formally intentional |

The packet generator surfaces no more than three translated trigrams
per packet. More than that turns into syntax whack-a-mole — the
writer ends up chasing local patterns instead of revising the
passage's actual rhetorical move.

## Dependency n-gram handling

Dependency n-grams default to `investigate_first` in the packet
generator's `DEP_NGRAM_TRANSLATIONS` constant. They can be useful
when localized and paired with examples, but exposing raw dependency
labels (`amod`, `compound`, `prep/pobj`) to a writer-facing prompt
without a plain-language gloss is a recipe for syntax theater.

Minimum v1 translations (these graduate from `investigate_first`
to `translated` when the contributor is unambiguous):

| Dependency pattern | Translation | Revision move |
|---|---|---|
| `amod` elevated | Modifier load is high | Test adjectives for work; cut ornamental modifiers |
| `compound` elevated | Noun-stack load is high | Unpack institutional/domain labels only when they obscure agency |
| `prep/pobj` elevated | Prepositional scaffolding | Collapse weak relations or vary sentence architecture |
| `aux/pass` elevated | Passive/modal mediation | Restore actors where accountability matters |
| `advmod` elevated | Booster/stance adverbs | Cut intensity words or replace with evidence |
| `nsubj/ROOT/dobj` depressed | Direct actor-action-object frames are thin | Add concrete actions if the prose is over-explanatory |

Do not expose raw dependency labels to a writer-facing prompt without
a plain-language gloss and at least one local example sentence.

## Restoration packet structure

The packet generator (`scripts/restoration_packet.py`) emits packets
in two shapes:

- **JSON** for downstream tools and prompt generators.
- **Markdown** for human readers and copy/paste into a prompt UI.

Both shapes carry the same fields. The JSON schema:

```json
{
  "task_surface": "craft_restoration",
  "tool": "restoration_packet",
  "claim_license": {
    "licenses": "Revision targets for measured drift.",
    "does_not_license": "AI provenance, authorship attribution, or proof the revision is better."
  },
  "packets": [
    {
      "id": "pos_trigram_DET_ADJ_NOUN_over",
      "targetability": "translated",
      "signal": "POS trigram DET+ADJ+NOUN",
      "direction": "over_represented",
      "severity": "moderate",
      "evidence": {
        "metric": "kl_contribution",
        "value": 0.018,
        "baseline_direction": "above writer baseline"
      },
      "plain_language_diagnosis": "The passage leans on polished adjective+noun packages.",
      "revision_moves": [
        "Replace generic descriptor packages with concrete actors, objects, or verbs.",
        "Preserve domain terms and idiolectic phrases."
      ],
      "guardrails": [
        "Do not add new facts.",
        "Do not replace writer-specific phrases from the preservation list.",
        "Do not optimize for POS tags directly."
      ],
      "post_check": [
        "Rerun bigram_diff.py on the revised passage.",
        "Rerun variance_audit.py with the same baseline."
      ]
    }
  ],
  "prompt": {
    "model_instruction": "...",
    "revision_brief": "...",
    "post_check_commands": [...]
  }
}
```

## Prompt packet requirements

The packet generator gives the prompt generator enough context to
revise surgically without inviting metric gaming. Every packet must
carry:

- **Target signal.** Name and targetability class (`direct`,
  `translated`, `investigate_first`, `avoid_direct`).
- **Direction.** Over-represented, under-represented, compressed,
  elevated, depressed.
- **Severity.** Light / moderate / heavy or z / percentile / KL
  contribution where available.
- **Locality.** Whole document, chapter, window, or paragraph span.
- **Contributors.** Top bigrams / trigrams / words / clusters
  driving the signal.
- **Examples.** Short local snippets when supplied by the diagnostic
  source. If no snippets are available, the packet says so and asks
  the user/tool to retrieve local examples before revision.
- **Baseline envelope.** What "normal" means: writer baseline,
  register baseline, validation fixture, or heuristic only.
- **Allowed moves.** Concrete revision operations (the
  `revision_moves` list above).
- **Forbidden moves.** No new facts, no thesaurus sweep, no
  flattening idiolect, no metric gaming.
- **Preservation list.** Optional, private, drawn from idiolect
  detector output.
- **Post-check commands.** Exact SETEC commands to rerun after
  revision.

The packet generator targets no more than two or three signals in a
single prompt by default (configurable with `--max-targets`).
Combining five metric instructions produces incoherent revision
pressure.

## Diagnostic prompt for poor targets

When a signal is `investigate_first` or `avoid_direct`, the packet
generator emits a diagnostic prompt rather than a revision prompt:

```text
The diagnostic says <signal> moved, but that metric is not a safe
direct revision target. Inspect the passage and answer:

1. Which repeated words or phrase templates are driving the narrow
   distribution / elevated divergence?
2. Are they thematic / project anchors, closed-scene constraints, or
   generic smoothing?
3. Which two local edits would address the cause without optimizing
   the metric directly?

Do not rewrite yet. Return causes, evidence, and candidate moves.
```

This keeps poor targets from becoming pseudo-objectives.

## Before/after verification protocol

Every packet must carry a post-check. The minimum workflow:

1. Run diagnostics on the original text.
2. Generate the restoration packet.
3. Revise a bounded passage.
4. Rerun the same diagnostics with the same baseline and flags.
5. Compare before/after:
   - Did the targeted signal move in the intended direction?
   - Did nearby non-target signals degrade?
   - Did the idiolect preservation list survive?
   - Did the prose still satisfy source triage?

The first implementation makes step 5 manual. A later
`scripts/before_after_restoration.py` will automate the comparison.

## Privacy guard

Prompt packets can contain idiolectic phrases and voice-profile
evidence. Treat them as voice-cloning inputs.

Rules:

- If `--idiolect-json` or `--voice-json` is used, the packet is
  private by default.
- Markdown / JSON output outside `ai-prose-baselines-private/`
  requires `--allow-public-output`.
- If the packet is printed to stdout and includes private signals,
  emit a stderr warning before output.
- Never include a full voice profile in a prompt. Include only the
  minimum local preservation list needed for the passage.
- Warn the writer before sending idiolect-rich prompts to a remote
  LLM.

## Borrow vs. build

The framework deliberately does not import an external "humanizer"
as the restoration engine. Humanizers are adversarial pipelines
optimized to evade detectors; this surface is a bounded craft-
restoration layer with claim licensing, revision moves with named
guardrails, and a required post-check.

The oracle work (Phase A frequency-table + distance verification
against R `stylo`) is the trust boundary for Surface 1/2 numbers
that feed this surface. Once a signal is oracle-verified for
distance correctness and the calibration toolchain has set its
threshold provenance, it can become a `direct` or `translated`
target with empirical backing rather than literature anchoring.

## Limitations

- v1 does not auto-rewrite prose. The packet is for a human or
  LLM-in-the-loop revision pass; the script produces target
  packets and prompt text only.
- v1 requires the diagnostic to supply local examples / windows; a
  later pass can read the original text and extract snippets keyed
  by window offsets.
- Trigram and dependency translations are starter sets; the
  translation tables grow as audit experience identifies stable
  pattern→prose mappings.
