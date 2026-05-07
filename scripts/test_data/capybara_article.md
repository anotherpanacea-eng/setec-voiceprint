# Capybara Astrophysicists and the Question of Publius

I've long been a Hamilton fan. I got into the Federalists because the musical made them popular. Then, like a hipster, I got tired of them somewhere during the musical's run. I'm back to being a fan now that everyone else has decided the musical is cringe. Tracing the identity of Publius is right up my alley, and Hamilton being at the heart of (yet another!) controversy is on-brand for a man who spent his life in them.

This year's example is Santiago Schnell's *Repairing the Ruins*, an anti-AI essay that has become popular in conservative and Catholic education circles. I don't know how the essay was drafted. But the published prose is a remarkably clean specimen of AI-shaped formal argument, with rhetorical structures that repeat at unusual density even when the sentence-level features look fine. Pangram flags the piece as AI-generated. My own script does not. That disagreement is the useful part. The best argument for AI assistance on a piece like this would be the essay's own: "An administrator may use [LLMs] to accelerate routine writing." Anti-AI essays *are* routine administrative writing right now. Walking through this case as a worked example will require a brief detour through 1788.

In 1787 and 1788, Alexander Hamilton, James Madison, and John Jay published 85 essays defending the proposed Constitution under the pen name *Publius*. They weren't trying to hide who they were forever. They were trying to make a public argument without three private men's egos getting in the way of it. By the time Hamilton was killed in 1804, he'd left behind a list claiming most of the disputed papers as his own. Madison's posthumous list claimed many of the same papers. They couldn't both be right.

The question of which Federalist papers Madison wrote and which Hamilton wrote sat unresolved for a century and a half. Historians had opinions. Editors had opinions. Nobody had proof. Then in 1964, two statisticians named Frederick Mosteller and David Wallace published a book that resolved most of the disputed cases by counting the word *upon*. Hamilton wrote it 3.24 times per thousand words. Madison wrote it 0.23 times per thousand words. On the disputed papers, the rate of *upon* came down on Madison's side, and so did the rates of *while* and *whilst* and *of* and a few other unglamorous words that neither author had any reason to think about while writing. Bayesian odds of 80 to 1, sometimes 1000 to 1, in Madison's favor on every contested paper. The book was one of the landmark early applications of Bayesian inference to a large-scale authorship problem, and it founded the field now called stylometry.

What Mosteller and Wallace had figured out is that we leave fingerprints in our prose without trying. The fingerprints sit below the level of conscious choice, in the articles and conjunctions and little hinges that hold a sentence together. Hamilton and Madison both used *liberty* and *tyranny* and *constitution* because the argument required them. The breath patterns underneath gave them up.

This is also, roughly, what every rap fan knows. You can identify Nas from a few bars before the chorus. You can hear that something is a Big Pun cadence even if you can't name the song. The trained ear doesn't need to be told. The flow is the fingerprint, and the syllables, once mastered, become identifying in a way the surface content never could be.

Lin-Manuel Miranda spent a decade absorbing this from specific rappers and then put it in the mouths of dead Federalists, and a 2015 Broadway audience that didn't know hip-hop could still hear that Hamilton talked differently than Burr without anyone explaining why. The deepest reading of *Hamilton* I've encountered argues that the musical is a staged theory of hip-hop as political authorship. The MC manufactures legitimacy through verbal mastery. Flow is how excluded people enter history. Hamilton, born poor and orphaned in the Caribbean, writes himself into the founding by producing essays at speed under a pseudonym, and the musical's argument is that this is the same move a rapper makes when they turn outsider status into the basis for institutional standing. It's all one continuum, one that runs from the pamphlets and the letters through the financial reports and the manifestos and onward down through history to the diss tracks of today. The breath patterns Mosteller and Wallace caught in 1964 are the same kind of marks a rap fan listens for to identify whose verse is whose. Cadence, density, the specific shape of how a person constructs a line, is identifying information. It is also how authorship gets manufactured in the first place.

By 2023, the field had a new question that Mosteller and Wallace never had to face: whether the text in front of you had an author at all.

