# Rhetorical Countermoves: Restoration at the Pattern Level

Once Layer B has identified specific AIC patterns, the restoration moves go in two directions:

1. **Cut moves** — covered in `source-triage.md` (the payoff test, cut-the-setup-promote-the-payoff, the cut rules for each named pattern).
2. **Replace moves** — covered here. Specific rhetorical figures that counteract specific AI patterns.

This reference complements the cut rules. Where source triage says "cut the setup," rhetorical countermoves often say "and replace with a figure AI underuses." A passage that has been cut down sometimes needs a fresh move to fill the rhythm.

## Three Universal Principles

Before the figure-by-flag pairings, three principles that apply across every AIC flag.

### 1. The Payoff Test (Cut Move)

AI-fluent prose often takes the shape AI-fluent setup → character-voice payoff. Cut the setup, promote the payoff. Most flagged patterns yield to this single move. See `source-triage.md` for examples and the cases where the test fails.

### 2. Soft N-Gram Preservation (Replace Move)

EditLens and modern detectors compute "soft n-gram stability": how many phrases in an edited document have τ-similar matches in the source. Full synonymization across a draft drops soft overlap to near zero, which is a strong AI-edit signal. Human revision preserves idioms, collocations, and domain-specific phrases verbatim while rewriting around them.

**Operational rule.** When restoring a passage, identify the multi-word chunks that belong to the writer's voice or to the domain. Preserve them verbatim. Rewrite the connective tissue around them, not them.

**Examples of preservable chunks.**

- *Domain phrases.* In legal writing: "post-disposition representation," "community placement agreement," "technical violation." Don't rewrite to "after sentencing," "agreement upon return to community," "minor compliance failure."
- *Idioms and collocations.* "Kicked the can down the road," "at the end of the day," "moved the needle." Don't rewrite to elegant variation; idiom is anchor.
- *Author idiolect.* If the writer uses "kind of" instead of "somewhat," preserve it. If the writer favors "anyway" over "in any case," preserve it.

**Why it works against detection.** Pangram and EditLens learn that AI editing replaces near-synonymous phrases everywhere. A document that retains the writer's distinctive collocations passes detector tests that pure synonymization fails.

**Why it works against unearned fluency.** The writer's distinctive phrases are part of voice. Stripping them is voice damage even when the surface prose looks "improved."

### 3. Variance Reinjection (Replace Move)

When Layer A shows compressed variance, Layer B-level countermoves alone are insufficient. The document needs distribution-level restoration:

- **Sentence-length variance.** Add fragments. Add longer sentences with embedded clauses. Don't normalize toward the middle band.
- **Lexical diversity.** Allow local repetition. Keep idiosyncratic word choices. Resist thesaurus passes across the whole document.
- **Readability variance.** Don't flatten everything to plain English. Let technical paragraphs spike grade level; let anecdotes drop it.
- **Cohesion variance.** Cut some discourse markers. Allow vague antecedents where the prose can carry them. Preserve missing transitions.
- **Connective density.** If the document averages > 25 explicit connectives per 1000 tokens, cut roughly half. The reader can follow.

These are not figure-of-speech moves; they are distribution-level edits applied across the document. Run them before deploying targeted figures, because targeted figures applied to a uniformly smoothed document often read as ornamental rather than restorative.

---

## Why These Figures

AI overuses figures of organization and emphasis: anaphora, epistrophe, tricolon, parallelism, correctio. These are the figures a composition teacher can label in a five-paragraph essay.

AI underuses figures of distortion, compression, dislocation, audacity, and play. These require one or more of: a felt ear for rhythm, tolerance for oddness, semantic risk, or a willingness to sound briefly unhelpful.

The figures below are grouped by which AI prose pattern they counteract. Each entry includes the figure name, a plain definition, why it works against the specific AIC flag, and a fresh example. The examples are deliberately varied in register so writers can see how the move works across genres.

---

## Countermoves by Flag

### Against AIC-1: Generic Hand (Voice Singularity)

The problem: one voice for everything. The countermoves introduce idiosyncrasy, grammatical personality, and moves that belong to a specific consciousness.

