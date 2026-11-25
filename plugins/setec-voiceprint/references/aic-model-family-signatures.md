# Candidate AIC Tells by Model Family (Victorian-Pastiche Pilot)

Status: **candidate reference, n = 1 source chapter.** These tells are hypotheses for the `aic-flags.md` taxonomy, not calibrated flags. Nothing here is a detector or a verdict; the same licensing rules as `aic-flags.md` apply. Every example sentence below is newly invented to show the shape of the pattern; none is taken from the source material, which stays private.

## Why this file exists

`aic-flags.md` treats AI-prose patterns as one taxonomy. A small pilot suggests two refinements:

1. **Tells are model- and pass-specific.** Drafts from one model family share a signature. A rewrite by a second family, told to remove the first family's tells, removed them and grew its own. An audit should key a tell to the model family or revision pass that produces it, and should check a de-tell revision for *substitution*, not only removal.
2. **Several popular tells are period habits in some registers.** Against public-domain Victorian narrative, em-dashes, triads, "as if" clauses, negation-then-correction and explicitation all sit inside the human range. They should not fire on Victorian pastiche without a register-matched baseline (see the Victorian-pastiche note in `aic-flags.md` § Genre Tolerance Quick Reference).

## Pilot method

- **Source:** one private Victorian-pastiche chapter in four versions, about 3,100 to 3,500 words each:
  - **V1** a human-revised Claude-family draft
  - **V2, V3** two Claude-family rewrites
  - **V4** a Gemini-family rewrite instructed to remove a list of flagged tells
- **Round robin:** three frontier readers from three vendors (GPT-6.2 Astra, Gemini 4 Carbon, Claude Fable 5.2) each catalogued wording tells independently (87 candidates), then judged the other two catalogues blind to vendor (174 verdicts, 12 added misses). Merged by hand into 36 clusters: 21 core (all three vendors, at least two independent finds, no disputes), 8 supported by two vendors, 7 single.
- **Human control:** four public-domain narrative samples, two contiguous ~3,000-word windows each (from 35% and 65% through the book), about 6,000 words per author:
  - Trollope, *The Small House at Allington* (1864)
  - Eliot, *Middlemarch* (1871-72)
  - James, *The Pupil* (1891)
  - Oliphant, *Kirsteen* (1890)
- **Measures:** 22 scripted measures (regex, plus spaCy `en_core_web_sm` for sentence openings) and 4 tells counted by one GPT-6.2 Astra read per text using quote-free definitions. Rates are per 1,000 words unless marked %. Quoted dialogue is stripped for narration measures.
- **Verdict rule:**
  - **Pass:** a model version is at least twice the highest human rate, with at least 3 raw hits (percent measures: more than 25% above the human maximum).
  - **Marginal:** above the human maximum without meeting that bar.
  - **Fail:** at or below the human maximum.
  - For contractions and paragraph-length variation, a *low* value is the tell.

### Limits

- One source chapter, one genre, one register (Victorian narrative pastiche).
- Four human control authors, all from that register; no Doyle control.
- Counters are approximate; regexes over- and under-fire.
- Reader counts are single, noisy reads, and the reader may recognise the classic control texts.
- The readers that found the tells belong to the same vendors whose models wrote the versions. A model never judged its own family's version alone, but the cluster merge was not blind.

Treat every rate below as a pilot number. Before a tell becomes a flag, it needs a second source, a second register and a larger human control.

## Tells that pass the human control