The first generation of AI prose detectors was built on a clean intuition. AI text, on average, has lower *perplexity* than human text, where perplexity measures how surprising each next word is given the words that came before. Human writing is jumpier. Humans reach for unexpected words; humans use rare grammatical constructions; humans hedge and lurch and double back. AI writing, especially the kind produced by RLHF-aligned chatbots optimizing for fluent helpfulness, is statistically smoother. Each next word is more predictable than it would have been in a human paragraph on the same topic. Detectors like GPTZero and DetectGPT used this insight to score text as probably human or probably machine.

Then in January 2024, a team of researchers led by Abhimanyu Hans published a paper called *Spotting LLMs with Binoculars*. Their paper opens with what they nicknamed the *capybara problem*. They fed ChatGPT the prompt *write a few sentences about a capybara that is an astrophysicist*. The text ChatGPT produced began *Dr. Capy Cosmos, a capybara unlike any other...* and proceeded to describe the rodent's groundbreaking work in observational cosmology. The text was unmistakably AI-generated. It also had high perplexity, because the conjunction of *capybara* and *astrophysicist* is so unusual that ChatGPT itself was uncertain about how to continue from one word to the next. The previous generation of detectors looked at that high perplexity and confidently classified the text as human-written.

The fix Hans and his coauthors proposed was elegant. Score the same text against two language models, then look at the ratio between them. If two different language models are confused in the same places, the text was probably produced by something that shares the structure of their confusion, which is to say, by another language model. If the models diverge in their confusion, the text was probably produced by a human, because a human's surprises are not coordinated with any particular model's surprises. The ratio catches what the absolute number missed. This is the move the field calls *cross-perplexity*, and it's the basis for the most accurate current detectors.

The capybara astrophysicist became the canonical illustration of why detection is harder than counting *upon*. Mosteller and Wallace had two known authors and a closed set of disputed papers. The 2024 problem is open-ended. The text could be from any of dozens of models, prompted in any of millions of ways, possibly run through a paraphrase tool and a Unicode-mangler before hitting your inbox. The math has to keep up with all of it, and the rest of this post is about how it does and doesn't.

What follows is an attempt to walk through that math without making you do the math. The Schnell case will return at the end as a worked example, both the stylistic moves any trained reader can spot and the stylometric scores that confirm what the reader already saw. By then the question worth asking is the one Schnell's own argument raises: which writing tasks should AI do, and which shouldn't it?

## Where detection looks first

The first generation of detectors I described above (perplexity, burstiness, lexical diversity, sentence-length variance) all measure the same kind of thing. They look at the *shape* of the distribution of small features across a piece of text. Humans write with high-variance distributions: some short sentences, some long; some easy words, some rare; some predictable next-tokens, some surprising. AI prose, especially RLHF-aligned chatbot output, compresses those distributions toward the median.

The intuition, if you don't want to do the math, is roughly this: imagine recording every sentence you wrote for a year. You'd get a wide range. Some sentences would be six words long. Some would be forty-five. Some would have rare vocabulary; some would lean on common words. Now imagine an LLM doing the same exercise on the same prompts. The LLM's distribution would be tighter. Fewer six-word sentences. Fewer forty-five-word sentences. The averages might match yours, but the *shape* of the distribution wouldn't. The variance is where humans live.

Every classical detector exploits some version of this. GPTZero combines mean perplexity and sentence-perplexity variance. DetectGPT takes a different angle: it perturbs the text by swapping in synonyms and measures how perplexity changes. Human writing is robust to perturbation because the original word choices were made for many reasons; AI writing degrades because each token was selected to minimize local perplexity, and swapping breaks the optimization. Burrows Delta, the 1989 ancestor of modern stylometric classifiers, measures function-word frequencies in z-score space. It's the technique that unmasked Robert Galbraith as J.K. Rowling in 2013. Binoculars, the cross-perplexity detector I described in the intro, measures perplexity ratios across paired models. The math gets sophisticated, but the underlying claim is consistent: humans have wider distributions than aligned LLMs do, and the gap is detectable if you measure the right things.