**Anthimeria** — Turning one part of speech into another. "He childed as he talked." Shakespearean at its most visible, but useful anywhere a character's voice bends grammar to fit how they actually think. AI sticks to standard usage because deviation looks like error. A narrator who *nouns* a verb or *verbs* a noun has a voice that resists swap.

*Example:* "She decisioned her way through the morning, each choice smaller and harder than the last."

**Enallage** — Deliberate grammatical irregularity. Using the wrong tense, number, or person for effect. AI is trained against this. A character who shifts tenses mid-thought because that's how memory works, or a narrator who drops into second person for one searing paragraph, marks the prose as belonging to someone.

*Example:* "He walks into the kitchen and there she was, already gone."

**Catachresis** — Strained or impossible metaphor. AI likes familiar metaphor and smooths away the grotesque or wrong-feeling image. Catachresis is the metaphor that makes you pause, recalibrate, and then decide: yes, that's exactly right, even though it shouldn't be.

*Example:* "Her laugh had corners."

**Tmesis** — Splitting a word for emphasis. "Abso-bloody-lutely." Almost never natural for AI. The move requires knowing which word to split, where to split it, and what to wedge in. It stamps a voice on the page.

*Example:* "It was un-goddamn-believable, the quiet in that house."

---

### Against AIC-2: Velvet Fog (Scene Fog + Lexical Genericism)

The problem: generic word choices, ungrounded scenes, no sensory surprise. The countermoves force specificity, strangeness, and displaced attention. For the named subtype Indefinite-pronoun gesture, the cut rule (replace with concrete imagery) is in `source-triage.md`; the figures below help when restoration needs more than concretion.

**Hypallage (Transferred Epithet)** — Displaced description: "the sleepless night" gives the person's insomnia to the time. AI uses familiar transferred epithets but rarely generates fresh ones. A new hypallage forces the reader to reconstruct the sensation.

*Example:* "She drank her anxious coffee and stared at the apologetic light."

**Catachresis** — (Also counteracts AIC-1.) The impossible metaphor is the opposite of lexical genericism. Where AI reaches for the center of the semantic field, catachresis reaches for the edge and past it.

*Example:* "The hallway tasted of old decisions."

**Syllepsis / Zeugma** — One word governing two others in different senses. "She broke his car and his heart." The compression forces the reader to hold two meanings simultaneously. AI can produce this if prompted but does not naturally live there; the move creates the specificity that Velvet Fog lacks.

*Example:* "He carried the groceries and the conversation, both badly."

**Scesis Onomaton** — Piled nouns or adjectives without a verb. A descriptive burst that refuses to predicate. AI wants completion; scesis onomaton gives you the room without telling you what happens in it, and the absence of a verb *is* the sensory experience.

*Example:* "Fluorescent hum. Wet linoleum. The medicinal, ammonia-bright air of a place that cleaned itself and nothing else."

---

### Against AIC-3: Echo Stack (Structural Repetition)

The problem: mechanical patterns at sentence, paragraph, or scene level. The countermoves break rhythm through syntactic derailment, strategic interruption, and deliberate incompletion.

**Anacoluthon** — Syntactic derailment: the sentence begins one way and swerves into another. Very human. Hard for AI because it violates local grammatical planning. Injecting even one anacoluthon into a paragraph of echo-stacked sentences breaks the template.

*Example:* "She was going to tell him everything, the whole stupid history of it, but the dog needed out and the moment was gone before she'd even started."

**Aposiopesis** — Breaking off mid-thought. AI usually wants to finish the sentence, explain the implication, and tidy the emotion away. Aposiopesis trusts the reader to complete what the speaker won't.

*Example:* "If you'd been there when she said it, you would have... well. You know what you would have done."

**Diacope** — Repetition with a gap: "The horror, the horror." Unlike echo stacks, diacope is chosen repetition with purpose. AI repeats concepts more than exact words, because exact repetition looks suspiciously literary. Deploying diacope where the echo stack had mechanical variation reclaims repetition as a deliberate move.