| # | Tell | Proposed family | Produced by | Model rate (max) | Human max |
|---|---|---|---|---|---|
| 1 | Perception-verb scaffolding | AIC-3 | Claude family | 6.37 | 1.00 |
| 2 | Perceiving the unobservable | NEW: Impossible Witness | Claude family | 3.82 | 0.30 |
| 3 | One-line narrative paragraph beats | AIC-9 | Claude family | 19.6% | 3.6% |
| 4 | Vague "thing" / "something" | AIC-2 | Claude family | 4.25 | 1.22 |
| 5 | Polysyndetic and-chains | AIC-3 | Claude family (V1) | 5.73 | 1.83 |
| 6 | Emphasis padding and tautological tails | AIC-7 | Claude rewrites, Gemini rewrite | 1.84 | 0.15 |
| 7 | Contraction-free dialogue | AIC-5 | Claude rewrites, Gemini rewrite | 0.0 (low is the tell) | human min 12.1 |
| 8 | Semicolon procession | NEW: Period Costume | Claude rewrites | 2.62 | 0.33 |
| 9 | Fronted-adjunct and inversion saturation | NEW: De-tell Overcorrection | Gemini rewrite | 59.8% | 41.1% |
| 10 | "Only to" and "before -ing" tails | NEW: De-tell Overcorrection | Gemini rewrite | 1.84 | 0.15 |
| 11 | Shell-noun labels for speech acts | AIC-7 | Gemini rewrite | 1.53 | 0.15 |
| 12 | Paragraph fusion (flat paragraph lengths) | NEW: De-tell Overcorrection | Gemini rewrite | CV 0.49 (low is the tell) | human min 0.76 |
| 13 | Refrain-breaking synonym variation | NEW: De-tell Overcorrection | Gemini rewrite | 1.84 | 0.89 |
| 14 | Generalising narrator aside (weak) | AIC-7 | one Claude rewrite | 1.13 | 0 |
| 15 | "Found that she" filter (weak) | AIC-1 | Claude family (V1) | 0.96 | 0.17 |

### 1. Perception-verb scaffolding

- **Proposed family:** AIC-3 Echo Stack.
- **Mechanism:** inner facts arrive through a POV pronoun plus a perception verb, often in adjacent sentences, so the filter becomes the default container for information instead of a chosen moment of looking.
- **Signature:** `\b(she|he) (saw|watched|perceived|observed|noticed|understood|felt)\b` in narration; flag runs of two or more in adjacent sentences.
- **Produced by:** Claude family (V1-V3); absent from the Gemini rewrite.
- **Rates:** V1 6.37, V2 4.81, V3 5.53, V4 0.00; human maximum 1.00 (Eliot; the others 0.15-0.17).
- **Earned:** when the act of looking is itself the event (a discovery, a misreading the plot depends on).
- **Revision move:** state the perceived fact directly and keep the verb only where the looking matters.
- **Example (invented):** She saw that the curate had grown tired. She watched him lean upon the gate, and she understood that he would not walk as far as the mill.

### 2. Perceiving the unobservable

- **Proposed family:** NEW, "Impossible Witness" (adjacent to AIC-7).
- **Mechanism:** a close-third POV character is said to see another person's inner process, an absence or an unexecuted intention, framed as observed fact. Omniscient information is smuggled in through a perception verb.
- **Signature:** a perception verb whose complement is a mental act, an absence or a failure (search, fail to find, decide against, give up, not feel). Needs a reader or a dependency-parse rule; the pilot used a reader count.
- **Produced by:** Claude family (V1-V3), weakly the Gemini rewrite.
- **Rates (reader count):** V1 3.82, V2 2.55, V3 2.04, V4 0.92; human maximum 0.30 (James; the others 0).
- **Earned:** when the text marks it as inference (guessed, fancied, it seemed) or the narrator is openly omniscient.
- **Revision move:** give the observable sign and let the reader infer, or mark the inference as the POV character's guess.
- **Example (invented):** Across the orchard she watched him search his memory for the name, and fail to find it, and give the effort up.

### 3. One-line narrative paragraph beats

- **Proposed family:** AIC-9 Closure Inflation.
- **Mechanism:** a short declarative is set as its own paragraph after a long one, so every decision lands with the same drum-tap.
- **Signature:** share of dialogue-free paragraphs that are a single sentence under 10 words.
- **Produced by:** Claude family (V1-V3). The Gemini rewrite fused them away (see tell 12).
- **Rates (% of narrative paragraphs):** V1 19.6%, V2 18.0%, V3 17.0%, V4 3.8%; human maximum 3.6% (Eliot; the others 0%).
- **Earned:** occasionally, at a true turn of the scene. Rarity is what makes it work.
- **Revision move:** fold most beats back into the preceding paragraph; keep the isolated line for one or two real hinges per chapter.
- **Example (invented):** a long paragraph about the rain and a pony that will not cross the ford, followed by a paragraph that reads only: *She turned back.*