So when I ran a script against Schnell's *Repairing the Ruins*, I expected the variance signatures to flag it. The piece reads as smooth, and most published prose that reads as smooth and gets traction in 2026 has been at least partly assisted.

The script returned *Lightly smoothed*.

## Where the script misses

Here are the variance numbers from Schnell's essay:

- 1,332 words across 84 sentences
- Sentence length: range 4 to 45 words, mean 15.86, standard deviation 9.98
- Lexical diversity (MATTR): 0.84
- Burstiness coefficient: −0.23
- Reading-level variance (FKGL): mean 10.9, standard deviation 5.2
- Function-word ratio: 0.46

By the metrics most variance-based detectors measure, this prose is more or less fine. The sentence-length distribution is wide, the lexical-diversity numbers sit comfortably in essayistic territory, and the reading-level variance is healthy enough to suggest a writer who can shift register paragraph to paragraph. The script flagged nothing because its heuristics were looking at surface distributions, and on the surface this piece reads as human as anything else in formal-register English. That is, in fact, a real finding. It just isn't yet the finding I came to make.

The Layer-A pass is also what protects the essay from one of the more legitimate concerns about AI detection in general. Schnell is Venezuelan-born; English is his second language. L2 English writing tends to occupy the same low-perplexity, low-burstiness region of distributional space as LLM output, which is why older detectors used to throw absurd false-positive rates at TOEFL essays. Yet the variance metrics that would once have falsely flagged a careful L2 writer pass this essay. Whatever the detection problem turns out to be, it isn't the L2 problem.

And it's worth being precise about why these two kinds of writing land in the same region of distributional space, since the convergence is real but it isn't single-mechanism. L2 writers compress variance because they're working in a constrained vocabulary and grammar; a smaller set of available words and constructions means fewer outliers, and the constructions that feel safe tend to land in the same middle zone. LLMs, by contrast, compress variance because the post-training reward signal selects for predictable, fluent continuations, and the resulting prose lives in the median region of the distribution by design. The two distributions overlap at the surface, which is why older detectors went so badly wrong on the L2 problem. But the structural-rhetorical patterns I'm about to walk through aren't L2 artifacts. They're patterns trained into the model from millions of formal essays, rhetorical templates that carry the prosodic feel of formal argument and recur at LLM density. Careful L2 writers, in my experience, don't reproduce them. They produce careful prose with constrained vocabulary, and that's a different signature entirely.

So if the script clears the piece, why am I writing about it as a case of AI-shaped prose?

Because surface variance is one kind of smoothing. There's another kind, and it doesn't show up in the variance metrics at all.

## The structural signature

Here are ten sentences from Schnell's essay that follow a single rhetorical pattern:

> *The danger is not only dishonesty — it is substitution.*
>
> *Learning is not the production of acceptable performances but the formation of a person capable of truth, judgment and responsibility.*
>
> *A real teacher is not merely a distributor of content. A real teacher is an experienced guide in inquiry.*
>
> *Its end is not the delivery of content, however accurate. It is the formation of persons capable of judgment, attention and intellectual honesty.*
>
> *Wisdom is formed in contact with reality, not in its simulation.*
>
> *The deepest challenge of AI in education is therefore not academic integrity, though that problem is real. It is whether we will allow our schools and universities to define learning as the production of acceptable outputs.*
>
> *The answer is neither panic nor blanket prohibition. It is pedagogical redesign.*
>
> *The point is not surveillance. It is intellectual ownership.*
>
> *They teach that education is not the production of impressive sentences. It is the formation of honest minds.*
>
> *AI has not created new educational problems; it has made old ones impossible to ignore.*

I count 21 instances of this pattern across the 1,332 words of the essay, which works out to 15.8 of these constructions per thousand words, and means roughly one in four sentences in the piece participates. The rhetorical move has a name. In classical rhetoric it's called *correctio*, the negation-then-affirmation pivot, and Schnell is using a familiar pattern at unusual density.