*Example:* "It was fine. The whole thing was fine. She'd make it fine or die in the attempt."

**Hysteron Proteron** — Putting what comes later first. "Let us die and rush into the heart of the fight" (Virgil). Humans do this vividly and instinctively; AI prefers orderly chronology. Reversing temporal order in even one sentence breaks the scene-level template of setup → event → reaction.

*Example:* "She flinched before the door slammed, which was the worst part."

---

### Against AIC-4: Register Seams (Multi-Source Splicing)

The problem: detectable shifts where the drafting method changed. The countermoves create deliberate, controlled register shifts that make the seams intentional. Note the Pangram signal-9 tension flagged in `aic-flags.md`: do not flatten all variation across segments. The fix is making variation purposeful, not eliminating it.

**Enallage** — (Also counteracts AIC-1.) Deliberate grammatical irregularity can serve as a *chosen* register shift, replacing an accidental one. The writer controls the gear change.

*Example:* "The contract was clear: services rendered, payment due. And then you get home and it don't feel like clarity no more."

**Antimetabole** — Reversal with repeated words: "You like it; it likes you." When deployed at a register boundary, antimetabole makes the shift feel purposeful. The reversal signals awareness of the two registers and plays them against each other.

*Example:* "She'd learned to speak their language; now the language was speaking her."

**Chiasmus** — The looser, more elegant cousin of antimetabole. A crossing pattern (ABBA) that can bridge two different prose registers by making the structural shape hold what the vocabulary shifts.

*Example:* "What the report called progress, the family called loss; what they called grief, the system filed as closure."

---

### Against AIC-5: Puppet Dialogue (Mouth Uniformity)

The problem: all characters speak identically. The countermoves give individual characters signature moves that AI doesn't naturally distribute.

**Aposiopesis** — Some characters break off. Others don't. Making one character habitually trail off and another habitually finish every thought is a voice differentiator AI rarely introduces on its own.

*Example:*
"If she thinks I'm going to just..."
"You are. You will. Because that's who you are, Marcus."

**Meiosis** — Strategic belittling; cutting understatement with bite. AI tends toward measured hedging, not the kind of understatement that draws blood. Give one character meiosis and you've given them a voice.

*Example:* "Oh, he only destroyed my entire life. No big deal."

**Paronomasia** — Serious punning. Not jokes; wordplay that reveals character. One character who can't resist a pun, another who finds them disgusting, and suddenly the dialogue has friction.

*Example:* "You're in-credible, you know that? Literally not credible. Nobody believes a word."

**Bdelygmia** — A torrent of disgust terms. Wildly rhetorical, tonally extreme. Give it to the character who earns it and it becomes their signature: the one who, when pushed, erupts.

*Example:* "Pathetic, self-serving, mealy-mouthed, pants-wetting, every-excuse-in-the-book garbage. That's what that was."

---

### Against AIC-7: Discourse Leak (Assistant-Register Intrusion)

The problem: the prose organizes thought like an assistant rather than a narrator or character. The named subtypes (Negation hedge, Disguised correctio, Pseudo-aphorism, Manifesto cadence) have specific cut rules in `source-triage.md`. The figures below provide replacement moves where cuts leave a rhythm that wants filling.

**Epanorthosis (genuine, unstable)** — Real self-revision under pressure, not the canned "or rather" of AI correctio. The difference: AI correctio moves from wrong to right in a single smooth gesture. Genuine epanorthosis struggles, overcorrects, and sometimes fails to land.

*Example:* "I hated him. No, that's too easy. I hated that I understood him, and I hated more that understanding changed nothing."

**Metalepsis** — Remote, leaping figurative substitution. Too oblique for assistant prose, which prefers directness over layered allusive jumps. A narrator who reaches through two levels of figuration to get at what they mean is not an assistant.

*Example:* "She had that Eurydice look again, the one that meant: don't turn around."

**Asteismus / Urbane Irony** — Dry innuendo and irony that doesn't signal itself. AI can be ironic, but it usually signals irony too clearly, afraid the reader won't get the joke. Genuine asteismus lets the reader do the work.