### 4. Vague "thing" / "something"

- **Proposed family:** AIC-2 Velvet Fog (Indefinite-pronoun gesture).
- **Mechanism:** placeholder nouns gesture at significance without naming it ("the one thing", "the last thing", "something in her").
- **Signature:** `\bthing\b|\bsomething\b` in narration; weight superlative or demonstrative "thing" and "Something" as a sentence subject.
- **Produced by:** Claude family (V1-V3).
- **Rates:** V1 3.50, V2 4.25, V3 3.20, V4 0.31; human maximum 1.22 (James).
- **Earned:** when the character genuinely cannot or will not name it, and the scene shows that resistance.
- **Revision move:** name the referent, or let a concrete detail carry it.
- **Example (invented):** A sermon on thrift was the last thing the doctor wished to hear, and something in him sank whenever the organ began.

### 5. Polysyndetic and-chains

- **Proposed family:** AIC-3 Echo Stack.
- **Mechanism:** four to six beats of unequal weight (impact, observation, feeling) chained with repeated "and", so every beat gets the same flat conjunction.
- **Signature:** narrative sentences containing three or more `\band\b`.
- **Produced by:** strongest in the human-revised Claude draft (V1); the Claude rewrites are marginal.
- **Rates:** V1 5.73, V2 2.83, V3 2.62, V4 0.61; human maximum 1.83 (Oliphant).
- **Earned:** for breathless or ritual sequence, used sparingly.
- **Revision move:** subordinate the minor beats, cut the redundant ones, and vary the joins by weight.
- **Example (invented):** He set down the lamp and wiped his spectacles and looked at the ledger and closed it and said nothing.

### 6. Emphasis padding and tautological tail tags

- **Proposed family:** AIC-7 Discourse Leak (Lexical Convergence).
- **Mechanism:** restrictive or intensifying tails restate what the sentence already fixed ("and no more", "nothing whatever", "word for word", "at all", "for an instant").
- **Signature:** `nothing whatever|and no more|nothing (\w+ ){0,6}save|nothing more than|no more than|\bat all\b|for an instant|word for word|and one only`
- **Produced by:** the Claude rewrites (V2, V3) and the Gemini rewrite (V4); barely present in V1.
- **Rates:** V1 0.32, V2 1.42, V3 1.17, V4 1.84; human maximum 0.15 (James).
- **Earned:** in a character's own emphatic speech.
- **Revision move:** lift the tail and check whether anything becomes less definite. Usually nothing does.
- **Example (invented):** He had called on the rector once, and once only, and nothing whatever had come of it.

### 7. Contraction-free dialogue (dialogue levelling)

- **Proposed family:** AIC-5 Puppet Dialogue.
- **Mechanism:** a rewrite expands every contraction and formalises every speaker to one register, so servants, children and gentry sound alike.
- **Signature:** contractions per 1,000 words of quoted dialogue, by speaker; a low value is the tell.
- **Produced by:** the Claude rewrites (V2, V3) and the Gemini rewrite (V4).
- **Rates:** V1 10.5, V2 0.0, V3 0.0, V4 2.5; human range 12.1 (Eliot) to 76.9 (James).
- **Earned:** for a deliberately stiff or formal speaker, set against others who are not.
- **Revision move:** restore contractions by speaker and class; keep formality as a character mark rather than a house style.
- **Example (invented):** "I do not know what you are talking about," said the stable-boy. "It is not my place to say, and I will not say it."

### 8. Semicolon procession

