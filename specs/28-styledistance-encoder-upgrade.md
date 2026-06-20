# StyleDistance / Multilingual-UAR — a stronger content-independent style encoder behind the `voice_fingerprint` seam

Builds on `specs/02-voice-fingerprint-embedding.md` (the `authorship_embedding`
surface: a *frozen* style encoder loaded through the injectable `_load_encoder`
seam, windows shared with `semantic_trajectory_audit`, and a **cosine-similarity
distribution** reported with **no threshold and no verdict**) and
`specs/19-crosslingual-voice-distance.md` (the parser-free, language-*agnostic*
`voice_coherence` sibling — char n-grams + punctuation/length/script profile, no
spaCy, no encoder). This spec changes **neither distance machinery**: it is an
**embedding upgrade** that slots a stronger, more content-independent encoder behind
the *existing* `voice_fingerprint._load_encoder` seam, and adds a *parallel,
opt-in* encoder mode to `crosslingual_voice_distance` for the multilingual case.
The cosine/distribution/claim-license plumbing is untouched.

Roots (cite in the eventual PR body **and** the `changelog.d/` fragment, not only
here): **StyleDistance** — synthetic near-paraphrase contrastive training for
*content-independent* style embeddings ([arXiv:2410.12757](https://arxiv.org/abs/2410.12757))
— and **mUAR / Multilingual Universal Authorship Representation** — a multilingual
authorship-representation encoder for crosslingual style
([arXiv:2509.16531](https://arxiv.org/abs/2509.16531)).

- **Status:** Reviewed (GO-WITH-CHANGES); review findings folded into this text —
  see "Review findings folded in (M1)" at the foot of this spec.
- **Tier:** "Capability-shortlist item 8 (IMPROVES) — stronger style encoder." It
  is on `setec-scratch/arxiv-capability-review/{SHORT-LIST,LONG-LIST}.md` mapped to
  `voice_fingerprint` (encoder swap) and `crosslingual_voice_distance`; this is that
  write-up, not a new surface.
- **GPU required:** no for M1 (the seam wiring + config + alias registration is
  stdlib, stub-tested, no weights touched); yes/optional for M2 (the real encoder
  load + GPU/CPU smoke is a model run, `skipif`-gated, never in CI). Same posture as
  spec 02's `_LUAREncoder` (present in code, never executed under the unit suite).

## Goal

`voice_fingerprint` already does the *right structural thing*: it embeds windows
with a frozen encoder and reports a cosine distribution under that model's manifold,
refusing every identity / AI verdict. Its **weakness is the encoder, not the
contract**. The shipped default, LUAR (`rrivera1849/LUAR-MUD`), is Reddit/social
trained, so its "voice" manifold carries **register/topic skew** — the very confound
the claim-license already warns about (two registers can read as "divergent" on
topic grounds rather than authorship). And the framework's only non-English path,
`crosslingual_voice_distance`, is language-*agnostic*: char n-grams that survive a
language switch but carry **no learned style** and (the module says so) **refuse
cross-language comparison entirely**.

Two papers close exactly these two gaps without touching the math:

- **StyleDistance** ([arXiv:2410.12757](https://arxiv.org/abs/2410.12757)) trains a
  style embedding on *synthetic near-paraphrases* that vary style while holding
  content fixed, so its manifold is **more content-independent** than LUAR's — a
  cleaner answer to the topic-leakage caveat `voice_fingerprint` already prints. It
  drops in behind `_load_encoder` as a new alias; the surface contract is unchanged.
- **mUAR** ([arXiv:2509.16531](https://arxiv.org/abs/2509.16531)) is a *multilingual*
  authorship-representation encoder. It is the learned, language-*aware* complement
  to `crosslingual_voice_distance`'s parser-free profile — added as an **opt-in
  encoder mode beside** the stdlib path (default stays the parser-free distance), and
  it is the surface that could honestly relax the same-language constraint *as a
  separately-licensed, separately-calibrated claim* (it is **not** relaxed by
  default; see Non-goals).

This spec adds **(a)** a StyleDistance encoder alias behind the existing
`voice_fingerprint._load_encoder` seam (plus an mUAR alias for the multilingual
authorship case), **(b)** an opt-in `--encoder`/mUAR mode in
`crosslingual_voice_distance` that runs *parallel to*, never replacing, the
parser-free default, and **(c)** the structural posture guards that keep a better
encoder from quietly becoming an identity verdict or leaking into the held-out
validators it could be compared against. No new task surface. No new aggregate. No
new band. Every output is the **same descriptive cosine distribution under model
M's manifold** spec 02 already ships — only M is better.

## Honest framing (limits, surfaced not hidden)

- **A better encoder is still not a verdict.** StyleDistance / mUAR raise how well
  the manifold separates *style* from *content*; they do **not** turn a cosine into
  "same person" / "different author" / "AI". The claim-license's refusals are
  unchanged and **must** be unchanged — a stronger manifold makes the *distribution*
  more trustworthy, not the *call* legitimate. There is no call. (The real refusal
  text is `does_not_license`'s "SAME PERSON" / "DIFFERENT AUTHOR" / "AI-generated or
  human-written" — those exact strings, not a re-coined "AI/human" form; see the
  folded findings.)
- **Content-independence is a claim about training, not a guarantee.** StyleDistance
  is *more* content-controlled than LUAR (synthetic-paraphrase contrastive); it is
  not topic-proof. The topic-leakage caveat stays in the claim-license, reworded
  per-encoder (StyleDistance: "trained to suppress content, not free of it"). The
  spec does **not** let a "content-independent" label retire the confound warning.
- **Cosines are model-bound and cross-model-incomparable.** A LUAR cosine and a
  StyleDistance cosine are **not** the same scale; the existing
  cross-model-incomparability caveat now covers four encoders. The `model_id` is
  already recorded in every envelope and the claim-license — that provenance is what
  makes a later comparison *flag itself* instead of silently mixing manifolds.
- **Multilingual ≠ cross-lingual-by-default.** mUAR *can* represent style across
  languages, but `crosslingual_voice_distance` ships with `--lang` as a **required,
  shared** provenance tag and refuses cross-language comparison. The mUAR mode does
  **not** silently relax that. Relaxing it is a *separate*, calibrated, opt-in claim
  (an explicit flag, its own claim-license language, PROVISIONAL) — see Non-goals; it
  is **not** in this spec's default behavior and **not** in M1.
- **Calibration does not improve for free.** The surface ships **PROVISIONAL** today
  (no operating point); a stronger encoder is **still PROVISIONAL**. A better
  manifold is not a calibrated band. The `calibration_status` string is per-encoder
  but stays "PROVISIONAL — uncalibrated" until a per-encoder impostor-pool study
  exists. No encoder swap promotes `status`.
- **This improves a *selection* surface — that is the real hazard.** In the
  voicewright consumer, `voice_fingerprint` is a **SELECTION_SURFACES** member (the
  ranker reads it); `general_imposters` / `mimicry_cosplay_audit` / `binoculars_audit`
  are **HOLDOUT_SURFACES** (validators never exposed during selection — the
  anti-Goodhart / circularity guard). A stronger style encoder must stay **disjoint**
  from those held-out validators (see the load-bearing question). That disjointness
  is the spec's spine, not a footnote.

## The load-bearing design question (and its answer)

**A better content-independent encoder is exactly what a held-out style validator
would also want — so what stops the upgrade from collapsing the selection /
validation firewall?** `voice_fingerprint` is a *selection* signal in voicewright's
fitness loop; the held-out validators (`general_imposters`, `mimicry_cosplay_audit`,
`binoculars_audit`) are the disjoint check that catches a candidate that *games the
selectors*. If the held-out validator were swapped to (or correlated into) the same
StyleDistance/mUAR manifold the selector now uses, the firewall is a mirror: the
generator optimizes against the selector's manifold and the "independent" validator
nods along. That is the precise circularity `HOLDOUT_SURFACES` exists to prevent.

The answer is that **this spec touches only the producer-side encoder of a surface
that is already declared a selector, and it changes no held-out validator's
backend.** Concretely:

- **The encoder swap is scoped to `voice_fingerprint` and the opt-in
  `crosslingual_voice_distance` mode — both `voice_coherence`/`authorship_embedding`
  *selection*-family surfaces.** It does **not** touch `general_imposters.py`,
  `mimicry_cosplay_audit.py`, or `binoculars_audit.py`. Those keep their own
  backends (GI's unsupervised char-n-gram attribution; cosplay's lexical-vs-syntactic
  check; Binoculars' surprisal). A structural test asserts the new encoder
  module/aliases are **not imported by** any held-out validator script.
- **Import-disjointness is NECESSARY, not SUFFICIENT (folded [P3] finding).** The
  import-disjointness test closes the *shared-manifold (code)* leak: a held-out
  validator cannot pull the selector's encoder module. It does **not** close the
  *correlation* leak — a StyleDistance/mUAR manifold could correlate with a held-out
  validator's signal without sharing an import. The voicewright anti-Goodhart line
  (`setec-voicewright/AGENTS.md §3` — "Anti-Goodhart — protect the held-out audit's
  independence", which forbids *any signal correlated* with the held-out validators)
  is satisfied at the *consumer* drift gate (selection encoder id ≠ any held-out
  backend id), not by this producer-side grep. This spec does **not** bill the grep
  as the whole firewall — it is the M1, code-leak form of the load-bearing answer; the
  correlation form is named here and enforced consumer-side.
- **No new aggregate, no new band, no win/loss proportion.** The upgrade adds an
  *alias*, not a number. The envelope still carries `cosine_distribution` (mean/sd/
  min/p10/p50/p90) and `model_id`; it gains **no** `is_same_author`, **no**
  `style_distance_score`, **no** threshold. There is nothing new for a downstream
  `rank()` to fold in beyond the same descriptive distribution it already reads.
- **`model_id` provenance is the disjointness *evidence*, made checkable.** Because
  every envelope and claim-license records the encoder id, a consumer can assert at
  contract time that its **selection** `voice_fingerprint` encoder and its
  **held-out** validators do not share a manifold id. The cross-model-incomparability
  caveat is the human-readable form of the same guarantee. (The voicewright-side
  enforcement — that `SELECTION_SURFACES`' encoder id ≠ any `HOLDOUT_SURFACES`
  backend id — is a *consumer* contract note, flagged for the consumer's drift gate;
  it is **out of scope** for this producer spec to implement, but the spec names it so
  the seam is honored end to end.)
- **The seam is the firewall's hinge — so the swap is config, not new code paths.**
  StyleDistance/mUAR enter through the *same* `_load_encoder` injection point spec 02
  built precisely so a new manifold is a one-line alias + a lazy encoder class, not a
  re-plumb of the distance or the gate. Keeping the change inside the seam is what
  makes "did the encoder leak into a validator?" a one-line grep, not an audit.

## Design (M1 seam wiring + config = stdlib, stub-testable; M2 = the real model load, gated)

### M1 — encoder-seam wiring + config + posture guards (stdlib, CI-runnable, no weights)

The honest M1 claim is **"the seam now offers StyleDistance and mUAR as selectable
encoders, the config/aliases/claim-license/capabilities all resolve correctly, and
the posture guards hold — without loading a single weight."** It is genuinely
stdlib because `_load_encoder` is already an injectable seam the test suite
monkeypatches, and alias resolution + dependency-gating + claim-license text +
capability-manifest shape are all checkable against a **stub encoder**, exactly as
spec 02's stub-monkeypatch test discipline already is (folded [P3]: the spec-02 test
file's exact count is not load-bearing — it is the *discipline* that matters). M1
does **not** claim the new manifold *works better* — that is an empirical property
only M2's real load can show.

- **`voice_fingerprint.MODEL_ALIASES` gains two aliases** (the only data change to the
  default surface): `"styledistance"` → the StyleDistance weight id, and `"muar"` →
  the mUAR weight id. `DEFAULT_MODEL` is **unchanged** (`"luar"`) so the surface is
  **default-preserving**: no existing invocation changes behavior. (Whether to *flip*
  the default to StyleDistance is an Open Question, gated on M2 calibration — not done
  here.)
- **`_load_encoder` dispatch extended, not rewritten.** The function already branches
  LUAR vs Wegmann on alias/resolved id and gates on `transformers` presence with a
  clean `VoiceFingerprintError` install hint (not a traceback). StyleDistance loads
  through the **same `transformers`/`AutoModel` path** as `_LUAREncoder` (an
  `_StyleDistanceEncoder` lazy class mirroring `_LUAREncoder`: lazy import,
  `trust_remote_code` per the model card, mean-pool-or-pooled-output, L2-normalized
  rows so downstream cosine is a dot product). If StyleDistance ships as a
  sentence-transformers model, it reuses the `_WegmannEncoder` path instead — the
  branch is chosen by the alias, and **whichever path, the encoder class is the only
  new code**; the dispatch, the dependency gate, and the error text are reused. The
  new encoder classes are **present in code but never executed under the unit suite**
  (the spec-02 `_LUAREncoder` discipline — tests monkeypatch `_load_encoder` to a stub).
- **mUAR encoder class** (`_MUAREncoder`) follows the identical lazy/normalized
  pattern. It is the encoder both `voice_fingerprint --model muar` and the
  `crosslingual_voice_distance` opt-in mode resolve to, so there is **one** mUAR load
  path, not two.
- **`--device` already flows end to end** (`main` → `_load_encoder(args.model,
  device=args.device)` → encoder `.to(device)`); no GPU plumbing is added. The
  GPU/model-CPU story is "the existing `--device` seam, now reachable by two more
  encoders." M1 asserts the device argument is threaded (stub records it); M2 is the
  only place a real `.to("cuda")` runs.
- **Claim-license: REFACTOR the static caveat block into a per-encoder branch, same
  refusals (folded [P1]).** Today `_claim_license` takes `model_id` and emits **one
  STATIC `additional_caveats` block** that names *both* LUAR and Wegmann
  unconditionally in *every* envelope (a `--model wegmann` run still prints the LUAR
  register-skew caveat); there is **no** per-encoder branch to "gain a branch" in.
  This spec therefore **REFACTORS** the static `additional_caveats` into a
  `model_id`-conditional structure (LUAR / Wegmann / StyleDistance / mUAR), adding a
  StyleDistance branch ("synthetic-paraphrase contrastive → *more* content-controlled
  than LUAR, **not** topic-proof") and an mUAR branch ("multilingual authorship
  manifold; cosines are within-encoder, and multilingual representation does **not**
  by itself license cross-language comparison"), and updating the
  cross-model-incomparability caveat (today "LUAR and Wegmann") to enumerate all four.
  The `licenses` / `does_not_license` strings are preserved **byte-for-byte** — a test
  asserts the refusal strings ("SAME PERSON" / "DIFFERENT AUTHOR" / "AI-generated or
  human-written") are unchanged across **every** encoder before/after the refactor.
  `calibration_status` stays "PROVISIONAL — uncalibrated" for the new encoders.
- **`crosslingual_voice_distance` opt-in encoder mode (parallel, never default).** A
  new `--encoder muar` flag (default **off** → the existing parser-free,
  zero-dependency `delta`/cosine path is **unchanged**, so spec 19's `dependencies.
  python: []` "core tier" claim still holds for the default invocation). When
  `--encoder muar` is supplied, the module additionally reports an
  **encoder-cosine block** beside the parser-free block (it does not replace `delta`):
  `encoder_id`, an mUAR cosine distribution target-vs-baseline-centroid (the
  `voice_fingerprint` two-corpus computation, reused — *not* reimplemented), and a
  per-encoder claim-license caveat. **The reuse import is LAZY and in-branch (folded
  [P2]).** `crosslingual_voice_distance` imports **nothing** from `voice_fingerprint`
  today; its whole identity (spec 19) is zero-dependency, any-Unicode, import-time
  stdlib. So the import of `voice_fingerprint` (for `run_two_corpus` / `_centroid` /
  `cosine_distribution` **and** the `_MUAREncoder` load) is **lazy and only inside the
  `--encoder muar` branch**, never at the crosslingual module top level. The
  `voice_fingerprint` import chain is itself import-time stdlib (it pulls
  `semantic_trajectory_audit` → `embedding_backend`, whose torch/transformers deps are
  lazy), so the in-branch import does **not** drag torch in eagerly. The `--lang`
  required-shared-provenance contract and the cross-language refusal are **unchanged**
  even in encoder mode (M1 asserts this). Importing `transformers` is **lazy and only
  on `--encoder muar`** so `import`-time and the default path stay stdlib (the spec-19
  invariant); M1 asserts the default path imports neither `transformers` **nor**
  `voice_fingerprint`/`semantic_trajectory_audit`.
- **Capabilities + dependency_check.** `voice_fingerprint.yaml`'s `purpose`/inputs and
  `cost_note` are reworded to name StyleDistance/mUAR as additional supported encoders
  (still `transformers`; mUAR/StyleDistance weight sizes folded into the disk/VRAM
  line). `crosslingual_voice_distance.yaml` gains a `python_optional: [transformers]`
  and an `--encoder` input row, with `compute.tier` **still `core`** for the default
  and a note that `--encoder muar` raises it to the optional/model tier. A
  `dependency_check.py` note covers the StyleDistance/mUAR weight download if distinct
  from the existing surprisal stack.
- **No-new-surface discipline.** This is an **encoder upgrade of two existing
  surfaces**, so it adds **no new `task_surface`** and **no new capabilities.d
  entry**: there is **no `_golden_task_surface_labels.json` change and no
  `test_capabilities_dropin.py` count bump** (the count is derived from the golden
  fragment set, not a `==N` literal — no new id, no count change). It **does** touch
  the two existing `capabilities.d/*.yaml` fragments (deps/inputs/cost), which
  re-blesses their two golden `_golden_capabilities/*.json` fragments **only** — the
  entry count is unchanged and the surface-label golden is unchanged. (If review later
  decides the multilingual encoder cosine warrants its own
  `authorship_embedding`-family id, *that* is the count-bump path — flagged as an Open
  Question, not assumed.)
- **Structural posture guards (the firewall, made checkable):**
  - **Held-out disjointness:** a structural test asserts the new encoder module /
    aliases are **not imported by** `general_imposters.py`, `mimicry_cosplay_audit.py`,
    or `binoculars_audit.py` (the held-out validators must not share the selector's
    manifold). This is the M1 (code-leak) form of the load-bearing answer — NECESSARY,
    not SUFFICIENT (the correlation form is the consumer drift gate's job).
  - **Default-preserving:** a test asserts `DEFAULT_MODEL == "luar"` and that a
    `voice_fingerprint` run with no `--model` produces the **same envelope shape and
    same `model_id`** as before the change (no silent default flip); and that
    `crosslingual_voice_distance` with no `--encoder` imports no `transformers` and
    emits the identical parser-free envelope.
  - **Refusals invariant:** a test enumerates every alias and asserts the
    `does_not_license` refusal set is identical across encoders (no encoder "earns" a
    verdict), against the EXISTING refusal strings.
  - **`import` stays stdlib:** importing either module pulls no model dependency; the
    encoder import (and the crosslingual `voice_fingerprint` reuse import) is lazy in
    both (the spec-02 / spec-19 invariant).

### M2 — the real StyleDistance / mUAR load + GPU/CPU smoke (model seam, `skipif`-gated, never in CI)

- **The gate is the model run, not a checkout.** Like spec 02's `_LUAREncoder`, the
  `_StyleDistanceEncoder` / `_MUAREncoder` classes exist in code but their `.encode`
  is **only** exercised by a maintainer GPU/CPU smoke, `skipif`-gated on the weights /
  `transformers` being present (e.g. an env flag + import guard), **never** run in CI.
  M1's stub suite covers everything the seam can assert without weights.
- **What M2 verifies (empirical, not posture):** the StyleDistance / mUAR weights load
  via the chosen path (`transformers` or sentence-transformers), `.encode` returns
  L2-normalized rows of the expected dimension on both CPU and `--device cuda`, a
  document-vs-itself yields cosine ≈ 1, and a content-matched / style-varied pair
  shows the **content-independence improvement** over LUAR the paper claims (a smoke
  comparison, recorded as a maintainer note — **not** a shipped band or a regression
  gate). For mUAR, a same-language two-corpus run produces a sane distribution; the
  **cross-language** case is recorded only as evidence for the *separate* future
  cross-language-claim decision, never wired into the default.
- **Calibration is explicitly deferred.** M2 does **not** ship an operating point. A
  per-encoder impostor-pool study (the spec-02 calibration path) is the route from
  PROVISIONAL → a calibrated band → a `status` promotion, and it is **future work per
  encoder**. No encoder swap promotes `status` in this spec.
- **License gate before ship (carried from spec 02).** The StyleDistance and mUAR
  **weight-card license tags are confirmed permissive before the M2 PR merges** (the
  spec-02 Wegmann-tag discipline). If a weight tag is non-permissive, that encoder
  ships **omitted** (alias removed) rather than shipped under an unclear license; the
  seam tolerates shipping a subset.

## Considered & rejected (posture)

- **Flipping `DEFAULT_MODEL` to StyleDistance in this spec.** Tempting (it is the more
  content-independent manifold), but the default is what every existing
  `voice_fingerprint` invocation and the voicewright selector silently get. Flipping
  it pre-calibration would (a) change a *selection* signal's behavior under everyone's
  feet and (b) do so on an uncalibrated manifold. Default stays LUAR; the flip is an
  Open Question gated on M2 calibration + the consumer's drift gate, not a default.
- **Replacing the parser-free `crosslingual_voice_distance` path with mUAR.** That path
  is the framework's **zero-dependency, any-Unicode-script** door-opener (spec 19's
  whole point); making it require `transformers` would slam that door. mUAR is an
  **opt-in parallel** block, default off, and the stdlib path keeps `dependencies.
  python: []`.
- **Relaxing the same-language `--lang` constraint because mUAR is multilingual.** The
  cross-language refusal is a *claim-license* commitment, not an encoder limitation;
  mUAR being multilingual is *capability*, not *license*. Cross-language comparison is a
  separate, calibrated, explicitly-flagged, PROVISIONAL claim (Non-goals) — **never**
  the silent default of an encoder swap.
- **Adding a `style_distance_score` / `is_same_author` / any new aggregate or band.**
  The whole posture is the encoder gets better, the *contract* (descriptive
  distribution, no threshold, no verdict) does not. A new scalar would be a verdict in
  disguise and a fresh Goodhart target for the selector. Rejected; the envelope gains
  only `model_id`-tagged distributions it already emits.
- **Using the same StyleDistance/mUAR manifold for a held-out validator (e.g. an
  embedding GI).** This is the firewall collapse the load-bearing question is about: a
  held-out validator on the selector's manifold is not held out. The held-out
  validators keep their own disjoint backends; the structural import-disjointness test
  enforces it (code leak), and the consumer drift gate enforces the correlation leak.
- **Fine-tuning the encoder per-deployment.** Spec 02 wraps *frozen* weights for a
  reason (no per-deployment training, no threshold). StyleDistance/mUAR are wrapped
  frozen too; fine-tuning would reintroduce a trained, gameable, deployment-specific
  manifold.
- **Retiring the topic-leakage caveat because StyleDistance is "content-independent."**
  Content-independence is a training property, not a guarantee; the caveat is reworded
  per-encoder, never removed.

## Non-goals

- **No change to the cosine / distribution / windowing / claim-license *machinery*.**
  Only `MODEL_ALIASES`, `_load_encoder`'s dispatch, the encoder classes, the
  per-encoder caveat text (a refactor of the existing static block), and (for
  crosslingual) an opt-in parallel block. The reused `split_windows`,
  `cosine_distribution`, `_centroid`, `build_output`, and the refusal set are
  untouched.
- **No default change.** `DEFAULT_MODEL` stays `luar`; `crosslingual_voice_distance`'s
  default stays parser-free. Default-preserving is a tested guard, not a hope.
- **No new task surface, no calibrated band, no `status` promotion.** PROVISIONAL
  stays PROVISIONAL per encoder until a future impostor-pool study.
- **No cross-language comparison by default.** Relaxing `--lang` is a separate future
  spec (its own flag, claim-license, and calibration), not this one.
- **No held-out-validator backend change.** `general_imposters` / `mimicry_cosplay_audit`
  / `binoculars_audit` keep their backends; the encoder upgrade is disjoint from them
  by construction and by test.
- **No consumer (voicewright) code in this spec.** The consumer-side assertion that the
  selection encoder id ≠ the held-out validator backend id is *named* as a contract the
  consumer's drift gate should honor, but implementing it is the consumer's PR, not this
  producer spec's.

## Anti-Goodhart / posture guardrails (must hold)

The `voice_fingerprint` / `crosslingual` cosine distribution stays a **descriptive,
model-bound distribution** under the named encoder's manifold — **never** a
`same_author` / `different_author` / `AI` verdict, **never** a new scalar/band/
win-loss aggregate, and a stronger manifold does **not** earn a call (the refusal set
is byte-identical across all encoders, tested against the EXISTING "SAME PERSON" /
"DIFFERENT AUTHOR" / "AI-generated or human-written" strings) · the upgrade is
**scoped to a selection-family surface and is structurally disjoint from the held-out
validators** (`general_imposters` / `mimicry_cosplay_audit` / `binoculars_audit`):
the new encoder module/aliases are **not imported by** any held-out validator
(tested), so the selection/validation firewall the consumer's `HOLDOUT_SURFACES`
encodes is not mirrored into a single manifold — import-disjointness is NECESSARY,
not SUFFICIENT, and the correlation form is the consumer drift gate's enforcement ·
**`model_id` provenance is recorded in every envelope and claim-license**, making
"did the selector's manifold leak into a validator?" a checkable contract (the
consumer drift gate's job, named here) and keeping cross-encoder cosines flagged
non-comparable rather than silently mixed · **default-preserving:** `DEFAULT_MODEL ==
"luar"`, the crosslingual default stays parser-free/`dependencies.python: []`, and no
encoder swap silently changes an existing run or promotes `status` (all tested) · the
topic-leakage / content-control caveat is **reworded per encoder, never retired** —
"content-independent" is a training claim, not a topic-proof guarantee · the real
StyleDistance/mUAR load is a **lazy, `skipif`-gated M2 model seam** (the spec-02
`_LUAREncoder` discipline): the encoder classes are present in code but never executed
under the unit suite, `import` stays stdlib, and weight-card license tags are confirmed
permissive before the M2 PR merges (non-permissive ⇒ that encoder ships omitted, not
under an unclear license) · ships **PROVISIONAL** — a better encoder is not a
calibrated band.

## Acceptance (stdlib-only where a model isn't required)

1. **Alias registration + default-preserving (M1):** `voice_fingerprint.MODEL_ALIASES`
   resolves `"styledistance"` and `"muar"` to their weight ids; `DEFAULT_MODEL ==
   "luar"` (asserted); a `voice_fingerprint` run with no `--model` (stub encoder)
   produces the **same envelope shape and same `model_id`** as the pre-change default
   (no silent default flip).
2. **Seam dispatch via stub, no weights (M1):** with `_load_encoder` monkeypatched to a
   stub (the spec-02 discipline), `--model styledistance` and `--model muar` each run
   `single` / `two_corpus` / `n_way` end to end and emit the schema_version 1.0
   envelope with `cosine_distribution` keys (mean/sd/min/p10/p50/p90) and the correct
   `model_id`. **No real weights are loaded**; `import voice_fingerprint` pulls no model
   dependency.
3. **Dependency gate is clean for new encoders (M1):** with `transformers` absent,
   `--model styledistance` (and `--model muar`) raise `VoiceFingerprintError` carrying
   the `dependency_check`-style install hint (not a traceback), via the existing gate.
4. **Refusals invariant across encoders (M1):** for **every** alias (`luar`, `wegmann`,
   `styledistance`, `muar`) the claim-license `does_not_license` contains the unchanged
   refusals — the literal substrings "SAME PERSON", "DIFFERENT AUTHOR", and
   "AI-generated or human-written" (NOT a re-coined "AI/human" form) — and **no** new
   license/verdict string; `calibration_status` for the new encoders is "PROVISIONAL —
   uncalibrated" (asserted).
5. **Per-encoder caveat text (M1):** the StyleDistance claim-license names the
   synthetic-paraphrase / "more content-controlled, not topic-proof" caveat; the mUAR
   one names "multilingual representation does not license cross-language comparison";
   the cross-model-incomparability caveat is present and enumerates all four encoders.
6. **`--device` threaded (M1):** the stub records the `device` argument; a
   `voice_fingerprint --model styledistance --device cuda:0` invocation threads
   `device` to `_load_encoder` and the encoder (asserted against the stub) **without**
   loading weights.
7. **Crosslingual default unchanged (M1):** `crosslingual_voice_distance` with **no**
   `--encoder` imports neither `transformers` NOR `voice_fingerprint` /
   `semantic_trajectory_audit`, emits the byte-identical parser-free envelope (`delta`
   / `cosine_distance` / profiles), and keeps `dependencies.python: []` for the default
   invocation (asserted: source has no top-level `transformers` or `voice_fingerprint`
   import).
8. **Crosslingual opt-in encoder mode (M1, stub):** with `--encoder muar` and a stub
   mUAR encoder, the envelope additionally carries an encoder-cosine block
   (`encoder_id` + cosine distribution target-vs-baseline-centroid) **beside** the
   parser-free block (not replacing `delta`); the `--lang` required-shared-provenance
   contract and the **cross-language refusal are unchanged** even in encoder mode
   (asserted); the `transformers` and `voice_fingerprint` imports are lazy / only on
   `--encoder muar`.
9. **Held-out disjointness (structural, M1):** a structural test asserts the new
   encoder module / aliases are **not imported by** `general_imposters.py`,
   `mimicry_cosplay_audit.py`, or `binoculars_audit.py` (the selector's manifold does
   not leak into a held-out validator). NECESSARY, not SUFFICIENT (correlation is the
   consumer drift gate's enforcement).
10. **Capabilities regen, no count change (M1):** the two edited `capabilities.d/*.yaml`
    fragments (deps/inputs/cost) re-bless their two `_golden_capabilities/*.json`
    fragments **only**; the entry **count is unchanged** (`test_capabilities_dropin.py`
    derives the count from the fragment set — no `==N` literal to bump) and
    `_golden_task_surface_labels.json` is **unchanged** (no new surface). The drift /
    docs-freshness gate passes with a `changelog.d/` fragment citing both arXiv ids.
11. **Real encoder load (M2, `skipif`-gated, never CI):** with the weights /
    `transformers` present, `_StyleDistanceEncoder` and `_MUAREncoder` load via the
    chosen path, `.encode` returns L2-normalized rows of the expected dimension on CPU
    and on `--device cuda`, and a document-vs-itself yields cosine ≈ 1. Skipped by
    default; a maintainer smoke. Records the content-independence-vs-LUAR observation as
    a **note**, not a shipped band or a regression gate.
12. **License-tag gate (M2, pre-merge):** the StyleDistance and mUAR weight-card license
    tags are confirmed permissive before the M2 PR merges; a non-permissive tag ⇒ that
    encoder's alias is **omitted** (the surface ships the permissively-licensed subset),
    never shipped under an unclear license.

## Milestones

1. ⏳ **M1 (seam wiring + config + posture guards — stdlib, CI-runnable, no weights):**
   the `MODEL_ALIASES` `styledistance`/`muar` entries + the extended `_load_encoder`
   dispatch + the lazy `_StyleDistanceEncoder` / `_MUAREncoder` classes (present, not
   executed) + the per-encoder claim-license REFACTOR (refusals byte-identical) + the
   opt-in `crosslingual_voice_distance --encoder muar` parallel block with the LAZY
   in-branch `voice_fingerprint` reuse (default parser-free unchanged) + the
   capabilities/dependency_check rewording (no new id, no count bump, no surface-label
   golden change) + the structural guards (held-out import disjointness,
   default-preserving, refusals-invariant, lazy/stdlib import). **Ships as "the seam now
   offers StyleDistance/mUAR and the posture holds"** — *not* a claim the new manifold
   measures better (that is M2's to show). One PR against this spec; the `changelog.d/`
   fragment cites both arXiv ids.
2. ⏳ **M2 (the real StyleDistance / mUAR load + GPU/CPU smoke — model seam, `skipif`,
   never CI):** the encoder classes' `.encode` exercised by a maintainer smoke (CPU +
   `--device cuda`), dimension / cosine-≈-1 / content-independence-vs-LUAR observations
   recorded as notes, the same-language mUAR distribution sanity-checked, the
   weight-card license tags confirmed permissive (non-permissive ⇒ omit), and the
   calibration path named as future per-encoder work. **This is the milestone that
   shows the manifold is actually better** — but it ships **PROVISIONAL** and promotes
   no `status`. Its own PR against this spec.

M1 is the seam/config/posture surface and is independently valuable as "a stronger
encoder is now *selectable and posture-safe* behind the existing seam"; M2 is the
model seam that actually loads StyleDistance/mUAR and records — as maintainer notes,
not shipped bands — that the manifold separates style from content better than LUAR.
Each lands as its own PR. On M1 merge, reword the `voice_fingerprint` /
`crosslingual_voice_distance` ROADMAP/capability notes (encoder upgrade, Planned →
in-progress); flip to shipped on M1+M2 with the license-tag gate cleared.

## Open questions

- **Flip `DEFAULT_MODEL` to StyleDistance?** Gated on M2 calibration evidence **and**
  the voicewright consumer's drift gate accepting a selection-signal manifold change.
  Default stays LUAR until then.
- **Does the multilingual mUAR cosine warrant its own `authorship_embedding`-family
  capability id** (rather than living as a `crosslingual_voice_distance` opt-in block)?
  If yes, *that* is the count-bump + `_golden_task_surface_labels.json` path — flagged,
  not assumed.
- **StyleDistance load path:** `transformers`/`AutoModel` (LUAR path) vs
  sentence-transformers (Wegmann path) — pin once the weight card is read; the alias
  picks the branch either way, so it is a one-line decision, not a redesign.
- **A future, separately-licensed cross-language claim** (relaxing `--lang` for mUAR):
  its own spec, flag, claim-license, and calibration — explicitly out of this one.

## Review findings folded in (M1)

This spec was adversarially reviewed (verdict GO-WITH-CHANGES, posture clean,
M1-stdlib-buildable). Every finding is folded into the text above; recorded here so
the build honors them and a reader can verify each against the real module source:

- **[P1] The refusal set does NOT contain a literal "AI/human" substring.** The real
  `voice_fingerprint._claim_license` `does_not_license` says "That these passages are
  by the SAME PERSON. That they are by a DIFFERENT AUTHOR. That the text is
  AI-generated or human-written." The refusals-invariant test asserts the EXISTING
  strings ("SAME PERSON" / "DIFFERENT AUTHOR" / "AI-generated or human-written") are
  unchanged across encoders — it must **never** introduce "AI/human" as a new
  canonical form, nor edit the load-bearing refusal text to match a mis-cited acceptance
  criterion. (Folded into Honest framing, the claim-license bullet, the guardrail block,
  and acceptance #4.)
- **[P1] `_claim_license` is currently STATIC — the change is a REFACTOR, not an
  additive branch.** Today `additional_caveats` names both LUAR and Wegmann
  unconditionally with no `model_id` conditional. The M1 change REFACTORS that static
  block into a per-encoder branch (LUAR / Wegmann / StyleDistance / mUAR) and updates
  the cross-model-incomparability caveat from "LUAR and Wegmann" to all four — while
  preserving `licenses` and `does_not_license` byte-for-byte (tested). (Folded into the
  claim-license bullet and acceptance #5.)
- **[P2] The crosslingual reuse of `voice_fingerprint` must be a LAZY in-branch
  import.** `crosslingual_voice_distance` imports nothing from `voice_fingerprint`
  today and is import-time stdlib by design (spec 19). The `--encoder muar` block's
  import of `voice_fingerprint` (`run_two_corpus` / `_centroid` / `cosine_distribution`
  + `_MUAREncoder`) is lazy and only inside the branch; the chain is verified
  import-time stdlib (`semantic_trajectory_audit` → `embedding_backend`, torch/
  transformers lazy). Acceptance #7 asserts the default path imports neither
  `transformers` nor `voice_fingerprint`/`semantic_trajectory_audit`. (Folded into the
  crosslingual bullet and acceptance #7/#8.)
- **[P3] Import-disjointness is NECESSARY, not SUFFICIENT.** The grep closes the
  shared-code (manifold) leak but not the *correlation* leak that
  `setec-voicewright/AGENTS.md §3` ("Anti-Goodhart — protect the held-out audit's
  independence") forbids; the correlation form is the consumer drift gate's enforcement
  (selection encoder id ≠ any held-out backend id), named here. (Folded into the
  load-bearing answer, the guardrail block, and acceptance #9.)
- **[P3] Two stale counts/pointers corrected.** "spec 02's eight tests" is replaced by
  "the spec-02 stub-monkeypatch test discipline" (the exact count is not load-bearing);
  the GRPO/anti-Goodhart grounding is re-pointed from a non-existent
  `setec-voicewright/specs/26-grpo-voice-mimicry-training.md` to
  `setec-voicewright/AGENTS.md §3`, where the corpus-grounded-in / SETEC-correlated-out
  line actually lives.