*Example:* "The committee thanked him for his candor, by which they meant his career was over."

**Adynaton** — Extravagant impossibility. "When seas run dry." The opposite of commitment evasion: adynaton commits absolutely, through hyperbole so extreme it becomes its own kind of precision.

*Example:* "I'll forgive her when the Potomac flows uphill and the cherry blossoms bloom in January and the Metro runs on time."

**Epanalepsis** — Ending with the word that began the clause. Incantatory, distinctive, and almost never generated by AI in ordinary prose. It creates the sense of a mind circling back, landing on its own beginning.

*Example:* "Power was what he wanted and power was what hollowed him out and power, in the end, was all that was left."

---

## Countermoves for Nonfiction Patterns

The argument-shaped patterns flagged in `aic-flags.md` (Abstraction Shielding, False-Balance Construction, Hedge-and-Affirm, Recommendation Template, Authority Laundering) are mostly cut targets, but several rhetorical figures aid restoration.

### Against Abstraction Shielding

The problem: noun phrases that name no actor, no action, no location. The countermove restores referential specificity.

**Specification (no classical figure name)** — Replace each abstract noun phrase with the concrete referent. "Stakeholders agree" becomes "Council members, advocates, and the families we represent agree." "Youth-serving systems" stays only when the abstraction is the analytical unit; otherwise becomes "DYRS, OVSJG, and DCPS."

**Antonomasia** — Calling a person by a descriptive title or epithet, or vice versa. "The agency that committed her" instead of "DYRS." Useful when naming the actor through the relevant relation rather than the institutional name produces sharper analysis.

*Example:* "The agency that committed her, supervised her release, and revoked her placement is the same agency that now claims it cannot serve her."

### Against False-Balance Construction

The problem: manufactured judiciousness. The countermove replaces fake balance with named asymmetry.

**Procatalepsis (anticipation of objection)** — State the opposing view in its strongest form, then commit to your own. AI false-balance flattens; procatalepsis sharpens by acknowledging the strongest opposition explicitly.

*Example:* "Critics will say that ankle monitoring offers a calibrated alternative to detention, sparing youth the harm of incarceration. The Council should reject this framing. The evidence shows that monitoring extends carceral control rather than substituting for it: youth on monitors are revoked at higher rates than youth without monitors, for the same underlying conduct."

### Against Hedge-and-Affirm

The problem: gestural hedging followed by gestural commitment. The countermove makes both halves carry weight.

**Concession with Cost** — Concede only what costs the writer something. Generic concession ("while reasonable people may disagree") costs nothing. Specific concession ("our recommendation will increase short-term staffing costs by approximately 12%") costs something the reader can verify and negotiate.

*Example:* "Eliminating GPS monitoring would require redirecting roughly $1.4M annually from the agency's surveillance budget to community-based supervision. The Council should make that redirection. The cost is real; the benefit is larger and verifiable."

### Against Recommendation Template

The problem: generic advocacy verbs without specified actors, actions, or scope. The countermove reconstructs the recommendation around verifiable specifics.

**Imperative with Object** — Replace "DC must commit to..." with imperative verbs that name the actor and the action. "The Council should reject the FY27 budget request for GPS expansion" beats "DC must invest in alternatives."

*Example:* "The Council should reject the FY27 budget request for expanded GPS monitoring capacity and redirect those funds to community-based alternatives that DYRS has under-funded for the past three fiscal years. The Mayor should publicly support this redirection. The Independent Juvenile Justice Facilities Ombudsman should report quarterly on placement data to verify implementation."

### Against Authority Laundering

The problem: "Research shows," "experts agree," "studies suggest" without naming the research, the experts, or the studies. The countermove names and commits.

**Specific Citation with Stake** — Name the author, year, finding, and the stake the citation carries for your argument.

*Example:* "Scott and Steinberg (2008) demonstrated that adolescent risk-taking reflects ongoing neurological maturation rather than character deficit. The Council's reliance on YRA exclusions to filter out 'mature' youth treats developmental status as a fixed trait, contradicting the developmental science on which the YRA itself depends."

