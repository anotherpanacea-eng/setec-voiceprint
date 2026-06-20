# watermark-probe — KGW green-list z-test ("was this watermarked?")

> A NEW positive-evidence axis SETEC lacks today: given a text **and the operator-supplied
> watermark key/hash parameters**, compute the KGW green-list z-statistic and report it as
> one more per-signal value with an operator-side reliability band. A high z is
> **watermark-consistent with a *known* scheme** — never "AI." A low z (or no key) is
> **not** evidence of human authorship. The entire surface is a presence probe for one
> family of token-level watermarks, not a detector.

- **Status:** M1 BUILT (`feat/watermark-probe`). M1 is the stdlib z-test + posture surface; M2
  is a thin gated key-sweep convenience only.

## Review findings folded into M1 (as built)

Five adversarial-review findings were folded into the build. They override the original draft
text below where they conflict:

1. **(P1) No-verdict structural guard is scoped to `results`, not the whole envelope.** Every
   `output_schema.build_output` envelope ALWAYS carries a top-level `ai_status` field
   (operator-supplied routing, defaults `None`). The no-verdict guard — and its acceptance test —
   asserts the forbidden verdict keys (`is_watermarked` / `is_ai` / `is_human` / `verdict`) are
   absent from `envelope['results']` (recursively over nested result dicts), NOT the top-level
   envelope. This matches the `test_fast_detect_curvature` precedent (`'verdict' not in r`).
   `ai_status` is a **permitted harness convention** at the top level — a maintainer must not try
   to forbid it.
2. **(P2) The band's posture lineage is `agency_abstraction_audit`** (which ships a default
   descriptive-INTENSITY band, never a class), NOT `binoculars_audit`'s class-band
   (`ai_likely` / `human_likely`, gated behind operator thresholds). The watermark bands name
   evidence-STRENGTH-for-a-named-scheme, not a class, so they ship PROVISIONAL by default. The
   `fast_detect_curvature` reference is kept ONLY for the "no shipped threshold on the z"
   discipline, not for the band.
3. **(P2) The thresholdable-triple back door is closed, both ways.** (a) The
   `strongly_watermark_consistent` top tier is DROPPED — only two descriptive bands ship
   (`under_powered` / `watermark_consistent`), so there is no "maximum" band that reads as a fire
   signal. (b) The `does_not_license` text names verbatim "do not threshold band or p_value … to
   manufacture an is_watermarked / is_ai / is_human decision," an acceptance test asserts the band
   string set contains no class/boolean token, and a structural-separation acceptance test asserts
   `watermark_probe.py` imports nothing from a selection/calibration/threshold-setting layer
   (named real modules: `conformal_gate`, `calibrate_thresholds`, `calibration_drift_monitor`,
   `calibration_survey`, `binoculars_calibrate`).
4. **(P3) `p_value` is reported at full float precision** (not `round(p, 4)`, which would floor a
   `z=6` tail to 0), plus a transform-safe `neg_log10_p` field named so the `output_schema` `[0,1]`
   probability bound does not apply to it. Fixture tolerances do not depend on a rounding that
   erases tail precision.
5. **(P3) One surface name chosen deliberately: `watermark_probe`.** The capability `surface:`
   field, the `claim_license_surfaces/watermark_probe.txt` filename, the `task_surface` string, and
   the envelope `task_surface` are ALL identical (a mismatch fails the `TASK_SURFACE_LABELS` lookup
   at `build_output` time).
- **Tier:** near-term (stdlib, additive — a NEW surface, the first watermarking axis in SETEC).
- **GPU required:** no. The KGW z-test is counting + arithmetic; it needs **no model**. (The
  *original* generator needed a model; the **detector** is a hash-seeded partition + a token
  count + a z-score. That asymmetry is the whole reason this is a stdlib surface.)
- **Builds on / sits beside:**
  - The per-signal **discrimination-evidence** card model (`fast_detect_curvature` /
    `binoculars_audit` / `surprisal_audit`): a standardized z-score reported with **no shipped
    threshold and no verdict band** — the exact posture this surface inherits, with one extra
    load-bearing caveat (absence ≠ human).
  - The voiceprint house contract idiom (spec 23 `23-rank-turbulence-delta.md`): a new stdlib
    script, an `output_schema.build_output()` envelope with a `ClaimLicense` block, a
    `capabilities.d/` fragment, the surface-addition paper trail.
  - Reuses tokenization conventions from `stylometry_core.word_tokens` **only as a
    documented fallback** — see the tokenizer-mismatch caveat below, which is the single most
    important honest limit of this surface.