I wanted to check what 'unusual' actually meant here, and so I ran the pattern against a small public-domain comparison corpus: Newman's *The Idea of a University*, Arnold's *Culture and Anarchy*, Mill's *On Liberty*, and Chesterton's *All Things Considered*. All four use negation-pivots. Of course they do. The move is native to moral and philosophical prose, and Schnell isn't doing anything any of them couldn't have done. But in a strict count of explicit "not X but Y" variants, the comparison texts run roughly 0.3 to 1.0 such pivots per thousand words across long argumentative books, while Schnell's essay runs 15.8 per thousand across thirteen hundred words. The issue is not that he uses a foreign rhetorical device. The issue is density. (That itself is a correctio. The pattern is hard to avoid even for someone watching for it, which is part of why I think the diagnostic is density rather than presence.) A familiar habit has been compressed until it starts to read like a template firing every few sentences.

This is what I mean by structural smoothing, and it's the layer the variance metrics can't see. The piece's sentence lengths are varied; its vocabulary is varied; its reading levels are varied. What isn't varied is the shape of how arguments get made. Roughly every fourth sentence runs the same rhetorical engine, and once the eye knows what to look for, the regularity is hard to unsee.

The triplets are the second tell. *Truth, judgment, and responsibility. Judgment, attention, and intellectual honesty. Attention, judgment, and love. Real questions, careful judgment, and responsibility for truth. Reading, questioning, hesitation, and revision.* I count fourteen three-or-four item lists in the essay, running at 10.5 per thousand words. Triplets are classical, and Catholic essayistic prose has lived on them for centuries; the figure isn't foreign here either. Yet at 10.5 per thousand they stop reading as classical inheritance and start reading as rhythmic fill, the way a phrase repeated three times in conversation can stop sounding like emphasis and start sounding like nervous habit. Once you start counting them you can't stop seeing them.

Then there's the manifesto cadence: *More writing done in class. More oral defense of arguments. More seminars organized around live questions rather than passive downloads of information. More laboratory and studio work...* Four anaphoric heads in a row, each launching a slogan-shaped recommendation. The paragraph is the rhetorical fingerprint of LLM-generated policy advice, and it sits in the essay precisely where you'd otherwise expect a paragraph of substantive policy argument.

Then the professional-parallel stacks, which I think give the prose away most clearly to a reader who knows the genre. Two adjacent paragraphs, one running *A professor may use them to... A researcher may use them to... An administrator may use them to...*, the next running *A student can submit polished prose without having... A researcher can produce a competent summary without having... A professional can sound informed without having...*. Stacked echo structures performing comprehensiveness without the substantive differentiation that would actually distinguish professors from researchers from administrators from students. The shape says "I have considered all the cases." The content says nothing in particular about any of them.

None of this is detectable from sentence-length variance. All of it is detectable from reading the piece with the patterns in mind. This is structural-rhetorical smoothing that lives one level above the variance distributions where most detectors look, which is why so much AI-assisted writing in 2026 passes detectors built for 2023.

Pangram, a current production detector, doesn't rely on variance metrics alone. It uses two methods designed to catch what surface metrics miss. Synthetic mirroring trains the classifier on pairs of human and AI-generated documents matched in topic, tone, and format, so the model can't cheat by detecting topic distributions. EditLens regression trains a regression head on labeled mixed-authorship data, so the model can output a continuous estimate of AI involvement rather than a binary verdict. Pangram's report on Schnell's essay flags it as AI-generated. Two different evidentiary registers converge on the same picture: the rhetorical patterns the eye catches, and the stylometric scores from a production system trained against the entire current generation of LLMs.

## When the argument loses its place

The structural patterns I've described so far are surface phenomena. They tell you that the prose has been shaped by something, but they don't tell you whether the argument the prose makes actually lands. The deeper diagnostic shows up at a level structural patterns can only point toward, which is the moment when the prose makes claims it can't quite sustain.

Here's the load-bearing paragraph of Schnell's essay, the one that has to do the real philosophical work:

> *This clarifies why certain acts cannot be delegated to machines without ceasing to occur at all. Attending carefully to a text, weighing conflicting evidence, judging whether a conclusion is warranted, taking responsibility for what one claims — these are not ancillary tasks. They are the work by which a mind is formed. No machine can perform them in our place — not because machines lack processing power, but because these acts have no effect unless a person performs them. Their purpose is not to produce an output. It is to form the one who does them.*

I want to take this slowly, because it's where Schnell's whole argument turns and where (I think) the prose stops doing the work the argument requires.

The claim is that certain acts *cannot be delegated to machines without ceasing to occur at all*. That's a strong claim, and it's worth holding for a moment to see what it has to mean. *Weighing conflicting evidence* is one of the listed acts. Yet machines weigh conflicting evidence all the time, in the relevant sense. Bayesian updating is weighing evidence. Comparing model outputs is weighing evidence. Ranking citations by relevance is weighing evidence. When a machine does this, the act is occurring. The output exists. The weighing has happened.

But Schnell's prose says these acts *cease to occur at all* when machines perform them. That can't be right in the sense the words ordinarily carry, and so the prose has to mean something stronger and more interesting: that the acts are constitutive of human formation in a way that does not transfer through their machine-performed analogues. When a student weighs conflicting evidence, on this reading, something happens to the student that doesn't happen when a machine produces the same output. The act and the formation it produces are inseparable, and the inseparability is the philosophical claim the prose needs to make.

That's a defensible position, and in fact it's the argument I think Schnell needs to be making. But the prose doesn't quite make it. It says the acts *have no effect unless a person performs them*, which is a different and weaker claim. (Of course they have effects. The text gets analyzed, the conclusion gets produced, the citation gets ranked. The effects exist.) Then the prose pivots once more: *Their purpose is not to produce an output. It is to form the one who does them.* That's closer to the argument. Yet notice: it's a third claim, not a clarification of the first two. The paragraph has slid from *acts cease to occur* to *acts have no effect* to *acts have a different purpose*. Three different claims, each weaker or differently scoped than the last, each presented as if it followed from the previous.

This is one common failure mode of AI-shaped prose, and I'd argue it's the one that matters more than the structural patterns: the transitions preserve fluency while the argument quietly changes claims. The sentences are well-formed individually. The cadence keeps moving. The actual argumentative thread breaks somewhere between *cease to occur at all* and *form the one who does them*, and the break is invisible because the rhetorical surface keeps moving smoothly over it.

The variance metrics don't catch this. The correctio counter doesn't catch this either. What catches it is reading carefully, and noticing the difference between an argument doing its work and an argument performing the appearance of doing its work.

## The arms race

I want to step back from the Schnell case for a moment and say something about what detection in 2026 actually looks like as a field, because most of the popular discourse around AI detection is at least eighteen months out of date and missing the genuinely interesting recent moves.