---

## Quick Reference: Figure-to-Flag Map

| Figure | Definition | Primary Flags |
|---|---|---|
| Anacoluthon | Syntactic derailment | AIC-3 |
| Adynaton | Extravagant impossibility | AIC-7 |
| Anthimeria | Part-of-speech conversion | AIC-1 |
| Antimetabole | Word-reversal | AIC-4 |
| Antonomasia | Descriptive naming | Nonfiction (Abstraction Shielding) |
| Aposiopesis | Breaking off mid-thought | AIC-3, AIC-5 |
| Asteismus | Urbane irony / dry innuendo | AIC-7 |
| Bdelygmia | Torrent of disgust | AIC-5 |
| Catachresis | Strained/impossible metaphor | AIC-1, AIC-2 |
| Chiasmus | Crossing pattern (ABBA) | AIC-4 |
| Concession with Cost | Costly concession | Nonfiction (Hedge-and-Affirm) |
| Diacope | Repetition with a gap | AIC-3 |
| Enallage | Deliberate grammatical error | AIC-1, AIC-4 |
| Epanalepsis | Ending with opening word | AIC-7 |
| Epanorthosis | Genuine self-revision | AIC-7 |
| Hypallage | Transferred/displaced epithet | AIC-2 |
| Hysteron proteron | Temporal reversal | AIC-3 |
| Imperative with Object | Specified action verb | Nonfiction (Recommendation Template) |
| Meiosis | Cutting understatement | AIC-5 |
| Metalepsis | Remote figurative leap | AIC-7 |
| Paronomasia | Serious punning | AIC-5 |
| Procatalepsis | Anticipation of objection | Nonfiction (False-Balance) |
| Scesis onomaton | Verbless noun/adjective pile | AIC-2 |
| Specification | Concrete referent for abstraction | Nonfiction (Abstraction Shielding) |
| Specific Citation with Stake | Named source with argumentative weight | Nonfiction (Authority Laundering) |
| Syllepsis/Zeugma | One word, two senses | AIC-2 |
| Tmesis | Word-splitting | AIC-1 |

---

## Usage Notes

**Don't deploy all of these at once.** A passage with anacoluthon, tmesis, bdelygmia, and scesis onomaton would read like a rhetorical exhibition, not prose. The goal is two or three well-chosen countermoves per flagged passage, selected for the specific pattern that needs breaking.

**Match the figure to the genre.** Tmesis and bdelygmia work in contemporary and literary fiction. They don't belong in historical romance or quiet literary realism unless the voice earns them. Hypallage and chiasmus are more genre-flexible. Aposiopesis works everywhere. The nonfiction countermoves work in argument-shaped prose; importing tmesis into testimony is unlikely to land.

**The figures counteract AI patterns; they don't replace craft.** A catachresis that doesn't illuminate anything is just a bad metaphor. An anacoluthon that confuses the reader is just bad grammar. The figure works only when it's the right move for the moment. The diagnostic (which flag fired, at what severity) tells you *what* needs fixing. The figure tells you *how* to fix it. The writer's judgment determines *whether* it fits.

**Some figures appear under multiple flags** because they do multiple things. Catachresis counteracts both Generic Hand (it's an idiosyncratic metaphor, marking voice) and Velvet Fog (it's a specific, sensory image). Aposiopesis breaks echo stacks (incomplete sentence in a field of complete ones) and differentiates dialogue (some characters trail off, others don't). Use the primary flag pairing for the first recommendation; note secondary uses when relevant.

**AIC-6 (Continuity Smear) has no rhetorical countermoves** because it's not a style problem. It's a world-model maintenance failure. The fix is tracking: keep a physical-state log (who's holding what, where everyone is, what each character knows) and check the passage against it. No figure of speech repairs a teleporting coffee cup.

**Order of restoration.** Run variance reinjection (Layer A countermoves) before deploying targeted figures (Layer B countermoves) before voice-driven cuts (Layer C countermoves). Reversed order tends to leave the most expensive moves doing the work the cheapest moves should have handled.