- **Proposed family:** NEW, "Period Costume" (register tokens deployed mechanically).
- **Mechanism:** the semicolon is used as a period-flavour token, slicing simple action into ;-separated steps, often ending on "; and".
- **Signature:** sentences with two or more semicolons; also the raw semicolon rate against a register baseline.
- **Produced by:** the Claude rewrites only (V2, V3).
- **Rates:**
  - Sentences with 2+ semicolons: V1 0.00, V2 2.26, V3 2.62, V4 0.00; human maximum 0.33 (Trollope).
  - Raw semicolons: V2 16.1, V3 15.7; human range 2.0-8.2.
- **Earned:** for genuinely balanced periodic sentences. Trollope and James use semicolons freely, but in argument, not stage business.
- **Revision move:** split action sequences into sentences; keep the semicolon for balance between ideas.
- **Example (invented):** The rain had stopped; the gardener came out; he looked at the roses, and shook his head; and the morning went on.

### 9. Fronted-adjunct and inversion saturation

- **Proposed family:** NEW, "De-tell Overcorrection" (or AIC-3).
- **Mechanism:** told to vary subject-first openings, a rewrite fronts prepositional, temporal and participial phrases, or inverts subject and verb, on most narrative sentences, so stress falls on incidental words.
- **Signature:** share of narrative sentences whose first token is outside the root subject's subtree (spaCy dependency parse), plus a count of verb-before-subject inversions.
- **Produced by:** the Gemini rewrite (V4).
- **Rates (% of narrative sentences):** V1 9.4%, V2 24.6%, V3 22.6%, V4 59.8%; human range 25.0% (Oliphant) to 41.1% (Eliot).
- **Reverse signal:** the human-revised Claude draft sits far *below* the human range; its subject-first monotony is itself atypical (an AIC-1 variance signal).
- **Earned:** for real emphasis or orientation; Eliot fronts often.
- **Revision move:** restore subject-first order wherever the fronted element is incidental; keep fronting where it sets place or time the reader needs first.
- **Example (invented):** Down the lane, past the smithy, went the doctor's gig. Of the vicar's opinion she had heard nothing. Slowly, with great care, the old woman lowered herself onto the bench.

### 10. "Only to" and "before -ing" tails

- **Proposed family:** NEW, "De-tell Overcorrection" (or AIC-3).
- **Mechanism:** sequential events are stitched with right-branching tails (", only to ...", "before ...-ing", ", leaving ..."), which pre-digest the sequence into irony or causality.
- **Signature:** `only to \w+|before \w+ing\b`. Trailing ", Xing" participles are a weaker co-measure.
- **Produced by:** the Gemini rewrite (V4).
- **Rates:**
  - "only to" / "before -ing": V4 1.84 (V1 0.32, V2 and V3 0.00); human maximum 0.15 (James).
  - Trailing participles: V4 7.36; human range 3.06-4.49 (marginal).
- **Earned:** for a single deliberate reversal.
- **Revision move:** give each event its own finite verb and let the sequence speak.
- **Example (invented):** He climbed to the top of the hill, only to find the view hidden by mist, before turning at last toward home, leaving the dog to follow.

### 11. Shell-noun labels for speech acts

- **Proposed family:** AIC-7 Discourse Leak.
- **Mechanism:** the line just spoken is labelled with an evaluative noun ("that taunt", "the entreaty", "such a compliment"), which settles its interpretation for the reader. This is the spoken-line gloss (tell 16) moved into a noun phrase.
- **Signature:** `(that|this|the|such an?) (taunt|thrust|entreaty|declaration|charge|appeal|retort|rebuke|reproach|admission|confession|threat|insult|jibe|plea|demand|challenge|avowal|sally)` in narration.
- **Produced by:** the Gemini rewrite (V4), weakly one Claude rewrite (V2).
- **Rates:** V1 0.00, V2 0.57, V3 0.00, V4 1.53; human maximum 0.15 (James).
- **Earned:** when the label is a character's own judgment and the scene contests it.
- **Revision move:** cut the label and let the listener's response interpret the line.
- **Example (invented):** "You were always the cleverer of us," said her cousin. That compliment cost her more than any rebuke.