OpenAI shipped an AI text classifier in January 2023, and they withdrew it in July 2023, citing low accuracy. The published numbers (and OpenAI deserves real credit for publishing them honestly, since they didn't have to) were 26% true positive rate at 9% false positive rate, which translates to: about a quarter of AI text caught, with one in eleven human texts incorrectly flagged. That's unusable for any serious purpose, and OpenAI to their credit said so plainly.

The next generation of detectors did substantially better. Binoculars, the cross-perplexity method I described in the intro, hit ninety-something percent true positive rates at 0.01% false positive rates on most known LLMs at release in early 2024. That's a meaningful improvement, and the field has continued moving since. Pangram, a current production detector used in academic-integrity contexts, retrains continuously against new models and humanizer tools, and their published numbers show a 4× improvement on humanizer evaluations between Pangram 3.1 and 3.2 in August 2025, with retraining specifically triggered by detected regressions on Claude releases.

On the other side, the humanizer industry has built an arms race of its own, which is itself worth noticing. DIPPER, an academic paraphrasing tool released in 2023, dropped one major detector's recall from 70% to under 5% by simply paraphrasing AI text into different surface forms while preserving meaning. The commercial humanizer tools (StealthGPT, UndetectableAI, Quillbot, and a long tail of smaller services) sell themselves explicitly as detection-evasion. Some work by inserting zero-width Unicode characters that look like spaces. Others add deliberate typos. The most sophisticated paraphrase AI text into surface forms that shift the statistical signature without changing the underlying claims.

The technical sophistication on each side, as far as I can tell, is asymmetric. The detectors are doing real engineering, training continuously against adversarial corpora and hiring linguists and statisticians to refine their methods. The humanizers, by and large, are running cheap text transformations that exploit known weaknesses in existing classifiers. Yet the market for evasion exists because the market for detection exists, and the volume on both sides is large enough that the arms race is now a stable industry rather than a passing controversy.

What all this means for a piece like Schnell's, in concrete terms: the variance-based detectors that a naive run might use have already lost the older battle. A careful AI-assisted essay in formal-register prose, with the surface variance preserved by the model's natural fluency in formal English, will pass most variance heuristics with no trouble at all. The detection that catches Schnell's piece is the structural-rhetorical kind, which can't be fully automated yet because nobody has formalized the patterns enough to write a reliable script. For now, the trained eye is still ahead of the variance metrics on this particular kind of smoothing.

That gap is the post-2024 detection problem in microcosm. The math has to learn to measure things it wasn't built to measure, and the things in question are genuinely harder to measure than sentence lengths.

## Style provenance, not idea provenance

Here's what no detector can do, and what no future detector will be able to do without major changes in how these systems are built:

Detection measures *style provenance*. It can tell you something about who or what produced the surface form of a piece of text. It cannot tell you who had the underlying idea, who's responsible for the argument, who deserves credit or blame for the substance.

In Schnell's case, the question of style provenance is genuinely interesting. The prose has the structural signature of AI-assisted writing. Whether Schnell wrote a draft and the model polished it, whether he prompted the model with an outline and edited the output, whether a staff member did either of these things, the detection methods can't distinguish among these scenarios. They can only tell you that whatever final pass the prose went through, that pass produced text with the structural fingerprints of model-assisted writing.

The question of *idea provenance* is separate, and it's the question that matters most for evaluating the essay's argument. *Is education the formation of persons capable of judgment?* That question doesn't depend on who wrote the sentence asserting it. The argument is correct or incorrect on the merits, regardless of provenance.

Detection measures where the surface form sits in a distribution of possible surface forms. That's a different question than authorship in the morally loaded sense. A piece can be heavily AI-assisted and still carry an author's genuine ideas through. A piece can be entirely human-written and still parrot ideas its author hasn't actually thought through. The detector doesn't know the difference, and it's not built to.

That's the move I'd ask any reader of this post to take seriously: detection tells you about provenance of style, not about authorship of ideas. Both questions matter. They're not the same question. Treating them as the same question is what gives AI detection its current bad reputation in some quarters, and it's a reputation it partly earned by overclaiming.

## Schnell, formed and unformed

I want to come back to Schnell's argument now, because this is the part that I find genuinely difficult.

The argument the essay makes is that education is the formation of persons, not the production of acceptable performances. That AI is fine for performance writing and corrosive for formation writing. That students need to do the work themselves because the work is what forms them, and a finished output without the work that produced it is hollow. The argument has Catholic intellectual roots going back to Newman and beyond, and it's defensible on its own merits.

The essay arguing for it appears, by the structural-rhetorical evidence, to have been at least partly produced by the technology Schnell argues against using for formation work. This creates a problem I want to be careful about, because there are several versions of the critique and not all of them are equally serious.

The shallow critique is hypocrisy: Schnell wrote an anti-AI essay with AI assistance, therefore his argument is undermined. The Catholic intellectual tradition has long resources for thinking about hypocrisy ("the compliment vice pays to virtue," in La Rochefoucauld's formulation). Hypocrisy doesn't refute an argument. The argument is correct or incorrect independently of its arguer's consistency. The shallow critique doesn't go very far.

The medium critique is category confusion: Schnell's own essay distinguishes between performance writing (where AI is fine) and formation writing (where it isn't), and an op-ed for a Catholic publication is clearly category-one writing. *An administrator may use [LLMs] to accelerate routine writing*, the essay says, and an anti-AI op-ed in 2026 is exactly that kind of routine work. So Schnell isn't even contradicting himself in any narrow sense. He may have just done what his own essay says is fine.

The deep critique is harder, and it's the one I think the case actually warrants. By Schnell's own framework, the legible evidence of formation, the kind of prose a reader is supposed to recognize as the product of years of careful reading and responsible thinking, is starting to be indistinguishable from the surface output of a model. If a Dartmouth provost can produce a piece of prose stylometrically indistinguishable from AI on a topic he's supposedly spent decades thinking about, then the formation tradition's own warrant (the implicit claim that you can recognize formation by reading what formed people produce) is in trouble. Not because Schnell did anything wrong. Because the legible signal of having-done-the-work is no longer a reliable signal.

That's the version of the critique that takes Schnell's own argument seriously. The argument may be exactly right. The harder question it raises, perhaps without meaning to, is how anyone is supposed to tell anymore. The formation/performance distinction depends on being able to recognize formation when you encounter it. If recognition is no longer possible from the prose alone, the distinction becomes harder to maintain in practice than in theory.

Schnell's essay is a small data point about how hard. That's why the case is worth thinking about, and worth thinking about *with* him rather than against him.

## What this is good for

I'll close with the contrarian move I've been hinting at since the intro.

The case for using AI more is stronger after this analysis than before, in specific contexts. Schnell's own framework gives the principle: AI is fine for writing tasks where the artifact is the point, and corrosive for writing tasks where the process is the point. The essay he wrote did its job for its venue. It impressed readers. It generated discussion. It made a real argument, regardless of how the prose was produced. By the standards Schnell himself authorizes for administrative writing, it succeeded.

What the case shows is that the discrimination required, between performance writing and formation writing, between contexts where AI assistance is appropriate and contexts where it's corrosive, is a human skill that hasn't yet been articulated well. We're still learning when to use AI and when not to. We're still learning what the moral and pedagogical stakes look like in the different contexts. The Schnell case sits awkwardly at the boundary because the essay is *about* formation but is itself category-one writing, and the boundary is genuinely confusing to navigate.

For students writing essays on Aristotelian friendship, AI assistance bypasses the formation that's the point of the assignment. That's the bright case Schnell is right to defend.

Administrators producing op-eds about why students shouldn't bypass formation are in a different position. AI assistance is exactly what Schnell's own framework says it should be. The dim case, but a defensible one.

For the rest of us, the discrimination is going to be harder, and the stylometric tools are going to keep getting better at flagging where AI was, without being able to tell us where AI should have been. That's a different problem than the one academic-integrity software was built to solve. Solving it requires the kind of work the variance metrics can't do, the kind of work I tried to do here: reading carefully, noticing when prose makes claims it can't sustain, noticing when the rhetorical surface is doing more work than the argument under it.

The capybara astrophysicist is the canonical illustration here too. The capybara prose tripped older detectors because the topic was anomalous enough to look human. The Schnell prose passes newer surface-variance detectors because the structural smoothing lives above the variance signature. Each generation of detection learns to measure what the previous generation missed. The trained reader, for now, is still ahead.

That won't last forever. By 2028 there will be detectors that catch structural smoothing, and humanizers that defeat them, and a third generation of tools that catch what those humanizers do. The arms race is the field. The math keeps going.

What stays unchanged is the older question, the one Mosteller and Wallace took up in 1964 and Hamilton's contemporaries argued about in 1788: who actually wrote this? Stylometry has always been able to measure the surface. It has never been able to tell us whose thinking is behind it. None of this measures who had the idea. It measures who shaped the surface. The harder question the detector can't answer is how much of a writer's thinking is recoverable from prose the writer didn't fully compose. Schnell's essay points at education as the place such recovery has always happened. Whether it still can, in the age of prose like this, is a question the case keeps open.