- **arXiv roots (cite each in the eventual PR body + the `changelog.d/` fragment, not only here):**
  - **A Watermark for Large Language Models** — Kirchenbauer et al. (the KGW green-list/red-list
    token-biasing scheme + its z-statistic detector), [arXiv:2301.10226](https://arxiv.org/abs/2301.10226).
    *The method this surface implements.*
  - **On the Reliability of Watermarks for Large Language Models** — Kirchenbauer et al. (empirical
    watermark survival under human paraphrase, rewrite, copy-paste mixing),
    [arXiv:2306.04634](https://arxiv.org/abs/2306.04634). *Supplies the realistic detection-power
    bands and the "decays under heavy rewrite" caveat.*
  - **Watermark under Fire: A Robustness Evaluation of LLM Watermarking (WaterPark)** — Liang et al.
    (10 watermarkers × 12 attacks; systematic scrubbing/spoofing),
    [arXiv:2411.13425](https://arxiv.org/abs/2411.13425). *Defines the probe's known blind spots —
    the catalog of what a token-level z-test cannot see.*
- **License decision:** **clean-room the method.** The KGW z-test is published math (a
  hash-seeded green-list partition of the vocabulary + a one-proportion z-test on the green-token
  count). Reimplemented from the paper in stdlib; no weights, no vendored watermark code.
- **Partition scope (the load-bearing interop boundary).** This detector scores against the
  green-list partition **this module defines** — `partition_prf =
  voiceprint-greenlist-v1/sha256-seed+pyrandom-shuffle` (a SHA-256(key, context) seed feeding a
  deterministic shuffle), the KGW *construction* but **NOT** byte-compatible with the official KGW
  reference processor's seeding schemes (`simple_1` / `selfhash` / `minhash`) or its RNG device.
  Tokens generated by a different processor (including the official one, or a vendor's watermark)
  fall in an **unrelated green list here and systematically false-negative**. A result is
  meaningful only when the operator generated with **this exact partition**; the partition is
  stamped in `assumptions.partition_prf` and the claim-license `comparison_set`, the capability
  `do_not_use_when` routes official/vendor schemes away, and a fixture proving the partition
  matches is required before any cross-generator interop is claimed. The use cases below assume
  the operator owns this partition — they are **not** a claim to detect arbitrary third-party KGW
  watermarks.

## Goal

SETEC today has *no* watermarking surface at all (confirmed against the capability review,
`arxiv-capability-review/02-robustness-evasion-watermark.md` and `SHORT-LIST.md` item 5: "the
two genuinely uncovered areas are a dedicated recursive-paraphrase harness and any watermarking
surface at all"). Its entire evidence stack is **stylometric / discrimination** — it reasons
about *how unlike a human distribution* a text looks. A watermark probe is a categorically
different channel: it asks whether a text carries the **statistical fingerprint of a specific,
operator-named generation scheme**. When it fires, it is *positive* evidence of a known scheme —
not an inference from style, but the recovery of an injected signal.

That makes it valuable **and** dangerous in exactly opposite ways, and this spec is honest about
both:

- **Positive direction (the value):** if the operator holds the green-list key/hash parameters
  for a scheme they suspect (their own pipeline, a vendor's documented watermark), the z-test is
  a fast, model-free, *high-confidence-when-it-fires* confirmation that the text was produced by
  **that** scheme. That is a cleaner signal than any stylometric one — it is the recovery of a
  planted bit pattern, not a judgement about prose.
- **Negative direction (the trap, and the load-bearing caveat):** a low z, or no available key,
  means **nothing** about human authorship. The text could be (a) human, (b) machine without a
  watermark, (c) machine with a *different* watermark scheme this key can't see, or (d) machine
  with this scheme but the watermark **scrubbed by paraphrase/rewrite** (the 2306.04634 / WaterPark
  decay result). Absence of a watermark signal is **not** evidence of human authorship — full
  stop. The surface must be architecturally unable to be read that way.

This spec adds a model-free **M1** that computes the green-list z-statistic over a target given
operator-supplied scheme parameters, reports it as a descriptive value with a **reliability band**
(`detection-power` framing, not a pass/fail), and refuses — in the claim license, structurally — any
AI/human verdict and any absence-is-human reading. **M2** is a thin, gated *convenience*: a
multi-key/multi-`γ`/`δ` sweep for an operator who wants to try several documented schemes at once.
M2 buys no new power; it only batches M1 — and it is gated precisely so the headline capability
(the z-test) stays fully CI-testable stdlib.

## Honest framing (limits, surfaced not hidden)

- **A positive is "watermark-consistent," never "AI."** A z above the operator's band says
  *"this text's green-token count is statistically improbable under the null that tokens were
  chosen independently of this green-list partition"* — i.e. it is **consistent with the named
  KGW scheme**. It is not "AI-generated" (a human could in principle be fed green-listed words;
  a different model with the same key collides; and the claim is always *relative to the supplied
  key*). The report says **watermark-consistent with scheme `<key_id>`**, and the claim license
  refuses the leap to "AI."
- **Absence is not evidence of human authorship (the headline caveat).** Spelled out above; it is
  repeated in (1) the Goal, (2) here, (3) the claim license `does_not_license`, (4) the
  `render()` footer, (5) the empty/low-z render line, and (6) the `_golden_task_surface_labels`
  label. Six places, because this is the one reading that turns a useful probe into a false
  accusation of plagiarism, and a reviewer will (rightly) hunt for any surface that drops it.
- **The probe needs the key — it cannot guess the scheme.** KGW detection is **keyed**: the
  green-list at each position is a deterministic function of the hash seed, the hash scheme
  (left-hash / prefix window), the vocabulary, and `γ` (green fraction). Without those, there is
  no partition and no test. The probe is **operator-parameterized, never sniffed** — the operator
  supplies `--key` (hash seed), `--gamma`, `--hash-scheme`, and a vocabulary. A "scan for unknown
  watermarks" mode is explicitly **out of scope** (it is the watermark-stealing problem, an attack,
  not a probe; see Considered & rejected).
- **The tokenizer-mismatch caveat (the quiet failure mode).** KGW partitions the *model's* BPE
  vocabulary and biases *model tokens*; the z-test must count green *model* tokens. If the probe
  re-tokenizes the text with a **different** tokenizer than the one the watermark was applied
  over, the green-list lookups are misaligned and the z collapses toward 0 — producing a **false
  negative that looks like "no watermark."** So: the probe takes an **operator-supplied
  tokenization** (`--vocab` + a token-id stream, or a named tokenizer the operator asserts matches
  the scheme). `stylometry_core.word_tokens` is offered **only** as a clearly-labelled
  *whitespace-fallback* for toy/demo and CI fixtures, never as a real detection path — using it
  against a real BPE watermark is documented as guaranteed to under-detect, and the `assumptions`
  block records which tokenization was used so a mismatch is visible, not silent.
- **Detection power decays, and the band says so (2306.04634 / WaterPark).** The reliability paper
  shows the watermark survives light paraphrase but **erodes under heavy rewrite, translation, and
  copy-paste mixing**; WaterPark's 10×12 matrix catalogs scrubbing/spoofing attacks. So the band
  is a **detection-power band keyed to text length and (declared) rewrite exposure**, not a fixed
  threshold — and a near-band z on a short or possibly-rewritten text is reported as
  *under-powered*, not as "no watermark."
- **Known blind spots, named (WaterPark + the cluster review).** A token-level green-list z-test
  **cannot** see: semantic / sentence-level watermarks (k-SemStamp, [arXiv:2402.11399], a different
  family entirely), SynthID-style schemes, distribution-shift / Gumbel schemes with a different
  detector, or any watermark scrubbed below the noise floor. The `do_not_use_when` and the
  `does_not_license` enumerate these so the operator never reads a negative as "no watermark of any
  kind."
- **It is not an authenticity or quality signal.** A watermarked text can be excellent; an
  unwatermarked one can be slop. This measures the presence of one injected statistical pattern,
  nothing about value, originality, or authorship.

## The load-bearing design question (and its answer)

**Why is a "was this watermarked?" probe not just another AI-detector that ships a threshold and
a verdict?** Because the z-statistic is *so* clean when it fires that the temptation to promote it
to a binary "AI: yes/no" gate is overwhelming — and that promotion is the trap, in two distinct
ways:

1. **The false-positive direction (an unkeyed/colliding scheme).** A z computed against the
   *wrong* key is a one-proportion z-test against a null the text doesn't actually obey; on
   adversarial or merely unlucky inputs it can spike. A shipped "watermarked = AI" threshold would
   turn a key mismatch into a plagiarism accusation.
2. **The false-negative direction (the absence-is-human reading).** This is the worse one. The
   instant a surface emits `is_watermarked: false`, a downstream consumer reads it as `is_human:
   true`. But the probe is blind to every scheme it doesn't hold a key for, and to every scrubbed
   watermark. A `false` here is `unknown`, never `human`.

So, exactly as the discrimination-evidence surfaces (`fast_detect_curvature` ships
**uncalibrated, no threshold, no verdict**):

- **No shipped threshold, no verdict, no boolean.** The surface emits the **raw z-statistic**, the
  **green-token fraction**, the **p-value under the null**, and a **descriptive reliability band**
  (`under_powered` / `watermark_consistent` — TWO tiers only as built per review finding 3a, no
  `strongly_*` top tier; operator-tunable and
  PROVISIONAL) — but **no** `is_watermarked` / `is_ai` / `is_human` field, and **no** top-level
  aggregate that reads as a decision. The band names *evidence strength for a known scheme*, never
  a class. (Guard: a structural test asserts no `is_ai` / `is_human` / `is_watermarked` / `verdict`
  key in the envelope.)
- **The negative is `unknown`, structurally.** Where the surface must express "the signal did not
  fire," it uses the word **`unknown`** (band `under_powered` or `no_watermark_signal_for_this_key`),
  never `human` and never an unqualified `false`. The `does_not_license` makes "absence ⇒ human" a
  refused claim, and the `render()` empty-case line says so in prose.
- **Keyed, parameterized, never sniffed.** The green-list partition is a deterministic function of
  operator-supplied `(key, gamma, hash_scheme, vocab)`. The probe makes **zero** inference about
  *which* scheme might be present; it tests exactly the scheme the operator names. No "detect any
  watermark" mode (that is an attack capability — out of scope).
- **One value among many.** The z is reported as **one more per-signal card**, designed to sit in
  an evidence pack beside the stylometric signals, never to override them. It is positive evidence
  for one narrow question, not a master switch.

## Design (model-free z-test is M1; the multi-key sweep is the M2 convenience)

A new `watermark_probe.py` (M1, stdlib, no SETEC consumer, no model). The KGW detector, per
Kirchenbauer et al. 2301.10226:

### M1 — model-free core (stdlib; the "build first" piece — the entire headline capability)

- **Green-list partition (keyed, deterministic).** For a vocabulary `V` and a hash seed `key`, at
  each token position `t` the scheme seeds a PRNG from `hash(key, context(t))` and selects a
  **green list** of size `⌊γ·|V|⌋` from `V`; the rest is the red list. `context(t)` depends on the
  `--hash-scheme`: **`left-hash`** (seed from the single previous token id — the paper's simplest
  scheme) is the M1 default and the only one required for v1; **`prefix-h`** (seed from the prior
  `h` tokens) is an operator-selectable variant. The partition is **reproducible**: same
  `(key, gamma, hash_scheme, vocab, context)` ⇒ same green list (pinned in a test).
- **Green-token count + the z-statistic (the paper's detector).** Over the `T` scored token
  positions (positions with a valid context), count `|s|_G` = number of tokens that fell in their
  position's green list. Under the null hypothesis *"the text was generated without knowledge of
  the green-list rule,"* each token is green with probability `γ` independently, so `|s|_G ~
  Binomial(T, γ)`. The KGW z-statistic is the standardized green-count:

      z = (|s|_G − γ·T) / sqrt(T · γ · (1 − γ))

  and the one-sided p-value is `p = 1 − Φ(z)` (probability of seeing this many or more green
  tokens by chance). A large positive z ⇒ improbably many green tokens ⇒ **consistent with the
  named watermark**. Both `z` and `p` are reported; **no threshold is shipped.**
- **Reliability band (descriptive, PROVISIONAL — 2306.04634 framing).** Map `(z, T,
  declared_rewrite_exposure)` to a descriptive band, **operator-tunable, defaults PROVISIONAL**:
  `under_powered` (T below a length floor, or z within a margin where the reliability paper shows
  detection is unreliable — reported as *insufficient evidence either way*) and
  `watermark_consistent` (z at/above the operator margin on a long-enough, not-heavily-rewritten
  text). TWO tiers only as built (review finding 3a — no `strongly_*` top tier, so no band reads as
  a fire signal). The band is **detection-power language, never a verdict**; it
  carries the length floor and the decay caveat in `assumptions`. There is **no `no_watermark`
  band that reads as human** — the low-z case is `under_powered` / `unknown`.
- **Tokenization input (the mismatch guard).** The probe accepts the target as **either** (a) an
  operator-supplied **token-id stream + `--vocab`** (the real path: the operator asserts these
  match the watermarking tokenizer), **or** (b) raw text plus a `--tokenizer fallback:whitespace`
  flag that uses `stylometry_core.word_tokens` and **stamps `assumptions.tokenization =
  "whitespace_fallback"` plus a stderr warning that this under-detects real BPE watermarks.** The
  `assumptions` block always records the tokenization mode and `|V|`, so a mismatch is auditable.
  No model is loaded on any path (a real BPE tokenizer, if the operator wants one, is their input,
  not a dependency this surface pulls).
- **Parameters, validated.** `--key` (required; the hash seed), `--gamma` (green fraction, default
  the paper's `0.5`, validated `0 < γ < 1`), `--hash-scheme` (`left-hash` default | `prefix-h`),
  `--vocab` (required for the real path). `validate_params` raises `ValueError` (the house refusal
  type) on a missing key, `γ ∉ (0,1)`, an empty vocab, an unknown hash scheme, or `T` below a hard
  minimum (too few scored positions for any z to be meaningful → routes to `bad_input`).
- **`WatermarkProbeResult`** (`@dataclass(frozen=True)`, `to_dict`/`render`): `z`, `p_value`,
  `green_fraction` (`|s|_G / T`), `gamma`, `n_scored_tokens` (`T`), `green_count`, `band`
  (the descriptive string), `key_id` (a non-secret label/hash of the key for provenance — **never
  the key itself**), `hash_scheme`, and `assumptions` (`{tokenization, vocab_size, hash_scheme,
  length_floor, rewrite_exposure, band_is_provisional: true, decay_caveat}`). **No** `is_watermarked`
  / `is_ai` / `is_human` / `verdict` field (the load-bearing question). `render()` **always** ends
  with the two caveats — *"a positive is consistent with the named scheme `<key_id>`, not 'AI'; a
  negative is not evidence of human authorship (this key sees one token-level scheme; other schemes,
  and scrubbed watermarks, are invisible to it)."* The low-z / under-powered render line says
  *"no green-list signal for this key — this is `unknown`, not 'no watermark' and not 'human'."*,
  never "unwatermarked."
- **`setec run watermark_probe` / CLI:** `python3 plugins/setec-voiceprint/scripts/watermark_probe.py
  --target T --key KEY --vocab V.json [--gamma 0.5] [--hash-scheme left-hash] [--tokens TOKENS.json
  | --tokenizer fallback:whitespace] [--rewrite-exposure none|light|heavy] [--json] [--out F]`.
  Loads **no model**. Missing/invalid params exit non-zero via `build_error_output(...,
  reason_category="bad_input")`; the key is never echoed to stdout/logs (only `key_id`).
- **JSON envelope:** via `output_schema.build_output()` with a `ClaimLicense` block (the spec-23
  pattern). `results` carries the `WatermarkProbeResult.to_dict()` fields. Unavailable / too-short
  / bad-param cases route through `build_error_output(..., reason_category="bad_input")`.
- **Claim license — `licenses`:** "the KGW green-list z-statistic and p-value of the target under
  the **operator-supplied** scheme parameters `(key, gamma, hash_scheme, vocab)`, and a PROVISIONAL
  detection-power band — i.e. whether the green-token count is statistically improbable under the
  null for **this named scheme**." **`does_not_license` (the load-bearing refusals):** any AI/human
  or authorship verdict; any reading of a positive as "AI-generated" (it is *watermark-consistent
  with the named scheme*, relative to the supplied key); **any reading of a low/zero z, or no
  available key, as evidence of human authorship** (absence ≠ human — the probe is blind to other
  schemes, to semantic/SynthID-class watermarks, and to scrubbed watermarks); any claim about
  *unknown* schemes (it tests only the supplied key); and a reminder that the whitespace fallback
  under-detects real BPE watermarks. Bands are operator-side / PROVISIONAL.
- **Separation + posture guards (structural):** after stripping comments + string literals (so the
  docstring may *name* the forbidden symbols as posture documentation), `watermark_probe.py`
  references no `is_ai` / `is_human` / `is_watermarked` / `verdict` key in its emitted envelope
  (asserted over the actual output dict, not just the source), imports nothing from a
  selection/training layer, and `import`ing the script pulls **no** model dependency (stays
  stdlib). The key never appears in `results` / stdout (only `key_id`).

### M2 — multi-key / multi-parameter sweep convenience (gated, stubbed in CI)

- **Why gated at all, given M1 is stdlib.** The z-test itself is stdlib and fully CI-tested in M1 —
  M2 buys **no new statistical power**. M2 is a *convenience batcher*: run M1 across a small
  operator-supplied **catalog of candidate schemes** (several `(key, gamma, hash_scheme, vocab)`
  triples — e.g. a vendor's documented presets) and a couple of `prefix-h` window sizes, then
  report a per-scheme card table so the operator can ask "do *any* of the schemes I hold keys for
  fire?" The gate is **not** a model gate (there is no model); it is a **scope/seam gate**: the
  catalog format, the optional operator-supplied real BPE tokenizer adapter, and any future
  integration with `compose_evidence_pack` live behind a clearly-marked seam so the **headline z-test
  surface stays a clean, fully-tested stdlib M1**. CI exercises M2's batching logic over **stub
  parameter sets and the whitespace fallback** — no real watermark model, no GPU, no network.
- **Strictly additive, same posture.** Each per-scheme card is exactly an M1
  `WatermarkProbeResult`; the sweep adds **no** cross-scheme aggregate, **no** "best match"
  verdict, and **no** boolean. It reports each scheme's `(z, p, band)` independently. Testing
  several keys is **not** a hypothesis-fishing license: M2 records `n_schemes_tried` in
  `assumptions` and the render footer warns that **trying many keys inflates the chance one fires
  spuriously** (a multiple-comparisons caveat — the operator must keep their key set principled,
  not a brute-force sweep). It is **never** wired into selection/validation; it is a read-only
  evidence batcher routed to the human.
- **No new surface.** M2 reuses the M1 `watermark_probe` task surface (a sweep is the same axis,
  batched); it adds CLI flags (`--catalog catalog.json`), not a new `claim_license_surfaces/`
  fragment or a new golden label.

## Considered & rejected (posture)

- **Shipping an `is_watermarked` boolean / an "AI: yes/no" verdict / a fixed z threshold.** The
  load-bearing trap. A boolean invites the absence-is-human reading; a fixed threshold turns a key
  mismatch or a short text into a false accusation. The surface emits `z` / `p` / a **descriptive
  PROVISIONAL band** and **no** boolean or class field — the `fast_detect_curvature`
  "uncalibrated, no verdict" posture, with the extra absence-≠-human refusal.
- **A "scan for unknown watermarks" / scheme-discovery mode.** Detecting *which* watermark (if any)
  is present without a key is the **watermark-stealing / reverse-engineering** problem
  ([arXiv:2402.19361], flagged in `SCAN-2-voicewright.md` as an *attack* that strengthens, not
  opens, the own-output gate). It is an offensive capability, not a probe, and it is out of scope
  by posture. The probe is **keyed**: it tests exactly the operator-named scheme.
- **Embedding a watermark (a generator).** SETEC is the analytic half of the fleet; embedding is
  voicewright's domain and even there it is **gated** (SHORT-LIST: own-output watermarking — VOW
  [arXiv:2604.27666] — is GATE-tier, bounded by SynthID-attack/Vaporizer evidence). This surface
  **detects** a known scheme; it never embeds one.
- **Auto-loading a real BPE tokenizer / making `transformers` a dependency.** Would (a) make the
  headline surface non-stdlib and CI-gated for no statistical gain, and (b) silently mask the
  tokenizer-mismatch hazard. Tokenization is **operator input** (token-id stream + vocab); the
  whitespace fallback is a labelled toy path that records its own under-detection.
- **A `no_watermark` band, or reporting the negative as `human` / unqualified `false`.** This is the
  precise failure that converts a probe into a plagiarism engine. The low-z case is
  `under_powered` / `unknown`, the word `human` never appears as an output value, and the refusal
  is in the claim license + render + the surface label.
- **Folding the z into a combined AI-score or any selection/validation target.** It is one
  per-signal card for an evidence pack, never an aggregate input, never a gate — the same held-out
  discipline as every discrimination signal.
- **Covering semantic / SynthID / Gumbel watermarks in v1.** Different families with different
  detectors (k-SemStamp [arXiv:2402.11399] is sentence-embedding k-means; SynthID is
  distribution-shift). v1 is **token-level KGW green-list only**, and the blind spots are named in
  `does_not_license` so a negative is never read as "no watermark of any kind." A semantic-watermark
  probe is a possible later, separate surface — not this one.

## Non-goals

- Embedding / applying a watermark (generation; voicewright's gated domain).
- Detecting *unknown* or *unkeyed* watermarks, or reverse-engineering a scheme (the
  watermark-stealing attack).
- Any AI/human or authorship verdict, any boolean, any combined score; any selection/validation
  target. No change to `loop`-style selection, no new consumed-SETEC dependency (this is a producer
  surface, not a consumer).
- Semantic / SynthID / Gumbel / distribution-shift watermark families (v1 is token-level KGW only).
- Loading a model or a real tokenizer as a dependency (tokenization is operator input; the
  fallback is a labelled toy path).
- Calibrating the band to an absolute detection probability — it ships PROVISIONAL; a labelled
  watermarked/clean corpus would calibrate it later (see Calibration posture).

## Anti-Goodhart / posture guardrails (must hold)

The watermark probe is a **per-signal positive-evidence card** routed to the human — **never** a
selection/validation target, never a reward, never folded into a combined AI-score · every output
is the **raw `z` / `p` / green-fraction + a PROVISIONAL detection-power band**, with **no**
`is_watermarked` / `is_ai` / `is_human` / `verdict` field and no aggregate that invites an auto-gate
(structural test over the emitted envelope) · a positive is **watermark-consistent with the named
scheme `<key_id>`, never "AI"**; the claim is always relative to the operator-supplied key · a
low/zero z, or no available key, is **`unknown`, never `human`** — absence ≠ human authorship is
refused in the claim license, the render footer, the empty-case line, and the surface label (the
blind spots — other schemes, semantic/SynthID classes, scrubbed watermarks — are enumerated) ·
the green-list partition is **operator-keyed, parameterized, never sniffed**; no unknown-scheme
discovery mode (that is an attack) · tokenization is **operator-supplied**; the whitespace fallback
is labelled as under-detecting real BPE watermarks and the tokenization mode is recorded in
`assumptions` so a mismatch is auditable, not silent · the secret **key is never emitted** (only a
non-secret `key_id`) · the band carries the **reliability-decay caveat** (survives light paraphrase,
erodes under heavy rewrite — 2306.04634 / WaterPark) and an under-powered short/short-rewrite text
is reported as *insufficient evidence*, not "no watermark" · M2's multi-key sweep adds **no**
aggregate/best-match verdict and warns about the **multiple-comparisons** inflation of testing many
keys · `import`ing the script pulls no model dependency (stdlib).

## Acceptance (stdlib-only where a model isn't required — M1 is entirely stdlib)

1. **Green-list partition is keyed + deterministic (M1):** for a fixed `(key, gamma, hash_scheme,
   vocab)` and a given context, `green_list(context)` is reproducible across calls; a **different
   key** (or a different `gamma`, or a different `hash_scheme`) yields a **different** partition
   (both asserted). `left-hash` (default) and `prefix-h` both produce valid `⌊γ·|V|⌋`-sized green
   lists.
2. **z-statistic + p-value numerics (M1):** on a **synthetic green-biased token stream** (a fixture
   where a known fraction of tokens are forced into their position's green list), `z` is large and
   positive and `p` small; on a **fixture drawn independently of the partition**, `z ≈ 0` and `p ≈
   0.5` (both asserted to tolerance). `z = (green_count − γ·T)/sqrt(T·γ·(1−γ))` and
   `green_fraction = green_count/T` are pinned. The math runs with **no model loaded**.
3. **Parameter validation (M1):** `validate_params` **raises `ValueError`** on a missing/empty
   `key`, `gamma ∉ (0,1)`, an empty vocab, an unknown `hash_scheme`, and `T` below the hard minimum
   (each asserted); the CLI surfaces these as `bad_input` non-zero exits.
4. **No-verdict envelope shape (M1, structural):** the emitted `results` dict contains `z`,
   `p_value`, `green_fraction`, `gamma`, `n_scored_tokens`, `band`, `key_id`, `hash_scheme`,
   `assumptions` — and **no** `is_watermarked` / `is_ai` / `is_human` / `verdict` key (asserted over
   the **actual output dict**, not the source). The `band` value is one of the descriptive strings,
   never a class label or a boolean.
5. **Absence-≠-human discipline (M1):** a low-z run produces band `under_powered` (or
   `no_watermark_signal_for_this_key`) and a `render()` whose footer contains the two caveats and
   whose empty/low-z line says **`unknown`, not "no watermark" and not "human"** — asserted that the
   word `human` never appears as an output *value* and that `render()` never emits "unwatermarked."
6. **Reliability band carries the decay + length caveat (M1):** a too-short `T` (or
   `--rewrite-exposure heavy`) forces band `under_powered` with `assumptions.length_floor` /
   `assumptions.rewrite_exposure` / `assumptions.decay_caveat` populated; a clearly green-biased
   long fixture reaches `watermark_consistent` (the top of the two-tier as-built band set — review
   finding 3a). Bands are flagged
   `band_is_provisional: true`.
7. **Tokenizer-mismatch guard (M1):** the real path (`--tokens` + `--vocab`) records
   `assumptions.tokenization = "operator_tokens"`; the `--tokenizer fallback:whitespace` path records
   `"whitespace_fallback"`, emits a stderr under-detection warning, and (asserted) the
   `does_not_license` text names the BPE-mismatch hazard.
8. **Key secrecy (M1):** the secret `key` never appears in `results` or on stdout (only a non-secret
   `key_id` hash); asserted by scanning the serialized envelope + captured stdout for the key value.
9. **Claim license — present + refuses the verdict (M1, structural):** the envelope carries a
   `ClaimLicense` block; `does_not_license` contains (asserted substrings) the **"not AI / not
   authorship"** refusal, the **"absence is not evidence of human authorship"** refusal, the
   **"tests only the supplied key / blind to other & scrubbed & semantic schemes"** refusal, and the
   **whitespace-fallback under-detection** note. No `verdict` / `is_ai` key anywhere in the envelope.
10. **Both goldens + count bumps (surface-addition discipline):** a new `capabilities.d/
    watermark_probe.yaml` fragment (`surface: watermark_probe`; `status: literature_anchored`;
    `compute.tier: core`; `dependencies.python: []`; `use_when` = "you hold the green-list
    key/params for a scheme you suspect"; `do_not_use_when` = "you have no key / want an AI verdict /
    want to read a negative as human / the text may be paraphrase-scrubbed / the scheme is
    semantic/SynthID-class") is added **and** mirrored into `scripts/tests/_golden_capabilities.json`
    (insert the entry; `json.dumps` **no** `sort_keys`); a new `claim_license_surfaces/
    watermark_probe.txt` fragment is added; **and** `scripts/tests/_golden_task_surface_labels.json`
    gains a `watermark_probe` label carrying the absence-≠-human caveat — *"KGW green-list watermark
    probe (Kirchenbauer et al. 2301.10226): z-test for a KNOWN, operator-keyed token-level scheme.
    A positive is watermark-consistent with that scheme, never 'AI'; absence is NOT evidence of human
    authorship (blind to other & semantic & scrubbed watermarks)."* The relevant golden-count
    assertions (`==N`) are bumped. (Per the fleet golden-bump lesson: parallel new-surface PRs
    collide here; this is the full-suite golden test, not the drift/docs gate.)
11. **M2 sweep is additive + caveated (gated, stubbed — no model, no GPU):** with a **stub
    catalog** of several scheme params and the whitespace fallback, the sweep emits one independent
    `WatermarkProbeResult` per scheme (each an M1 result), records `assumptions.n_schemes_tried`,
    adds the **multiple-comparisons** warning to the render footer, and emits **no** cross-scheme
    aggregate / "best match" / boolean (asserted). The M2 entrypoint returns only per-scheme cards.
12. **Surface-addition paper trail + gates green (release discipline):** a `changelog.d/<slug>.md`
    fragment citing **arXiv:2301.10226** (KGW), **arXiv:2306.04634** (reliability bands), and
    **arXiv:2411.13425** (WaterPark blind spots); `check_capabilities_drift`,
    `gen_calibration_readiness`, and `check_docs_freshness` pass before push;
    `references/signals-glossary.md` + `ROADMAP.md` updated at release reconciliation.

## Milestones

1. ⏳ **M1 (model-free, stdlib — the design core + the entire headline capability):** the keyed
   green-list partition (`left-hash` default + `prefix-h`), the KGW z-statistic + p-value, the
   PROVISIONAL detection-power band with the length/decay caveat, the operator-tokenization input +
   labelled whitespace fallback, `validate_params`, the `WatermarkProbeResult` (no-verdict
   envelope, `render()` with the two caveats), the `setec run watermark_probe` CLI, the
   `ClaimLicense` block (absence-≠-human refusals), key secrecy, the structural no-verdict guard,
   and the **both-goldens + new surface label + count bumps** (the surface-addition discipline). No
   SETEC consumer, no model. **Ships as a keyed presence probe for one token-level scheme** — a
   positive is watermark-consistent, a negative is `unknown`, never human.
2. ⏳ **M2 (multi-key sweep convenience; gated by scope/seam, stubbed in CI — no model, no GPU):**
   the operator-catalog batcher running M1 across several `(key, gamma, hash_scheme, vocab)` schemes
   and `prefix-h` windows, each card an independent M1 result, with the `n_schemes_tried` record and
   the multiple-comparisons caveat — strictly additive, no aggregate/best-match verdict, never a
   selector/validator, reusing the M1 surface. **This buys convenience, not power.**

M1 is the stdlib core + the full capability + the posture surface, and is independently shippable
and CI-complete on its own; M2 is a thin convenience batcher behind a scope/seam gate so the
headline z-test stays clean stdlib. Each lands as its own PR against this spec. On merge of M1, add
a ROADMAP entry (watermark probe — the first watermarking axis — Planned → in-progress); flip to
shipped on M1 (M2 is optional polish).

## Calibration posture

Ships **PROVISIONAL / literature_anchored** — a z-statistic + p-value (exact under the stated null)
plus a detection-power band whose cut-points are operator-side and PROVISIONAL, grounded in the
reliability paper's qualitative survival curves (2306.04634), not a fitted SETEC threshold. A
labelled **watermarked-with-this-key / clean** corpus (and paraphrase-scrubbed variants) would
calibrate the band to an empirical detection-power-vs-length curve later → `empirically_oriented`
with a PROVENANCE entry. The default emits **no** decision; the z/p are the load-bearing numbers,
the band is advisory.

## Open questions

1. **Band cut-points (PROVISIONAL).** The `under_powered` / `watermark_consistent` /
   `strongly_watermark_consistent` z-margins and the length floor are a maintainer/calibration call
   (the reliability paper gives qualitative survival, not a universal cut-point). `--gamma` and the
   margins are exposed; the defaults are operator-tunable and flagged PROVISIONAL — not a build
   blocker.
2. **`prefix-h` default window.** v1 ships `left-hash` as default; whether to expose a `prefix-h`
   window-size default (the paper's `h`) in M1 or defer it to the M2 catalog is a scoping call.
   Leaning: `left-hash` only in M1, `prefix-h` windows in the M2 sweep.
3. **Real-tokenizer adapter location.** Whether the optional operator-supplied BPE-tokenizer adapter
   (for operators who *do* hold the model's tokenizer) lives behind the M2 seam or is a documented
   M1 input contract only. Leaning: M1 takes a token-id stream + vocab (no adapter); any
   `transformers`-backed convenience adapter is M2-gated so M1 stays stdlib.
4. **`compose_evidence_pack` wiring.** Whether/when the watermark card joins the standard evidence
   pack alongside the stylometric signals (additive, later, separate change — out of scope here).