### 12. Paragraph fusion (flattened paragraph lengths)

- **Proposed family:** NEW, "De-tell Overcorrection" (or AIC-1 variance).
- **Mechanism:** removing one-line beats by merging them makes every paragraph a similar long block, and decisive beats sit mid-paragraph at the weight of stage business.
- **Signature:** coefficient of variation of dialogue-free paragraph lengths; a low value is the tell.
- **Produced by:** the Gemini rewrite (V4).
- **Rates (CV):** V1 0.79, V2 0.75, V3 0.79, V4 0.49; human range 0.76-0.80.
- **Earned:** in a deliberately even, chronicle-like stretch.
- **Revision move:** re-break at decisions and at changes of time or place; let length follow weight.
- **Example (invented):** a scene in which every paragraph runs to four or five sentences of similar length, with the decisive refusal buried as the third sentence of the fourth paragraph.

### 13. Refrain-breaking synonym variation

- **Proposed family:** NEW, "De-tell Overcorrection" (elegant variation).
- **Mechanism:** a deliberately repeated formula (a vow, a ritual phrase, a repeated command) is varied with near-synonyms at each recurrence, which damages the ritual and can change what the speaker undertakes to do.
- **Signature:** a reader or diff check. Find a phrase repeated three or more times in the source and test whether the revision varies its head verb or noun. The pilot used a reader count.
- **Produced by:** the Gemini rewrite (V4) most; the Claude family is marginal.
- **Rates (reader count):** V1 1.27, V2 1.13, V3 1.17, V4 1.84; human maximum 0.89 (James).
- **Earned:** when the variation marks escalation or a change of meaning.
- **Revision move:** restore the formula.
- **Example (invented):** "I bless this house," she said at the door; at the kitchen, "I give my blessing to this room"; and at the stair, "Let this place be hallowed."

### 14. Generalising narrator aside (weak)

- **Proposed family:** AIC-7 Discourse Leak (Pseudo-aphorism).
- **Mechanism:** a worldly maxim about a human type gives the narrator an essayist's voice the source lacks.
- **Signature:** `as (\w+ )?(men|women|people) (are|do|will)|like most (men|women|people)|was not an? (man|woman|person) to|it may be said|(men|women) are apt`
- **Produced by:** one Claude rewrite (V2) only, 4 hits.
- **Rates:** V2 1.13, all other versions 0; human 0 in these samples. Eliot and Trollope generalise elsewhere, so this needs a larger control before it means anything.
- **Earned:** when the narrator has an established essayistic voice.
- **Revision move:** cut it, or make it the character's thought.
- **Example (invented):** He grew sullen, as clever men will when they are crossed in trifles.

### 15. "Found that she" filter (weak)

- **Proposed family:** AIC-1 Generic Hand.
- **Mechanism:** a feeling or action is reported as something the character "found" in herself, which adds a reporting layer.
- **Signature:** `(she|he) found (that )?(she|he|herself|himself)`
- **Produced by:** the human-revised Claude draft (V1), 3 hits.
- **Rates:** V1 0.96, V2 0.57, V3 0.29, V4 0.00; human maximum 0.17 (Oliphant).
- **Earned:** for genuine self-surprise.
- **Revision move:** report the feeling directly.
- **Example (invented):** On the vestry stair the organist found himself whistling a hornpipe, and found, too, that he could not stop.

## Marginal

### 16. Spoken-line gloss

- **Proposed family:** AIC-7 Discourse Leak.
- **Mechanism:** the narrator translates what a line of dialogue meant, within two sentences of it ("He meant that ...").
- **Signature:** "meant" or "what lay behind" in narration within two sentences after a quotation; best counted by a reader.
- **Produced by:** Claude family (V1-V3) and the Gemini rewrite (V4).
- **Rates (reader count):** V1 1.91, V2 1.98, V3 1.46, V4 1.23; human maximum 1.18 (James; the others 0.99-1.00).
- **Note:** Victorians gloss speech too, so the tell is density (about 1.7x human), not presence.
- **Revision move:** cut glosses where the reply or action already carries the meaning.
- **Example (invented):** "The roads will be very bad after the rain," said the squire. He meant that she ought not to go.

