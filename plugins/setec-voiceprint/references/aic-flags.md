# AIC Flag Framework: Diagnosing AI Prose Patterns

The pattern-resolution layer. Each flag catalogs a specific manifestation of the underlying mode collapse described in `distributional-diagnostics.md`. Each flag includes the distributional signature that predicts it, indicators, severity bands, and false-positive guardrails. For voice-attribution and earned/unearned triage, see `source-triage.md`.

## The Meta-Category: Unearned Fluency

Prose that reads smoothly without having earned that smoothness through specificity, voice, or structural pressure. The sentences are grammatically competent, the paragraphs transition logically, the rhythm is consistent, and none of it required a human consciousness to select these particular words in this particular order. The hallmark: you can swap any sentence with a paraphrase and lose nothing.

The seven flag families below are specific manifestations of unearned fluency, and each has a distributional signature in Layer A.

---

## AIC-1: Generic Hand (Voice Singularity)

A manuscript that reads as though one consciousness wrote every character, every scene, every register. No variation in sentence rhythm across POV characters. No shift in diction between intimate scenes and action scenes. No idiosyncrasy. The narrator has no personality, only competence.

**Distributional signature.** Compressed sentence-length variance across the document. Compressed FKGL standard deviation. Function-word distribution close to LLM defaults rather than the writer's prior fingerprint. Compressed MDD-SD across sentences. POS-bigram distribution close to a generic reference rather than displaying authorial preference.

**Test.** Read the passage aloud in a neutral voice. Then read it as if a different character wrote it. Does anything resist the swap?

**Indicators.**
- Sentence rhythm does not vary across characters or emotional states
- Diction level is uniform (no character uses simpler or more complex language than another)
- Narrator personality is absent: the prose reports but doesn't select
- Interiority sounds the same in every POV character

