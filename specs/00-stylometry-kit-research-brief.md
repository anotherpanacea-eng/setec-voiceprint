# Stylometry Kit — frontier research brief (2026-06-06)

Synthesis of three parallel research passes (RoBERTa/neural stylometry; EditLens
& partial-AI detection; frontier-kit gaps), reconciled against fetched primary
sources. Goal: identify what a *complete* stylometry / AI-prose-forensics kit
should contain that SETEC lacks, judged on two axes — **orthogonality** (does it
add a measurement axis we don't have?) and **license cleanliness** under
GPL-3.0-or-later.

> Sandbox HTTP was partially WAF/403-blocked during research; a handful of exact
> license tags and PAN leaderboard numbers are drawn from indexed summaries, not
> line-by-line from the primary PDF. Every such claim is in the
> **License-verification TODO** at the bottom and must be confirmed before any
> weights/code are vendored.

## The license principle (applies throughout)

Model **weights are data, not code.** Apache-2.0/MIT code is GPL-3-compatible
(Apache-2.0 is one-way compatible into GPLv3). The real blocker is **non-commercial
/ research-only / unstated** weight or dataset licenses, which prevent
redistribution. In every blocked case the *method* (the published math) is not
copyrightable, so the path is **clean-room reimplementation** against a
GPL-compatible corpus — never vendoring the restricted artifact. SETEC's existing
`fetch_pangram_editlens.py` (CC BY-NC-SA, local-only) is the correct pattern.

## Findings by area

### A. Neural style embeddings — the "voice fingerprint" axis (strongest add)

Maps text → a dense **style vector**; compares passages by cosine similarity.
Genuinely orthogonal to everything SETEC ships: classical signals are interpretable
scalars, Surface 5 is perplexity/surprisal — an embedding is a *learned holistic
voice manifold* supporting same-author verification and drift detection without
thresholds.

| Model | Shape | Code / weights license | Notes |
|---|---|---|---|
| **LUAR** (Rivera-Soto, EMNLP 2021) | 512-d author embedding, ~83M | **Apache-2.0 code AND weights** (`LLNL/LUAR`, `rrivera1849/LUAR-MUD`) | Cleanest license of the set; Reddit-trained (social-media register skew). |
| **Wegmann 2022** "Same Author or Just Same Topic?" | 768-d, RoBERTa sentence-transformer | sentence-transformers stack Apache-2.0; **confirm weight card tag** | *Controls for topic* — directly mitigates SETEC's deepest confound. Honest STEL caveat: captures mostly punctuation/casing/contraction. |
| **STAR** (Huertas-Tato 2023) | 768-d, RoBERTa-large, 70k authors | **weight license unstated → BLOCKER** | Strongest raw verification numbers; blocked until card clarified. |
| **DeTeCtive** (NeurIPS 2024) | multi-level contrastive; retrieval DB | **repo/weights license unconfirmed** | Reframes AI-detection as authorship-style discrimination; *training-free incremental adaptation* attacks domain shift. Gate on license. |

Footprint: RoBERTa-base/large class, 0.5–1.4 GB weights, **CPU-feasible** for
forensic batch sizes, optional GPU <2 GB. Sources:
[LUAR EMNLP21](https://aclanthology.org/2021.emnlp-main.70/) ·
[LUAR repo](https://github.com/LLNL/LUAR) ·
[Wegmann RepL4NLP22](https://aclanthology.org/2022.repl4nlp-1.26/) ·
[STAR](https://arxiv.org/pdf/2310.11081) ·
[DeTeCtive](https://arxiv.org/abs/2410.20964).

### B. Supervised RoBERTa AI-detectors — skip as a verdict

OpenAI's RoBERTa GPT-2 detector (MIT, clean) and MAGE's Longformer hit high
*in-distribution* accuracy but are **single-verdict, ESL-biased, and brittle**:
Liang et al. found 61% of human TOEFL essays flagged as AI
([Patterns 2023](https://arxiv.org/abs/2304.02819)); RAID shows a stale
RoBERTa-GPT2 detector *improves* under paraphrase because it reads dataset
artifacts ([RAID, ACL 2024](https://github.com/liamdugan/raid)). Adding one as an
authority conflicts with SETEC's brand. **Only defensible use: a labeled
bias-demonstration control** that emits the score *with* the Liang caveat and
refuses "this is AI." Closed-set fine-tuned RoBERTa attribution is dominated by
the embedding approach (A) and needs a fixed author roster SETEC doesn't assume.

### C. EditLens & the dosage frontier — clean-room, with heavy caveats

**EditLens** (Thai et al., ICLR 2026; [arXiv:2510.03154](https://arxiv.org/abs/2510.03154))
is a **document-level edit-magnitude regressor**: RoBERTa-Large fine-tuned with MSE
against a BERTScore-style similarity proxy between pre/post-edit text. It explicitly
argues **sentence/span boundary detection is ill-posed** for layered edits and
reframes as document-level regression — the same design instinct as SETEC's
refusal. Weights **and** dataset are **CC BY-NC-SA (NC → no GPL reuse)**; the
*recipe* is cheaply clean-room reproducible given a non-NC paired corpus.

The dosage science is shaky and **corroborates SETEC's refusal to emit "% AI"**:
[APT-Eval (ACL 2025)](https://arxiv.org/pdf/2502.15666) shows 11 SOTA detectors
cannot separate minor from major polish (RoBERTa-large: 47.7% minor vs 52.0% major
flagged — near-flat); [Guo et al.](https://arxiv.org/abs/2506.03501) recover
involvement only *in-distribution*. EditLens's own magnitude correlation is a
*moderate* 0.606. Span lineage (SeqXGPT, RoFT, SemEval-2024 8C, MixSet) is noisy —
best boundary error ~16 words — and SeqXGPT mostly reuses surprisal SETEC already
computes.

### D. Zero-shot detectors not yet in SETEC

| Method | Axis | Footprint / license | Orthogonal? |
|---|---|---|---|
| **Fast-DetectGPT** ([arXiv](https://arxiv.org/abs/2310.05130)) | conditional-probability *curvature* | 1 small LM, laptop; **MIT** | Yes — curvature ≠ cross-perplexity ratio. Cleanest near-term add. |
| **Intrinsic dim / PHD** ([arXiv](https://arxiv.org/abs/2306.04723)) | topological dim of embedding cloud | embeddings + TDA; verify repo license | **Strongly orthogonal**, multilingual; research-grade. |
| **Raidar** ([ICLR24](https://arxiv.org/abs/2401.12970)) | rewriting-invariance (LLM edits AI text less) | needs LLM access; license unconfirmed | Yes — paraphrase-robust; clean-room the idea if blocked. |
| **DNA-GPT** ([ICLR24](https://openreview.net/forum?id=Xlayxj2fWp)) | regeneration n-gram divergence | needs generator access | Yes, explainable; strong only with candidate's model family. |
| **Lastde++** ([arXiv](https://arxiv.org/pdf/2410.06072)) | multi-scale entropy of prob-sequence | small LM | Overlaps DivEye autocorrelation — marginal. |
| **Glimpse** ([ICLR25](https://arxiv.org/abs/2412.11506)) | reconstruct full dist from API top-logprobs | API cost; MIT-ish | Enabler, not an axis — lets white-box detectors use proprietary scorers. |
| **GLTR** ([repo](https://github.com/HendrikStrobelt/detecting-fake-text)) | per-token rank/prob buckets | 1 LM; Apache-2.0 | Low novelty; worth borrowing as a *glass-box visualization*. |

### E. Authorship verification SOTA (PAN@CLEF 2024/2025)

PAN's "Voight-Kampff" Generative-AI Authorship task moved from pairwise (2024) to
the harder single-text setting (2025). Fine-tuned transformer classifiers win
in-distribution; **Binoculars and Fast-DetectGPT remain the strongest zero-shot
baselines**; the named open problem is **OOD generalization, short texts, and
obfuscation robustness** — exactly SETEC's adversarial-fixture territory. SETEC's
General Imposters is the right *unsupervised* primitive; the kit lacks a **PAN-style
obfuscation-fixture replay harness** (run our signals against the public Unicode /
paraphrase / language-switch variants). Highest-value, license-clean addition.
([PAN24 task](https://pan.webis.de/clef24/pan24-web/generated-content-analysis.html))

### F. ESL / multilingual fairness — most defensible, most underbuilt

SETEC *preaches* the [Liang ESL false-positive finding](https://arxiv.org/abs/2304.02819)
but the kit lacks an **ESL/L2 + translated-text fairness slice** in the validation
harness, and is English-only (spaCy). Adding L2/translated fixtures to the existing
FPR/TPR/ROC harness is near-term, dependency-free, and directly strengthens the
project's central honesty claim.

### G. Watermark detection — narrow, defer

Detecting Kirchenbauer green-list / [SynthID-Text](https://www.nature.com/articles/s41586-024-08025-4)
watermarks only works if the generator opted in *and* you hold/guess the key.
SynthID's reference detector ships in HF Transformers (Apache-2.0). Worth at most a
small **opt-in "if a watermark key is supplied, test for it"** module with explicit
"absence says nothing" docs. PAN 2026 adds a watermarking track — track, don't build.

## Consolidated ranked shortlist (the kit additions)

| # | Capability | Tier | License | Orthogonality | Effort |
|---|---|---|---|---|---|
| 1 | **Voice-fingerprint / same-author embedding surface** (LUAR + Wegmann) | near-term | Apache-2.0 (LUAR clean; Wegmann verify) | new learned voice manifold | low–med (wrap weights) |
| 2 | **Fast-DetectGPT curvature** (Surface 5 add) | near-term | MIT | new zero-shot statistic | low |
| 3 | **PAN obfuscation-fixture replay harness** | near-term | public fixtures | extends validation surface | low |
| 4 | **ESL/L2 + translated fairness slice** | near-term | none/clean | operationalizes the ESL honesty claim | low |
| 5 | **EditLens-style clean-room edit-magnitude regressor** | research | clean-room only (NC blocked) | dosage-as-calibrated-estimate | med (GPU + corpus) |
| 6 | **Intrinsic-dimension / PHD** | research | verify | topological — most orthogonal | med (GPU/TDA) |
| 7 | **Raidar rewriting-invariance** | research | verify / clean-room | paraphrase-robust | med (LLM access) |
| 8 | **Watermark-key test module** | defer | Apache (SynthID) | narrow | low, low-value |

**Skip as verdicts:** off-the-shelf RoBERTa/MAGE detectors (posture conflict;
bias-control only), closed-set RoBERTa attribution (dominated by #1), SeqXGPT/RoFT
span detection (ill-posed), Lastde++/Glimpse/GLTR as core signals.

## License-verification TODO (before vendoring any weights/code)

- [ ] `AnnaWegmann/Style-Embedding` — confirm weight-card license tag.
- [ ] `AIDA-UPM/star` — weight license unstated (blocker).
- [ ] DeTeCtive repo + checkpoints — license unconfirmed.
- [ ] Raidar, Lastde, DNA-GPT, GPTID/PHD repos — confirm LICENSE; clean-room if non-permissive.
- [ ] APT-Eval corpus license — before use as a negative-control benchmark.
- [ ] PAN 2024/2025 fixture redistribution terms — before bundling replay fixtures.
- [ ] Confirm PAN leaderboard specifics against the CEUR overview PDF before quoting.

## Honest hype check

Headline AUROCs (~0.95) are **in-distribution**; every survey shows them falling
under obfuscation / OOD / short text — which *is* SETEC's thesis. No new detector
should quietly become a verdict. Embeddings are not identity proof (topic leakage,
short text, translation, adversarial style transfer). Dosage estimation works only
in-distribution; if ever added it must be a same-corpus calibrated estimate with
explicit OOD caveats, never an absolute "% AI."