### 17. Generic-man simile

- **Proposed family:** AIC-8 Aesthetic Authority Laundering.
- **Mechanism:** comparisons in one mould ("as a man who ...", "like one who ...") translate a particular feeling into an unparticular man's.
- **Signature:** `(as|like) (a man|a woman|one) (who|that|does|is|might|would)`
- **Produced by:** Claude family (V1-V3). The Gemini rewrite replaced these with "as if" clauses, which fail the control.
- **Rates:** V1 0.64, V2 0.28, V3 0.29; human 0 in all four samples, but only 1-2 hits per version.
- **Revision move:** cut the simile when the action already conveys the feeling.
- **Example (invented):** He listened as a man listens to a sermon he has heard before.

## Fail the human control (period habits in this register)

These popular tells sit inside the Victorian human range. In this register they are not evidence of anything without a register-matched baseline.

| Measure | Human range (4 PD authors) | Model range (V1-V4) |
|---|---|---|
| Em-dashes | 6.21-17.0 | 0.32-7.98 |
| Triads (A, B, and C) | 0.50-1.38 | 0.00-0.92 |
| Hedged third (A, B, and almost C) | 0 everywhere | 0 everywhere (counter likely too narrow) |
| "as if" / "as though" | 0.46-1.66 | 0.00-1.23 |
| Negation-then-correction | 0.00-0.33 | 0.00-0.32 |
| Abstract-noun sentence subjects | 0.17-0.83 | 0.00 |
| Explicitation (reader count) | 2.00-2.32 | 2.23-3.68 (only V2 above, 1.6x) |
| Trailing participles (V1-V3) | 3.06-4.49 | 0.32-1.70 |
| Light-verb periphrasis | 0.00-0.17 | 0.00-0.31 (too rare) |

**Untested:** reciprocal mirror clauses ("he looked at X, and X at him"). All three readers flagged them, but the regex barely fires on any text; the counter needs a dependency-parse detector or a reader count.

## Proposed taxonomy changes (not yet adopted)

These are proposals for review. `aic-flags.md` keeps its nine families until a second source confirms them.

1. **NEW "Impossible Witness":** tell 2. Close-third POV reporting another mind, an absence or an unexecuted intention as perceived fact.
2. **NEW "Period Costume":** register tokens deployed mechanically (tell 8, and possibly contraction stripping, tell 7). The register-pastiche cousin of AIC-7 Lexical Convergence.
3. **NEW "De-tell Overcorrection":** signatures that appear when a revision pass is told to remove tells (tells 9, 10, 12, 13). Pair each with the tell it replaces, so an audit of a de-tell revision checks for substitution:

   | Removed tell | Substituted tell |
   |---|---|
   | Subject-first monotony (AIC-1 variance) | Fronted-adjunct and inversion saturation (9) |
   | Polysyndetic and-chains (5) | "Only to" / "before -ing" tails (10) |
   | One-line paragraph beats (3) | Paragraph fusion (12) |
   | Generic-man simile (17) | "as if" clauses (period habit, but watch density) |
   | Spoken-line gloss (16) | Shell-noun labels (11) |
   | Repetition flagged as Echo Stack (AIC-3) | Refrain-breaking synonym variation (13) |

4. **Register calibration:** em-dash, triad, "as if" and correctio thresholds need a register-matched baseline before firing on period pastiche.

## How to use this file

- In a craft-restoration pass on pastiche or on a draft with a known model history, read this file after `aic-flags.md`. Use the produced-by column to decide which tells to look for first, and the substitution table when the draft is itself a de-tell revision.
- Compare against a register-matched human baseline, not the general thresholds. The four public-domain samples above are one such baseline; a writer's own pre-AI work is better.
- Source triage still adjudicates each instance (`source-triage.md`). A pilot rate above the human maximum says the device is spent too often; it does not say who wrote the passage.