**Distinguished from** consistent voice (a deliberate authorial choice, where the narrator's personality *is* the consistency) and close third (where voice should shift with POV character). Voice singularity is the absence of choice.

**Severity.**
- **Spot** — isolated to one passage; surrounding text has voice
- **Pattern** — recurring across scenes; the manuscript has a default register it falls into
- **Systemic** — manuscript-wide; one entity generated all of it

---

## AIC-2: Velvet Fog (Scene Fog + Lexical Genericism)

Scenes that proceed through dialogue and interiority without grounding the reader in physical reality. Characters talk in unspecified spaces. Actions happen without bodies. Word choices are accurate but never specific: center of the semantic field, never the edge, never the wrong word used right.

**Distributional signature.** Compressed lexical diversity (low MATTR, low MTLD, high Yule's K). Compressed Shannon entropy. Compressed adjacent-sentence cosine variance (sentences stay close in topical embedding because the prose stays in the abstract layer rather than landing in concrete imagery).

**Test.** After reading the passage, can you draw the room? Can you point to three sensory details that only this character in this moment would notice?

**Indicators.**
- Characters talk in unspecified spaces
- Physical descriptions are accurate but generic ("a cozy apartment," "a bustling street")
- Sensory detail is visual-only or absent
- The character's body doesn't exist between dialogue lines
- Word choices never surprise: "She felt a wave of sadness" instead of anything a particular person in a particular moment would think

**Named subtype: Indefinite-pronoun gesture.** "Something" + abstract qualifier. "Some X part" + adjective. "A kind of Y" without specifying Y. The prose outsources specificity to the reader's imagination. See `source-triage.md` for earned/unearned triage and revision moves.

**Distinguished from** minimalist staging (deliberate sparseness, as in Beckett or Carver). Minimalist staging chooses what to omit; Velvet Fog simply doesn't generate the physical world unless forced.

**Severity.**
- **Spot** — one scene lacks grounding; adjacent scenes are specific
- **Pattern** — bridge scenes and dialogue consistently foggy; action grounded
- **Systemic** — the manuscript lacks a physical world

---

## AIC-3: Echo Stack (Structural Repetition)

Repetitive structural patterns at the sentence, paragraph, or scene level that the writer didn't choose. The pattern is correct but mechanical: a template applied, not a rhythm felt.

**Distributional signature.** Compressed sentence-length variance (and burstiness B near 0 or negative). Compressed MDD variance across sentences. Repeated POS-bigram patterns inflating bigram entropy. At the paragraph and scene levels, repeated structural rhythms (every paragraph the same number of sentences; every scene the same setup-dialogue-reflection arc).

**Test.** Mark sentence openings and syntactic patterns across 10+ consecutive sentences. Mark paragraph openings across 5+ paragraphs. Mark scene openings across 3+ scenes.

**Indicators.**
- *Sentence-level:* repeated Subject-Verb-Object pattern, identical sentence lengths, parallel constructions that aren't rhetorical
- *Paragraph-level:* every paragraph opens with a topic sentence, closes with a transition, has the same number of sentences
- *Scene-level:* every scene opens with setting → moves to dialogue → ends with reflection

**Severity.**
- **Spot** — one passage falls into repetitive rhythm; surrounding text varies
- **Pattern** — the echo recurs in predictable contexts (all dialogue scenes, all openings)
- **Systemic** — the manuscript has a template it applies everywhere

---

## AIC-4: Register Seams (Multi-Source Splicing)

A detectable shift in prose quality, vocabulary level, or stylistic confidence that correlates with a change in drafting method or model. Common in manuscripts assembled across multiple LLM sessions or mixed human/AI drafting.

**Distributional signature (within-document).** Sliding-window analysis of Layer A signals shows segment-level variance in MATTR, FKGL, function-word distribution, or POS-bigram patterns that doesn't correspond to scene or POV boundaries. The seams correlate with changes in compression magnitude.

**Important caveat: Pangram signal-9 tension.** Pangram and EditLens treat uniform style across segments as the AI tell, on the theory that humans naturally drift more in voice and register across a draft. AIC-4 flags *visible drift* as a problem; signal-9 flags *uniformity* as a problem. The framework distinguishes:

- **Bad drift (AIC-4 fires):** jarring tonal shift mid-scene that breaks reader trust. The seam serves nothing; it's an artifact of production.
- **Natural drift (AIC-4 does not fire):** a writer who switches register between a technical paragraph and an anecdote, or whose voice wobbles across a long draft because attention and energy varied. This is human and protective of the writer against detector signals.

The diagnostic is whether the shift serves the prose. Authorial-controlled register variation is good. Production-artifact register seams are bad. A writer who smooths every seam at the prose level may walk into higher detector confidence; a writer who introduces tonal shifts to "humanize" the prose may break reader trust. Source triage adjudicates.

**Test.** Read looking for shifts in vocabulary level, sentence complexity, or stylistic confidence that don't correspond to POV shifts, emotional shifts, or deliberate register changes.

**Indicators.**
- Abrupt vocabulary level changes mid-paragraph or mid-scene
- One chapter reads at a notably different prose level than its neighbors
- Dialogue and narration feel written by different people (not character voice; authorial voice)
- Transitions between sections feel like cuts, not flows
- Confidence level shifts: some passages are assertive, others hedge

**Distinguished from** intentional register shifts (formal narration dropping into colloquial interiority). Register seams serve nothing; they're artifacts of production.

**Severity.**
- **Spot** — one detectable seam; might be a single paste-in
- **Pattern** — multiple seams correlating with chapter or scene boundaries
- **Systemic** — the manuscript is a patchwork; no consistent authorial voice holds it together

---

## AIC-5: Puppet Dialogue (Mouth Uniformity)

Dialogue where every character speaks in the same register, at the same intelligence level, with the same sentence complexity. Characters take turns delivering information or advancing plot. No one interrupts, misunderstands strategically, uses language as a weapon, or reveals themselves through what they *won't* say.

**Distributional signature (within dialogue passages).** Compressed sentence-length variance across speaker turns. Function-word distributions across speakers cluster on the same fingerprint. POS-bigram distributions across speakers do not differentiate. Adjacent-turn cosine similarity high.

**Test.** Cover character names. Can you tell who is speaking from diction, rhythm, sentence length, what they refuse to say, or how they deflect?

**Indicators.**
- All characters speak in complete sentences at the same complexity level
- Characters take turns without interrupting, mishearing, or talking past each other
- No character has a verbal tic, habitual deflection, or characteristic rhythm
- Subtext is absent: characters say what they mean and mean what they say
- Dialogue tags are the only differentiation

**Distinguished from** functional dialogue (sparse by design, as in Hemingway or Carver). Puppet dialogue isn't spare; it's uniform.

**Severity.**
- **Spot** — one conversation is uniform; others have distinct voices
- **Pattern** — dialogue scenes consistently puppet; non-dialogue has more voice
- **Systemic** — no character has a distinct speech pattern

---

## AIC-6: Continuity Smear (World-Model Failures)

Failures of physical, temporal, or causal continuity that result from generating text without maintaining a persistent world model.

**Distributional signature.** None at Layer A. Continuity failures are world-model failures, not stylometric ones. AIC-6 fires on close reading, not statistics.

**Test.** Track three things through the passage: (1) what characters are physically holding or wearing, (2) spatial positions relative to each other, (3) what information each character has at each point.

**Indicators.**
- Objects appear, disappear, or teleport between characters' hands
- Spatial positions are inconsistent within a scene
- Characters reference information they shouldn't have yet
- Time passes unevenly (a five-minute conversation spans an hour, or vice versa)
- Emotional states reset between paragraphs without transition

**Distinguished from** ordinary human continuity errors by density and type. AI continuity smear clusters around entity states (holding, wearing, positioning) and temporal sequence. Human errors tend to be factual consistency across long stretches.

**Severity.**
- **Spot** — one or two continuity breaks; the physical world is otherwise maintained
- **Pattern** — breaks cluster around specific scene types (action, group dialogue)
- **Systemic** — the manuscript doesn't maintain a physical world model

---

## AIC-7: Discourse Leak (Assistant-Register Intrusion)

Prose in which characters, narrators, or the text itself organizes thought the way a language model does rather than the way a person in that situation would. The tell is not bad prose; it's the wrong *kind* of fluency.

**Distributional signature.** High connective density (>25 explicit discourse markers per 1000 tokens). High mean adjacent-sentence cosine with low std (cohesion too tidy). Function-word distribution heavy on hedging connectives. POS-bigram patterns favor MD-VB modal-verb constructions and RB-JJ adverbial pre-modification at LLM-default density.

**Test.** Read looking for moments where the text sounds like it's explaining or organizing for a reader rather than inhabiting a character or telling a story. Ask: "Is this how a person in this situation would think, or is this how an AI would present this person's thoughts?"

### Evidence Categories

**Assistant Frame** — Direct assistant-register intrusions. Throat-clearing before points ("Here's the thing about grief..."). Resumptive parroting (a character restating what just happened before reacting). Sycophantic framing ("The remarkable thing was..."). Metacommentary on complexity ("It was a complicated feeling, one that resisted easy categorization").

*Named subtype: Pseudo-aphorism.* "X as Y." "X is the Y of Z." "There is a kind of X in every Y." Aspires to maxim register without earning the standing of a maxim. Often has a real image right after that does the actual work. See `source-triage.md` for the cut rule.

**Hedge Drift** — Epistemic hedging at densities suggesting LLM caution rather than narrative uncertainty. "In some ways," "to a certain extent," "it could be said that," "arguably," "there was a sense in which." The flag fires on accumulation: three hedges in a paragraph of genuine uncertainty is voice; three hedges per page across a chapter is drift.

*Named subtype: Negation hedge.* "Not X." / "Not X, exactly." / "Not X. Not Y." Narrator pretends to make a careful discrimination. The next sentence does the work the negation pretended to. See `source-triage.md` for the cut rule and the source triage that distinguishes earned (cognitive sorting) from unearned (narrator pose).

**Template Loop** — Rhetorical figures deployed as structural tics rather than choices. Correctio is the most common. Also: cataphoric teasing ("Here's where it gets complicated"), synonym stacking ("robust, thorough, and comprehensive"), and the magic triple (grouping attributes in threes with mechanical regularity). The test: does the pattern do new work each time, or is it applied on schedule?

*Named subtype: Disguised correctio.* "Not X, but Y" embedded in narration. "Did not X but Y." Same as Negation hedge but harder to spot because it's mid-sentence. Almost always cuts. See `source-triage.md`.

*Named subtype: Manifesto cadence.* Three or four parallel sentences building to conclusion. Earned when each sentence escalates, restricts, or reveals. Unearned when parallel structure substitutes for actual development. See `source-triage.md`.

**Lexical Convergence** — The model reaches for the same high-register word across semantically different contexts where a human voice would differentiate. A human writer might choose "structure," "layout," "pattern," "shape," or "logic" depending on context; an LLM has a favorite and uses it for all of them. The diagnostic question: does this manuscript reuse the same prestige term across unrelated contexts where more specific or more ordinary words would serve?

The convergence habit is more durable than any word list. ChatGPT-4o overindexed on "delve," "tapestry," "navigate," "landscape." Sonnet 3.5 favored "architecture," "choreography," and lyrical register in contexts that didn't warrant it. The specific words shift every model generation. The habit of convergence persists.

**Commitment Evasion** — Both-sidesing, positivity pivots, and unearned resolution. A narrator who refuses to commit when the story's stakes demand commitment. Interiority that compulsively balances every negative thought with a qualifying positive ("It was devastating, but also, in a strange way, freeing"). A character whose anger always resolves into understanding within the same paragraph.

The positivity pivot is especially diagnostic: real people sometimes end on unresolved negative feeling. LLM-generated interiority almost never does.

### Evidence Burden

Each fired evidence category requires a minimum of two quoted instances from the passage, with a brief note explaining why each instance is unearned in context (not explained by the character's psychology, the narrator's established voice, or the scene's rhetorical demands). A single instance of correctio or a lone hedge is not evidence. The flag fires on accumulation, and the audit must show the accumulation.

### False-Positive Guardrails

Before flagging AIC-7, test whether the pattern is explained by any of these:

- **Essayistic fiction** (Sebald, Bernhard, Knausgaard): Hedge Drift, Template Loop, and Commitment Evasion may all be structural features. The test: do the qualifications carry personality, or are they generic?
- **Philosophical narrators**: Does hedging track the character's specific intellectual commitments, or is it generalized caution about everything?
- **Trauma-loop cognition**: Does repetition escalate, deepen, or shift with each iteration (working through something), or cycle at the same level (stalling)?
- **Adolescent or uncertain narrators**: Does uncertainty match developmental stage and contrast with passages where the character is more sure?
- **Ironic or unreliable narration**: Does the text frame the hedging as characterization (exposed by plot, seen through by reader)?

When a guardrail applies, note it: "AIC-7 evidence present but consistent with [essayistic voice / philosophical narrator / etc.]. Not flagged."

**Severity.**
- **Spot** — one or two evidence categories in isolated passages; surrounding text is clean
- **Pattern** — multiple categories recur, especially in interiority. Action and dialogue stay cleaner. The manuscript has a "thinking voice" problem.
- **Systemic** — the narrator's voice is contaminated throughout. The reader feels presented to rather than told a story.

---

## Nonfiction Parallel Pattern Set

For testimony, briefs, op-eds, scholarly articles, and policy memos, the AI-prose patterns differ. Five argument-shaped poses common in AI-assisted nonfiction:

| Pattern | What it does (unearned) | Where AIC equivalent |
|---|---|---|
| **Abstraction Shielding** | Lets the writer avoid naming specific actors. "Stakeholders," "youth-serving systems," "those impacted" | AIC-2 (Velvet Fog) for argument |
| **False-Balance Construction** | Smuggles in judiciousness while granting standing to positions that don't merit it. "While reasonable people may disagree" | AIC-7 (Commitment Evasion) |
| **Hedge-and-Affirm** | Performs caution while saying nothing definite. "While X is generally true, in some cases Y" | AIC-7 (Hedge Drift) |
| **Recommendation Template** | Provides advocacy appearance without specifying action. "DC must commit to..." | AIC-7 (Template Loop) for argument |
| **Authority Laundering** | Borrows authority without taking responsibility. "Research has shown..." | AIC-7 (Assistant Frame) for argument |

See `source-triage.md` for earned/unearned distinctions and revision moves for each nonfiction pattern.

These patterns share the same distributional signature as AIC-7 in fiction: high connective density, low specificity in noun phrases, function-word distributions heavy on hedging. They differ in that argument-shaped nonfiction has higher natural tolerance for some patterns (qualifications are part of analytical work) and lower tolerance for others (recommendation templates that name no actor are uniquely costly to advocacy).

---

## Genre Tolerance Quick Reference

Each AIC flag fires at different thresholds in different genres. A pattern that signals trouble in literary fiction may be partially structural to testimony or blog, and the inverse holds. This table consolidates calibration notes scattered across the framework into a single quick reference for triage. Use it after Layer A reports a band but before deciding whether a Layer B finding rises to a flag.

**Tolerance bands.**

- **Low**: even isolated instances signal a problem. Flag at Spot severity and above.
- **Med**: pattern fires only on accumulation. Isolated instances are normal; flag at Pattern severity and above.
- **High**: pattern is partially structural to the genre. Flag only at Systemic severity, and verify with the writer first.
- **N/A**: pattern category does not apply to this genre.

| Flag | Literary Fiction | Thriller | Romance | Essay | Blog | Testimony |
|---|---|---|---|---|---|---|
| **AIC-1** Generic Hand | Low | Med | Low | Low | Low | High¹ |
| **AIC-2** Velvet Fog | Low | Low | Low | Med | Med | Mixed² |
| **AIC-3** Echo Stack | Low | Med | Med | Med | Mixed⁴ | Mixed⁶ |
| **AIC-4** Register Seams | Low | Low | Low | Med | Med | Low |
| **AIC-5** Puppet Dialogue | Low | Med | Low | N/A | N/A | N/A |
| **AIC-6** Continuity Smear | Low | Low | Low | N/A | Med | Low |
| **AIC-7** Discourse Leak | Low | Med | Med | High³ | Mixed⁵ | High³ |

¹ Institutional voice is the form. A consistent register is appropriate to formal advocacy; "voice singularity" reads as professionalism. The flag still fires on a generic non-institutional voice that fails to advance the argument's specifics.

² AIC-2 in testimony is a mixed call. High tolerance for legal-category abstraction ("stakeholders," "youth-serving systems," "those impacted") that has institutional warrant. Low tolerance for actor-abstraction where naming the responsible actor is required by the advocacy form. Use source-triage on case-by-case basis; the nonfiction parallel "Abstraction Shielding" is the more specific lens.

³ AIC-7 in essay and testimony has high tolerance specifically for the Hedge Drift and connective-density subtypes, because epistemic care and explicit structural cohesion are structural to analytical writing and legal qualification. Tolerance is lower for the Assistant Frame and Template Loop subtypes, which import LLM presentation habits without analytical warrant. Source-triage adjudicates per subtype.

⁴ AIC-3 in blog is split. Medium tolerance for associative and cadence repetition, which is part of the form (a writer who circles back to a motif, a paragraph rhythm that recurs across sections). Low tolerance for visible template rhythm: every paragraph opening with a question, every paragraph closing with a takeaway, parallel structures applied on schedule. The cadence-vs-template distinction is the diagnostic, not the count of repetitions.

⁵ AIC-7 in blog is asymmetric. Low tolerance for Assistant Frame and Template Loop subtypes (resumptive parroting, throat-clearing before points, magic-triple synonym stacking), which read as AI-generated regardless of the writer's voice. Medium tolerance for the Hedge Drift subtype: genuine personal hedging is part of conversational voice. The diagnostic is whether the hedge tracks the writer's specific uncertainty or flattens into LLM-generic caution.

⁶ AIC-3 in testimony is split. Medium tolerance for numbered and parallel advocacy structure (recommendation lists, "first ... second ... third," or anaphora across enumerated points), which is conventional and rhetorically warranted. Low tolerance for template rhetoric (every section opening "We urge the Council to consider," every recommendation matching the same syntactic shell), which reads as the AI-assisted Recommendation Template pattern from the Nonfiction Parallel Pattern Set.

### Reading the table

The load-bearing principles:

**Voice-centered fiction** (literary fiction, romance) holds low tolerance for AIC-1, AIC-4, and AIC-7 because voice integrity is what readers came for. A romance with no voice differentiation between leads loses chemistry; a literary novel with assistant-register intrusions in the narrator loses the narrator's authority.

**Plot-centered fiction** (thriller) holds medium tolerance for AIC-3 and AIC-5 because pace and functional dialogue are partially structural. A pursuit scene with similar sentence rhythms is conventional; a dialogue scene where characters trade pure information advances the plot. The flag still fires when the structural repetition exceeds what pace warrants.

**Argumentative nonfiction** (essay, testimony) holds high tolerance for some AIC-7 subtypes because analytical writing requires explicit qualification ("in some cases," "to a certain extent") and explicit structural cohesion ("first," "however," "moreover"). The flag fires when accumulation exceeds what the argument warrants, when hedges qualify nothing in particular, or when the connectives stop tracking actual logical relations.

**Conversational nonfiction** (blog) is asymmetric across patterns. Low tolerance for AIC-1 (a flat-voice blog has no reason to exist) and for the Assistant Frame and Template Loop subtypes of AIC-7 (they read as AI-generated regardless of the writer's voice). Medium tolerance for AIC-3 cadence repetition and AIC-4 register shifts (digression is part of the form). Medium tolerance for the Hedge Drift subtype of AIC-7 (genuine personal hedging is part of conversational voice). See footnotes 4 and 5 for the split-tolerance treatment of AIC-3 and AIC-7 in blog.

**All genres** hold low tolerance for AIC-6 (continuity smear) when narrative continuity is in scope. Continuity failures are world-model failures, not stylometric ones, and they break reader trust regardless of genre. The exception is essay, where there is no persistent world model in the same sense, and blog, where conversational digression provides some cover.

### Calibration warnings

A draft can read as "Heavily smoothed" against general human prose while sitting comfortably inside its genre's tolerance ranges. Institutional testimony, technical writing, and academic prose all run lower in burstiness and lexical diversity than literary fiction does. Always run with a register-matched personal baseline if available; the heuristic thresholds in `variance_audit.py` are general-prose calibrations, not genre-specific.

A draft can also read as "Lightly smoothed" on Layer A while landing low-tolerance flags within its genre. A literary fiction draft with a foggy sensory layer reads "lightly smoothed" on the distributional metrics but flags AIC-2 on Layer B because the metrics it failed are not load-bearing for that genre. The Layer A band is necessary but not sufficient; Layer B and source triage adjudicate.

Genre tolerance is also writer-specific within a register. Some literary novelists tolerate high AIC-3 (Knausgaard's recursive paragraphs, Bernhard's spiraling repetitions) as a structural choice; some essayists tolerate high AIC-7 (Sebald's hedging) as voice. The table captures genre-typical tolerances; personal-baseline z-scores override them when the writer has a documented register.

---

## Pattern Synthesis: Flag Compounds

Some flag combinations are more damaging than their parts:

| Compound | What it means | Revision scope |
|---|---|---|
| AIC-1 + AIC-5 | Entire voice layer was generated | Voice needs building from scratch |
| AIC-2 + AIC-6 | Text generated without persistent world model | Physical reality needs grounding pass |
| AIC-7 in interiority only | Narrow contamination | Rewrite the thinking; keep the doing |
| AIC-7 + AIC-1 (Pattern+) | Voice *and* register both wrong | Most damaging compound; voice layer needs rebuilding |
| AIC-3 + AIC-4 | Template applied unevenly across sources | Structural rhythm needs unifying |
| Multiple nonfiction patterns + Layer A "Heavily smoothed" | Generic advocacy register; argument is template | Rebuild from specific evidence and named actors |

---

## Multi-POV Cross-Check

In multi-POV fiction, the most diagnostic test is whether a pattern belongs to a character or to the generating consciousness.

**The authorial frequency test.** Take a syntactic move (correctio, a particular uncertainty word, an interiority rhythm) and check whether it appears at similar density across multiple POV characters. If Character A self-corrects through "Not X. Something more like Y" and Characters B and C also self-correct through the same construction at similar rates, the pattern belongs to the author/generator, not to any character.

**The Blind Swap test for interiority.** Extract internal monologue passages from different POV characters and strip identifying context. Can you tell which character is thinking? If not, AIC-1 is present in the interiority layer even if dialogue and action are well-differentiated.

**Convergence words across POVs.** A word like "something" doing uncertainty duty is unremarkable in one character. The same word doing the same duty in the same syntactic position across three characters is lexical convergence at the authorial level. The fix is character-specific: each POV character should have their own vocabulary for the unnamed and half-understood, filtered through their particular way of perceiving.

---

## The Surface-Tell Question

Writers and editors often maintain lists of "AI words" and surface tells: em-dash frequency, "delve," "tapestry," the magic triple. These lists are useful but unstable; they decay as models change and writers learn to avoid them.

This framework targets *discourse habits* rather than *vocabulary*. The tendency to hedge, organize in threes, throat-clear before revelations, qualify every commitment: these reflect how language models process and present information. The habits evolve more slowly than the vocabulary.

The named patterns within AIC-7 (Negation hedge, Disguised correctio, Pseudo-aphorism, Manifesto cadence) and within AIC-2 (Indefinite-pronoun gesture) are syntactic rather than lexical. They survive vocabulary changes across model generations because they are structural moves, not word choices.

That said, AIC-7's Lexical Convergence category does maintain a per-project watchlist of recurring prestige vocabulary. A word earns its watchlist spot when it (a) appears in multiple unrelated contexts and (b) could be replaced with a more specific or ordinary word each time.
