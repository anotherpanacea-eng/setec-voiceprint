# 37-register-classifier-repair

> Repair the heuristic register classifier so its taxonomy, scorer coverage, and tie behavior
> are coherent with the manifest schema it exists to guard: **`manifest_validator.ALLOWED_REGISTER`
> becomes the canonical register vocabulary**, the classifier emits a small set of **register
> FAMILIES** (one honest scorer per family, no alias collisions), and a **documented, total,
> many-to-one mapping** connects canonical slugs to families — so `register_match` stops
> reporting taxonomy misalignment as register mismatch. Posture unchanged: heuristic,
> not-labeled-corpus-validated, no-verdict — the output is *a prompt to ask register-match
> questions*, never a measured distribution.

- **Status:** H1-only resolution draft — the merged spec was reviewed on immutable head
  `f864b91410b8f502c78f307f660b5db32442109b` and returned **NEEDS REWORK**
  (two build-blocking P1s). This revision freezes those decisions, restores the
  classifier-only H1 boundary, and remains build-blocked until an independent
  spec re-review clears it.
- **Tier:** near-term (pure stdlib repair of a shipped module; CI-runnable)
- **GPU required:** no
- **Upstream / prior art:** none external — this is an internal-coherence repair of
  `plugins/setec-voiceprint/scripts/register_classifier.py` (shipped Release 1, Phase-1
  trustworthiness layer) against `plugins/setec-voiceprint/scripts/manifest_validator.py`
  (`ALLOWED_REGISTER`) and its sole runtime consumer
  `plugins/setec-voiceprint/scripts/voice_distance.py`.
- **License decision:** N/A — no weights, no external method.

## Motivation

`register_classifier.py`'s entire stated value is **honest claim-licensing**: "when target and
baseline registers diverge, the report should say so explicitly rather than silently produce
numbers as if the comparison were clean" (module docstring). The shipped module cannot deliver
that, because its own vocabulary has drifted from the schema vocabulary manifests actually
carry, its scorer table does not cover its own taxonomy, and its tie behavior silently
privileges first-inserted aliases. The result is worse than no guardrail: for baselines
declared under the schema's most common register values, the guard is **structurally guaranteed**
to report `weak`/`mismatch` — a false alarm the operator learns to ignore.

This repair is **H1**, the prerequisite for the separately specified **H2
register-composition sweep** in `specs/73-register-composition-sweep.md`. H1 ships no corpus
runner. H2 may not build until this repair is independently reviewed, landed, and re-verified
at its immutable implementation head.

**Orthogonality:** no new signal axis. This is a correctness/coherence repair of an existing
module plus its integration seam in `voice_distance.py`.

## Defect inventory (each verified against the code at head; file:line anchors)

### D1 — Scorer coverage gap (verified; counts exact)

`KNOWN_REGISTERS` (`register_classifier.py:59`) enumerates **18** slugs (17 registers + the
`unknown` sentinel). `_SCORERS` (`register_classifier.py:334`) maps **14** keys. The four
uncovered entries: **`policy_advocacy`**, **`report_prose`**, **`email`** (real registers with
no scorer — they can never be `primary`, never appear in `scores`, and are silently absent
from every output), plus `unknown` (sentinel; unscored **by design** — it is the refusal
value, not a scorable register). Nothing in the output distinguishes "scored low" from "never
scored": the taxonomy advertises 17 registers, the classifier can only ever discuss 14.

### D2 — Alias-collision bias (verified; "~8" is exactly 8)

The 14 `_SCORERS` keys share only **8 distinct scorer functions**. Alias groups:

| scorer function | registers sharing it |
|---|---|
| `_score_legal_or_policy_memo` | `legal_memo`, `policy_memo` |
| `_score_academic` | `academic_philosophy`, `academic_general` |
| `_score_literary_fiction` | `literary_fiction`, `commercial_fiction`, `literary_horror` |
| `_score_blog_or_personal_essay` | `blog_essay`, `personal_essay`, `newsletter` |
| `_score_testimony_policy` / `_score_journalism` / `_score_marketing` / `_score_social_thread` | one register each |

Aliases in a group receive **identical** scores by construction. `classify_register` ranks with
`sorted(scores.items(), key=lambda kv: -kv[1])` (`register_classifier.py:395`) — Python's sort
is stable, so equal-scored aliases keep `_SCORERS` insertion order and **the first-inserted
alias always wins**. Absent a hint, exactly **8 of 17** registers are reachable as `primary`:
`legal_memo`, `testimony_policy`, `academic_philosophy`, `journalism`, `literary_fiction`,
`blog_essay`, `marketing`, `social_thread`. `policy_memo`, `academic_general`,
`commercial_fiction`, `literary_horror`, `personal_essay`, `newsletter` are *unreachable as
primary* (they can only surface in `secondary`, where the same tie puts them every time their
group scores at all). This is not a tie-break policy; it is an undocumented accident of dict
ordering wearing the costume of a classification.

### D3 — Taxonomy divergence (verified; one count corrected)

`manifest_validator.ALLOWED_REGISTER` (`manifest_validator.py:73`) — the schema authority the
validator warns against (`manifest_validator.py:327`) and the vocabulary **voiceprint
manifests** declare — holds **15** slugs on the pinned build base:

```
literary_fiction, blog_essay, academic_philosophy, testimony_policy, personal,
policy_advocacy, literary_horror, policy_brief, scholarly_article, legal_brief,
grant_proposal, expert_affidavit, regulatory_comment, professional_letter, teaching
```

The former land-after-#343 dependency is satisfied at pinned base
`80f393977c7639c546bf135ee322d064a542d779`; the build still re-checks exact set equality
against its own head rather than trusting this transcription.

Overlap with `KNOWN_REGISTERS` is exactly **6**: `blog_essay`, `literary_fiction`,
`literary_horror`, `academic_philosophy`, `policy_advocacy`, `testimony_policy`. And the
divergence compounds with D1/D2 at the integration seam:

- Of the 6 overlapping slugs, `policy_advocacy` has **no scorer** (D1) and `literary_horror` is
  **alias-shadowed** (D2) — so only **4 of the 15** schema slugs (`blog_essay`,
  `literary_fiction`, `academic_philosophy`, `testimony_policy`) can *ever* be produced as
  `primary` and therefore ever confirm a baseline's declared register.
- `personal` — a plurality declared value in real manifests (already stated publicly in spec
  36) — is absent from `KNOWN_REGISTERS` entirely. `voice_distance._build_register_match`
  (`voice_distance.py:599`) compares the classifier's predicted `primary` against the
  manifest-declared baseline registers (`_baseline_registers`, `voice_distance.py:577`), so a
  baseline tagged `personal` is **structurally guaranteed** `weak`/`mismatch` regardless of the
  prose. The guardrail cries wolf on the corpus's most common register.

### D4 — Silent hint no-op (found during verification; not in the original report)

`voice_distance.py:852` passes `hint=args.register`, and `--register` is documented as a
**manifest** filter value (`voice_distance.py:635`). But `classify_register` applies the hint
only `if hint and hint in scores` (`register_classifier.py:392`) — and `scores` is keyed by
`_SCORERS`, so the hint silently no-ops for **10 of the 15** schema slugs, including `personal`
and `policy_advocacy`. The documented "nudge toward the declared register" works for exactly 5
manifest values (`blog_essay`, `literary_fiction`, `literary_horror`, `academic_philosophy`,
`testimony_policy`) and is silently ignored otherwise — no warning, no record.

### D5 — Dead claim-license read-site (verified during spec-review fold)

`register_match`'s output is consumed at **two** sites in `voice_distance.py` with
**different key expectations**: the report render reads `match_block.get("strength")`
(`voice_distance.py:508`), but the claim-license `comparison_set` reads
`register_match.get("match", {}).get("verdict")` (`voice_distance.py:938`) — a key
`register_match()` has **never emitted** (it emits `strength`). So on live runs at head,
`comparison_set.register_match` is **always `None`**: the claim-license block silently drops
the register-match signal it claims to carry. (The vendored voicewright contract fixture
shows a `register_match.match.verdict = "match"` shape — a stale/hand-built shape that live
emission never produces, confirming the two sites genuinely diverged. See "Consumer-visible
change".)

### D6 — Taxonomy migration marker is dropped by the sole consumer

The merged draft adds `taxonomy` to `classify_register`, but `voice_distance.py:858-865`
constructs a new `target_classification` object containing only `primary`, `confidence`, and
`secondary`. The marker therefore cannot reach the normalized `voice_distance` result or its
vendored consumer fixtures. A marker that exists only in a library return that the sole runtime
consumer immediately projects away is not a migration contract. This revision expands the
allowed `voice_distance.py` scope: every family-valued forwarded block is versioned, including
the `unavailable` match path.

## The design call — which taxonomy is canonical, and how the two reconcile

**Canonical vocabulary: `manifest_validator.ALLOWED_REGISTER`.** It is the schema authority
**voiceprint manifests** validate against; it is the vocabulary every declared register in
those manifests carries; it is the vocabulary `voice_distance`'s baseline side speaks
unconditionally (`_baseline_registers` reads manifest metadata) and the vocabulary
`--register` filters and hints in. The classifier's `KNOWN_REGISTERS` has no corpus behind
it — its docstring claim that "the manifest's `register` field uses these slugs" is simply
false at head.

**Scope boundary:** "canonical" here means canonical **for voiceprint's manifest schema and
its register-match seam**. The setec-voicewright consumer runs a separate register regime
(free-form front-matter/RAG registers plus its own `FICTION_REGISTERS`) that is **not**
governed by `CANONICAL_REGISTER_TO_FAMILY` and is out of scope for this repair — and must not
be assumed in-scope by the future voiceprint-manifest composition sweep either.

**But the classifier must NOT align 1:1 to the canonical set.** The 15 canonical slugs are
fine-grained *document types* (`policy_brief` vs `regulatory_comment` vs `legal_brief` vs
`grant_proposal`…) that surface-regex heuristics **cannot honestly discriminate** — every one
of those four is statutory-citation-heavy formal prose to the feature extractors in this
module. Thirteen "distinct" scorers would be eight real ones wearing thirteen names: exactly
the alias-collapse defect (D2) rebuilt in a new vocabulary, plus a false precision the
no-verdict posture forbids.

**Rejected: a third reconciled vocabulary for both sides.** Changing `ALLOWED_REGISTER` would
re-tag every existing manifest across corpora and machines, churn the schema that acquisition
scripts and the validator ratchets already emit, and buy nothing the mapping below doesn't —
the schema vocabulary is load-bearing data, not a free variable.

**Adopted: a documented, total, many-to-one mapping onto register FAMILIES.** The honest
resolution of this classifier *is* its 8 scorer functions — so make that explicit:

1. The classifier emits **register families** — one per existing scorer function, 1:1, no
   aliases. These consumer-visible slugs are frozen for v2:
   `formal_legal_policy` (← `_score_legal_or_policy_memo`), `formal_first_person`
   (← `_score_testimony_policy`), `academic` (← `_score_academic`), `journalism`
   (← `_score_journalism`), `narrative_fiction` (← `_score_literary_fiction`),
   `first_person_essay` (← `_score_blog_or_personal_essay`), `promotional`
   (← `_score_marketing`), `short_social` (← `_score_social_thread`), plus the `unknown`
   sentinel (refusal only; never scored).
2. A module-level exported table **`CANONICAL_REGISTER_TO_FAMILY`** maps **every**
   `ALLOWED_REGISTER` slug to exactly one frozen family (assignments below). Totality is
   **mechanically enforced**: a test imports both
   modules and asserts the mapping's domain `== manifest_validator.ALLOWED_REGISTER` — so any
   future schema addition fails CI loudly until mapped. This is a structural gate, not prose
   (per the mechanical-not-rhetorical guard standard).
3. **One shared resolver, family-first (the P1 fix).** All register-valued inputs — the
   `classify_register` hint, the `register_match` target, and every baseline declaration —
   resolve through a **single** helper. A separate, exported
   `LEGACY_REGISTER_TO_FAMILY` table preserves the old classifier-only input spellings for
   one migration cycle; it is not part of the canonical manifest domain and its values are
   never emitted:

   ```
   resolve_family(value) ->
       value                                if value in REGISTER_FAMILIES   (identity; incl. "unknown")
       CANONICAL_REGISTER_TO_FAMILY[value]  elif value in the mapping        (canonical slug)
       LEGACY_REGISTER_TO_FAMILY[value]     elif value in the legacy map     (deprecated input)
       "unknown"                            otherwise                        (caller decides how loudly)
   ```

   Family-first identity matters because the classifier's own `primary` is **already a family
   slug** — `voice_distance` feeds `classify_register(...)["primary"]` straight into
   `register_match` (`voice_distance.py:855`), so the target side arrives family-vocabulary
   while the baseline side arrives canonical-vocabulary. A mapping-only lookup would send
   every already-family target to `unknown` (the exact self-contradiction the spec-review P1
   caught). One resolver, three call-sites; **no second resolution path may be introduced**.
   Note the domain-equality gate in (2) constrains `CANONICAL_REGISTER_TO_FAMILY` alone —
   family names are deliberately NOT in that table's domain; the identity branch of the
   resolver handles them. The three resolver domains are pairwise disjoint. The legacy map
   contains exactly `personal_essay`, `commercial_fiction`, `academic_general`,
   `legal_memo`, `policy_memo`, `newsletter`, `marketing`, `report_prose`,
   `social_thread`, and `email`, mapped respectively to the closest frozen family. Existing
   old labels that are also canonical (`blog_essay`, `literary_fiction`,
   `literary_horror`, `academic_philosophy`, `policy_advocacy`, `testimony_policy`) resolve
   through the canonical table, while `journalism` is already a family identity.
4. `register_match` compares **in family space**: target and baseline declarations both pass
   through `resolve_family` (unresolvable declared values count as `unknown`, matching the
   validator's warn-don't-error extensibility). `personal` maps to `first_person_essay`, so a
   personal-essay baseline is no longer **structurally guaranteed** `weak`/`mismatch` — the
   verdict becomes a function of the prose again (no accuracy claim; the guarantee removal is
   the repair).
5. **Unscored → cannot be primary, and honestly so.** With families 1:1 to scorers, every
   emittable family has a real scorer by construction (enforced:
   `len(set(_SCORERS.values())) == len(_SCORERS)` and `set(_SCORERS) ==` family set minus
   `unknown`). No canonical slug is silently unscorable — every slug maps to a scored family —
   and no family exists without a scorer. `unknown` remains reachable only via the existing
   refusal paths (short text; all scores `< 0.30`), never via a scorer.
6. **Alias-tie bias removed; genuine top ties refuse.** With no two families sharing a scorer,
   the equal-by-construction alias class disappears. A genuine exact tie for the highest
   rounded score at or above `0.30` MUST NOT be broken by insertion order: `primary` becomes
   `unknown`, `confidence` retains the tied score, every tied family appears in
   `secondary` (followed by any additional family in the existing `<0.10` band), and the
   single existing `warning` field names the tied families. If an unresolved hint warning
   also exists, the warning string concatenates the hint warning first and the tie warning
   second with `"; "`. Near-ties that are not exact retain the existing secondary-band
   semantics. This freezes deterministic behavior without turning dict order into a
   classification claim.
7. **Family collapse is disclosed, not hidden (the firewall seam).** A family-space `strong`
   between, say, a `grant_proposal` target declaration and a `legal_brief`-dominated baseline
   is **document-type-blind within `formal_legal_policy`** — and that must reach the operator,
   not just the JSON. Chosen mechanism: the disclosure lives in **`rationale`** (see Design
   §4a), because the render at `voice_distance.py:508-517` already prints `rationale` for
   **every** strength (the ⚠️ marker is weak/mismatch-only, but the `elif strength:` branch
   renders `strength` + `rationale` for strong/moderate too — the caveat is *not* dropped from
   the render path, it just has to be *in* the rationale). The ⚠️ severity tier stays
   weak/mismatch-only: a strong family match is a scope disclosure, not an alarm condition.

Frozen `CANONICAL_REGISTER_TO_FAMILY` for the 15 slugs on pinned base
`80f393977c7639c546bf135ee322d064a542d779` (the builder re-verifies exact domain equality
against its own head):

| canonical slug | family | note |
|---|---|---|
| `literary_fiction` | `narrative_fiction` | |
| `literary_horror` | `narrative_fiction` | |
| `blog_essay` | `first_person_essay` | |
| `personal` | `first_person_essay` | fixes the D3 structural mismatch |
| `academic_philosophy` | `academic` | |
| `scholarly_article` | `academic` | |
| `testimony_policy` | `formal_first_person` | the existing scorer combines formal address, first person, and statutory cues |
| `expert_affidavit` | `formal_first_person` | sworn first-person + statutory prose |
| `policy_brief` | `formal_legal_policy` | |
| `legal_brief` | `formal_legal_policy` | |
| `regulatory_comment` | `formal_legal_policy` | |
| `grant_proposal` | `formal_legal_policy` | closest existing scorer; family match remains document-type-blind |
| `policy_advocacy` | `formal_legal_policy` | closest existing scorer; advocacy subgenres are disclosed as collapsed |
| `professional_letter` | `formal_first_person` | `_FORMAL_ADDRESS` explicitly scores salutations; unlike personal essays, letters are addressed |
| `teaching` | `academic` | closest existing scorer; no claim that pedagogy and scholarship are the same document type |

Families with no canonical slug mapping to them (`journalism`, `promotional`, `short_social`)
**remain emittable**: the classifier may honestly detect prose kinds the corpus schema doesn't
declare, and `register_match` will then honestly report the divergence. Old `KNOWN_REGISTERS`
slugs that were classifier-only or aliases no longer appear in outputs. They remain accepted
only through `LEGACY_REGISTER_TO_FAMILY` during the v2 migration window; v3 may remove that
compatibility table. `report_prose` maps to `journalism`, `email` maps to
`formal_first_person`, and the other assignments follow their former shared scorer. This is
input compatibility, not a claim that those old labels were an honest taxonomy.

Frozen `LEGACY_REGISTER_TO_FAMILY`:

| deprecated input slug | family |
|---|---|
| `personal_essay` | `first_person_essay` |
| `commercial_fiction` | `narrative_fiction` |
| `academic_general` | `academic` |
| `legal_memo` | `formal_legal_policy` |
| `policy_memo` | `formal_legal_policy` |
| `newsletter` | `first_person_essay` |
| `marketing` | `promotional` |
| `report_prose` | `journalism` |
| `social_thread` | `short_social` |
| `email` | `formal_first_person` |

### Taxonomy ownership and versioning

- `manifest_validator.ALLOWED_REGISTER` owns the canonical **document-type** vocabulary.
  This repair does not derive, mutate, or broaden it.
- `register_classifier.py` owns the **family** vocabulary, both mapping tables, and
  `REGISTER_TAXONOMY = "register_families/v2"`.
- The canonical mapping is a reviewed literal, not runtime-derived inference. Tests import
  `manifest_validator` and enforce exact domain equality; the runtime module does not import
  the validator.
- Renaming/adding/removing a family or reassigning any already-canonical slug is a
  consumer-visible semantic change and requires a new taxonomy marker, changelog entry,
  goldens, and downstream fixture/pin migration. Adding a newly introduced canonical slug to
  an existing family may retain v2 because no prior v2 meaning exists for that slug, but it
  still requires the equality gate, changelog, and consumer-fixture audit.

## Design (the repair, concretely)

Implementation scope is `register_classifier.py`, the existing `voice_distance.py` integration,
and their tests/registration artifacts. The prior "D5 one-line only" constraint is withdrawn
because it made D6 impossible to satisfy. Corpus scanning, checkpointing, and sweep/report
publication belong exclusively to Spec 73.

1. **Taxonomy.** Replace the 18-slug `KNOWN_REGISTERS` tuple with the **9-entry family tuple**
   (8 families + `unknown`). Keep the name `KNOWN_REGISTERS` exported with its existing
   semantics ("the set `classify_register` can return as `primary`"); add `REGISTER_FAMILIES`
   as an explicit alias and export `REGISTER_TAXONOMY`,
   `CANONICAL_REGISTER_TO_FAMILY`, `LEGACY_REGISTER_TO_FAMILY`, and `resolve_family`.
   Correct the docstring's false schema claim; state the family posture and point at
   `ALLOWED_REGISTER` as canonical **for voiceprint manifests**.
2. **Scorers.** Re-key `_SCORERS` by family — the 8 existing scorer functions, unchanged
   arithmetic, one key each. **The re-key is provably mechanical:** every scorer's signature
   is `(f: dict[str, float]) -> float` — it receives only the feature vector, never the slug
   it is registered under (verified at head; no scorer branches on its key). The builder must
   re-confirm this before treating the re-key as a rename, and a test pins it (Test 4).
   No new heuristics in this spec (scorer *quality* is out of scope; scorer *coverage and
   identity* are the repair).
3. **`classify_register`.** Signature unchanged (`text, *, hint=None, min_words=100`). Return
   dict keeps every existing key (`primary`, `confidence`, `secondary`, `scores`, `evidence`,
   `warning`) with `primary`/`secondary`/`scores` now in family vocabulary. Additions:
   - `taxonomy: REGISTER_TAXONOMY` on **every return**, including short/all-weak/tied
     refusals — a version marker so any consumer can detect the vocabulary change
     mechanically. This marker is the **migration trigger** for downstream fixture/golden
     re-syncs (see Consumer-visible change).
   - **Hint repair (D4):** the hint resolves through the shared `resolve_family` (design call
     §3) — identity for a family slug, mapped for a canonical slug; a hint that resolves to
     `unknown` sets a non-fatal `warning` naming the ignored value (never a silent no-op).
     The `+0.05` bonus and `min(1.0, …)` clamp are unchanged.
   - Thresholds (`min_words=100`, `< 0.30` → `unknown`, secondary band `0.10`) unchanged;
     exact top-tie refusal follows Design call §6.
4. **`register_match`.** Signature unchanged (`target_register, baseline_registers`). Both
   sides resolve via `resolve_family` before counting — so the function accepts family slugs
   (what `voice_distance` actually passes as target) **and** canonical slugs (what baselines
   declare and what any downstream caller may pass) interchangeably. Return dict keeps
   `strength` (same four library labels; the consumer-side `unavailable` meaning unchanged),
   `rationale`,
   `target` (raw input value, back-compat), `baseline_distribution` (**raw declared slugs**,
   back-compat) — and adds `taxonomy: REGISTER_TAXONOMY`, `target_family`, and
   `baseline_family_distribution` (the space the strength was actually computed in).

   **4a. Family-collapse disclosure (the firewall seam, chosen option).** Whenever the
   family-space comparison collapsed ≥ 2 distinct raw declared values into the matched family
   (or the raw target value differs from a matched baseline's raw values within the same
   family), `rationale` MUST carry an explicit document-type-blindness sentence, e.g.:
   "family-level match: `formal_legal_policy` here spans `legal_brief`, `grant_proposal`;
   a strong family match does not distinguish document types within the family." Because
   `voice_distance`'s render prints `rationale` on **all** strength branches
   (`voice_distance.py:508-517`), the disclosure reaches the operator-visible report with
   **zero render-code change** — and a test pins that end-to-end (Test 13). The ⚠️ marker
   remains weak/mismatch-only by design (disclosure ≠ alarm).
5. **`voice_distance.py` — versioned forwarding plus D5.**
   - Import `REGISTER_TAXONOMY` with the classifier functions at the existing lazy-import
     seams.
   - Forward `taxonomy` into `result["register_match"]["target_classification"]` beside
     `primary`/`confidence`/`secondary`; do not forward `scores`, `evidence`, or raw text.
   - Every `_build_register_match` return carries `taxonomy`, including the locally built
     `unavailable` shape. Normal returns inherit the same constant from `register_match`.
   - Re-key the claim-license read from
     `register_match.get("match", {}).get("verdict")` to `strength`, restoring a live value
     to `comparison_set.register_match`.
   - The report render remains behaviorally unchanged except that it prints frozen family
     slugs and any family-collapse caveat already carried by `rationale`.
6. **Coherence gates (tests, mechanical):**
   - domain(`CANONICAL_REGISTER_TO_FAMILY`) `== manifest_validator.ALLOWED_REGISTER` (imports
     both modules; schema growth fails loud until mapped);
   - codomain ⊆ families; `set(_SCORERS) ==` families minus `unknown`;
   - `len(set(_SCORERS.values())) == len(_SCORERS)` (no shared scorer functions, ever again);
   - the family, canonical, and legacy resolver domains are pairwise disjoint; both mapping
     codomains are subsets of scored families (never `unknown`);
   - every return from both public functions and every forwarded `voice_distance`
     classification/match block carries exactly `REGISTER_TAXONOMY`.
7. **Registration hygiene.** `capabilities.d/register_classifier.yaml` is a seeded TODO stub
   (`status: todo`, `_seeded_at: 2026-05-28`) — fill it honestly as part of this behavior
   change: `status: heuristic`, real `purpose`/`use_when`/`do_not_use_when`, `registers:` the
   family list, `consumers: [voice_distance]`. Update the matching per-id drop-in
   `_golden_capabilities/register_classifier.json` fragment to mirror it. **No `==N` count
   literal anywhere** (post-#170 drop-in standard). No `references/contract_fixtures/` entry
   exists for this id in voiceprint (grep-confirmed; `position_pair_register.json` is a
   different capability) — nothing to sync in-repo; the **cross-repo** vendored fixture is
   handled under Consumer-visible change. Spec 73 owns the future
   `register_composition_sweep` capability and adds it only after H1 lands. Ship a
   `changelog.d/<slug>-register-classifier-repair.md` fragment; never edit `CHANGELOG.md`
   directly.

### Consumer-visible change (stated, not hidden)

The **shapes** of `classify_register` / `register_match` results are additive (no key removed,
no signature change, strength labels unchanged), but the value vocabulary is intentionally
breaking: `primary` / `secondary` / `scores` now emit only family slugs. Old classifier-only
input spellings remain accepted through `LEGACY_REGISTER_TO_FAMILY`; canonical manifest slugs
remain accepted; outputs never emit deprecated aliases. `register_match` strengths change where
the old behavior was the defect (the structural mismatch guarantee on `personal`-declared
baselines is removed). Migration contract:

- **Migration trigger:** `taxonomy: "register_families/v2"` appears on every direct public
  return, under `voice_distance.results.register_match.target_classification`, and under
  `voice_distance.results.register_match.match` (including `unavailable`). Any consumer
  string-matching old labels migrates keyed on the marker. A regression test builds the real
  normalized envelope and proves both forwarded markers survive.
- **In-repo blast radius:** `voice_distance.py` (D5 plus D6 forwarding and unavailable-shape
  versioning), `tests/test_register_classifier.py`,
  `tests/test_voice_distance_register_guard.py`, `gen_contract_fixtures.py`, and the
  voice-distance schema/contract fixtures. The producer generator must derive the committed
  fixture from the repaired live envelope rather than hand-build the old register block.
  Strong/mismatch fixtures are re-grounded in family space; the stale fixture-only `verdict`
  shape is removed.
- **Cross-repo blast radius (setec-voicewright):** the vendored contract fixture
  `tests/setec-contract/fixtures/voice_distance.json` hardcodes register values
  (`literary_fiction`, a `registers: [literary_fiction]` baseline) **and a stale
  `register_match.match.verdict = "match"` shape that live voiceprint emission has never
  produced** (live emits `strength`; see D5). The contract test parses structurally, so
  value drift passes **silently** — therefore the voicewright **pin bump past this repair
  MUST re-sync that fixture** (and any other register-carrying goldens) to the family
  vocabulary and the new keys (`target_family`, `baseline_family_distribution`, `taxonomy`),
  replacing the stale `verdict` shape with `strength`. The release carrying this repair must
  not be promoted as consumer-ready until a voicewright pin-bump PR re-syncs the fixture,
  asserts the exact repaired register shape, and passes both its offline contract suite and
  live drift check with `--require-live` against the finalized immutable producer tag/commit.
  The consumer lock must prove that exact tag/commit, not a mutable branch or minimum-version
  string. That is a cross-repo migration gate, not optional future cleanup.
- **Cross-repo blast radius (APODICTIC):** APODICTIC also vendors
  `tests/setec-contract/fixtures/voice_distance.json`; it hardcodes
  `literary_fiction`, the stale `match.verdict` shape, and a
  `claim_license.comparison_set.register_match = "match"` value. Its
  `tests/setec-contract/test_setec_contract.py` pins the `voice_distance` release floor.
  The release carrying H1 must not be promoted as consumer-ready until an APODICTIC pin-bump
  PR regenerates the voice-distance fixture from the repaired producer, updates the pinned
  immutable tag/commit and capability snapshot, asserts the exact repaired register shape,
  and passes both the offline contract suite and live drift check with `--require-live`
  against that finalized producer identity. APODICTIC and voicewright are separate
  release-blocking migrations; clearing one does not clear the other.
- **Downstream canonical-slug inputs stay accepted:** any caller passing canonical
  manifest slugs (e.g. a dispatcher forwarding `--register personal`) keeps working — the
  shared resolver maps them, never a silent no-op (a resolver-miss surfaces a named
  warning).
- **Deprecated classifier inputs stay accepted for v2:** the ten frozen legacy aliases map
  through `LEGACY_REGISTER_TO_FAMILY`. Removal requires v3. This compatibility does not
  preserve old output labels or insertion-order tie selection.
- **Out-of-scope regime:** voicewright's own free-form front-matter/RAG register values and
  its `FICTION_REGISTERS` set are a separate vocabulary not governed by this mapping; they
  interact with voiceprint only through the CLI seams above.

## Hard preconditions (build-ordering, load-bearing)

1. **PR #343 dependency is satisfied.** Pinned base `80f3939` contains the 15-slug
   `ALLOWED_REGISTER`, including `professional_letter` and `teaching`. The builder still
   starts from current `origin/main`, proves those slugs remain present, and refuses any
   mapping-domain drift rather than copying this spec's set blindly.
2. **Re-ground-truth D1–D6 at build time.** Every count and symbol this spec relies on
   (18 `KNOWN_REGISTERS` / 14 `_SCORERS` keys / 8 scorer functions and their names / 15
   `ALLOWED_REGISTER` slugs / the current `voice_distance.py` render, baseline-reader,
   projection, unavailable-match, and claim-license anchors)
   must be re-verified against setec-voiceprint@head immediately before build, and the
   canonical mapping domain compared with actual `ALLOWED_REGISTER`. Assignments are frozen
   here; the builder may add a newly introduced canonical slug only under the taxonomy
   versioning rule, and may not silently reassign an existing slug. Any other discrepancy
   stops the build and comes back to spec resolution (the anchor-verify rule).
3. **Independent spec re-review clears this revision.** The merged spec's immutable-head
   review was NEEDS REWORK; merge status did not make it build-ready.
4. **Consumer migrations are scheduled as release gates.** The H1 implementation PR may
   land in voiceprint after its own review, but the release/pin must not be promoted as
   consumer-ready until both APODICTIC and voicewright fixture/pin migrations pass their
   respective offline drift gates.

## Contract (the testable interface)

- **task_surface:** `validation` (existing). `register_classifier` remains a library; H1
  adds no CLI or new capability id.
- **Public API:** `classify_register`, `register_match`, `render_register_match_block`,
  `KNOWN_REGISTERS` — signatures unchanged; exports gain `REGISTER_FAMILIES`,
  `REGISTER_TAXONOMY`, `CANONICAL_REGISTER_TO_FAMILY`, `LEGACY_REGISTER_TO_FAMILY`, and
  `resolve_family` (all in `__all__`).
- **JSON envelopes:** the library itself still emits no normalized envelope.
  `voice_distance` forwards the versioned additive blocks defined in Design §5.
- **Claim license:** unchanged posture, carried by the consumer as today. The module's honest
  framing — heuristic, not labeled-corpus-validated, "a prompt to ask register match
  questions" — is preserved verbatim in the docstring and the capability fragment's
  `do_not_use_when` (no definitive register call; no verdict; scores are compatibility
  heuristics, not probabilities). Family-collapse disclosure added to `rationale` (§4a).
- **capability entry:** existing `register_classifier` upgraded from its seeded stub with a
  matching drop-in golden and no count literal.
- **Dependencies / footprint:** stdlib only. The classifier does not import
  `manifest_validator` at runtime; the equality gate is test-side.

## Test contract (names + invariants the build must satisfy)

`plugins/setec-voiceprint/scripts/tests/test_register_classifier.py` (rewritten) plus fixture
re-checks in `test_voice_distance_register_guard.py` and one claim-license check in the
voice_distance schema tests:

1. **taxonomy-coherence (the D3 gate):** domain of `CANONICAL_REGISTER_TO_FAMILY` equals
   `manifest_validator.ALLOWED_REGISTER` exactly; codomain ⊆ `REGISTER_FAMILIES`; `unknown`
   not in either mapping's domain or codomain; family/canonical/legacy domains pairwise
   disjoint; both codomains contain only scored families.
2. **full scorer coverage (the D1 gate):** `set(_SCORERS.keys()) ==
   set(REGISTER_FAMILIES) - {"unknown"}` — every emittable family scored, nothing else.
3. **no shared scorers (the D2 gate):** `len(set(_SCORERS.values())) == len(_SCORERS)`.
4. **scorer key-agnosticism (re-key is a rename):** every scorer accepts exactly one
   positional argument (the feature dict — assert via `inspect.signature`), and for a fixed
   feature vector, calling each scorer function directly equals the value
   `classify_register` reports under that scorer's registered family key (the score depends
   only on text-derived features, never on the registration slug).
5. **shared resolver, both spellings (the P1 gate):** `resolve_family("first_person_essay")
   == "first_person_essay"` (identity on a family) AND `resolve_family("personal") ==
   "first_person_essay"` (mapped canonical slug); an unresolvable value → `"unknown"`. Then
   end-to-end: `register_match("first_person_essay", ["personal"] * 10)["strength"] ==
   "strong"` AND `register_match("personal", ["personal"] * 10)["strength"] == "strong"` —
   with `baseline_family_distribution == {"first_person_essay": 10}` and
   `baseline_distribution == {"personal": 10}` (raw preserved) in both.
6. **every family reachable as primary:** for each family, a synthetic fixture in that
   family's signal profile classifies with `primary ==` that family (extends the existing
   clear-case fixtures to all 8; the 8-reachable-of-17 defect becomes 8-of-8).
7. **hint accepts canonical slugs (the D4 gate):** `hint="personal"` applies the bonus to
   `first_person_essay`; `hint="first_person_essay"` behaves identically (same resolver); a
   nonsense hint yields a `warning` naming it and applies no bonus (assert no silent no-op).
8. **back-compat shape:** `classify_register` output carries all six legacy keys plus
   `taxonomy == "register_families/v2"` on every path; `register_match` output carries all
   four legacy keys plus taxonomy and the two family keys; strength label set unchanged
   (`strong`/`moderate`/`weak`/`mismatch`).
9. **refusal paths unchanged:** short-text refusal (`primary == "unknown"`, confidence 0.0,
   warning) and the `< 0.30` all-weak refusal behave exactly as today.
10. **determinism:** same text → identical output dict across runs (stable sort retained; no
    randomness introduced).
11. **no-verdict guard:** no output key from `{verdict, is_ai, is_human, label, same_author}`
    in either public function's result; `confidence` remains a bounded [0,1] heuristic score;
    the module `__doc__` still contains the "prompt to ask register match questions" framing
    (a **presence check** documenting the posture — the mechanical enforcement is the key
    guard, not the docstring).
12. **claim-license read-site repaired (the D5 gate):** a `voice_distance` run whose
    `register_match.match.strength` is non-null carries that same value at
    `claim_license.comparison_set.register_match` (not `None`); grep-level: the string
    `"verdict"` no longer appears in `voice_distance.py`'s register-match handling.
13. **family-collapse disclosure reaches the render (the firewall gate):** a
    `register_match` whose family-space comparison collapsed ≥ 2 distinct raw values carries
    the document-type-blindness sentence in `rationale`, AND `voice_distance`'s rendered
    report block contains it for a `strong`/`moderate` result (pinning that the disclosure
    survives the `voice_distance.py:508-517` render path, where ⚠️ is weak/mismatch-only).
14. **consumer integration:** `test_voice_distance_register_guard.py` still passes with
    fixture register values drawn from `ALLOWED_REGISTER`; the no-register-baseline path
    retains `strength=unavailable` while gaining only the taxonomy marker; the strong-match
    and mismatch fixtures are re-checked to be same-family and cross-family respectively.
15. **D6 marker forwarding:** a real `voice_distance` normalized envelope carries
    `register_families/v2` under both `target_classification.taxonomy` and `match.taxonomy`;
    the same holds for the `unavailable` match path. A mutation dropping either projection
    fails. Regenerate the committed producer contract fixture through
    `gen_contract_fixtures.py` and assert that the generated and committed shapes both carry
    family-valued `primary`/`secondary`/`scores`, both taxonomy markers, `target_family`,
    `baseline_family_distribution`, `match.strength`, and the repaired
    `claim_license.comparison_set.register_match`, with no stale `verdict`. A mutation that
    leaves generator and golden mutually stale fails.
16. **legacy input migration:** every frozen legacy slug resolves to its tabled family and is
    accepted as a hint/target/baseline input; no deprecated slug appears in `primary`,
    `secondary`, `scores`, `target_family`, or `baseline_family_distribution`.
17. **exact-top-tie refusal:** inject two distinct scorer functions with the same rounded
    highest score ≥0.30; assert `primary=unknown`, all tied families in deterministic
    `secondary` order, retained confidence, and named warning. A one-unit score difference
    at the fourth decimal restores the actual winner. Invalid-hint + tie warnings appear in
    the frozen order.
18. **frozen mappings and ownership:** assert the exact 15-row canonical mapping and exact
    ten-row legacy mapping from this spec, not merely set equality; importing
    `register_classifier` in a fresh interpreter does not import `manifest_validator`.
## Calibration posture

Unchanged and explicitly not upgraded: `status: heuristic`, not labeled-corpus-validated, no
band, no verdict. This repair fixes *coherence*, not *accuracy* — no claim is made that the 8
family scorers are good, only that they are now the honest, complete, collision-free
resolution of what the module can actually discriminate, and that the structural
false-mismatch guarantee is gone. A labeled register-tagged corpus evaluation (per-family
precision against independently adjudicated labels) would calibrate it later. Spec 73's
future composition sweep is explicitly **not** that calibration: declared manifest values
are comparison metadata, not independent truth. Any calibration run records a PROVENANCE
entry before any `status` promotion.

## Out of scope / non-goals

- **No new scorers, no scorer-arithmetic changes** — coverage and identity are repaired;
  heuristic quality is a separate concern.
- **No register-composition sweep, corpus scan, checkpoint, report publication, row
  selection, corpus mutation, registration, or semantic-mode inference.** Those diagnostic
  inventory mechanics belong to Spec 73 after H1 lands.
- **No `ALLOWED_REGISTER` changes** — the canonical set is consumed, not edited (PR #343's
  additions are absorbed via the mapping, not negotiated).
- **No manifest re-tagging, no validator changes** — `manifest_validator.py` is untouched.
- **No APODICTIC- or voicewright-side code in this repository** — both vendored-fixture
  re-syncs are release-blocking consumer pin-bump follow-ons; voicewright's own register regime
  (front-matter/RAG, `FICTION_REGISTERS`) is a separate vocabulary this mapping does not
  govern.
- **No verdict, no band, no calibration claim** — posture frozen.

## Resolved decisions (formerly open questions)

1. `policy_advocacy` and `grant_proposal` map to `formal_legal_policy`; the family-collapse
   disclosure prevents that closest-scorer assignment from masquerading as document-type
   equivalence.
2. `expert_affidavit`, `testimony_policy`, and `professional_letter` map to the renamed
   `formal_first_person` family because the scorer's actual cues are formal address,
   first-person usage, and statutory language.
3. `teaching` maps to `academic`; the report remains explicit that this is heuristic family
   compatibility, not document-type identity.
4. `manifest_validator` owns canonical slugs; `register_classifier` owns a literal family
   mapping. Equality is enforced test-side; there is no runtime validator import.
5. The eight v2 emitted family names are frozen in Design call §1. Changes follow the
   taxonomy-versioning rule rather than builder discretion.
6. Exact rounded top ties at/above the refusal floor return `primary=unknown` with all tied
   families in `secondary` and a deterministic warning; non-exact near-ties retain the
   existing `<0.10` band.

There are no remaining builder-discretion questions in this revision.

## Review findings folded in

Immutable-head Codex review of merged spec head
`f864b91410b8f502c78f307f660b5db32442109b` returned **NEEDS REWORK**:

- **[P1] Migration marker dropped by projection → D6.** The allowed implementation scope now
  includes `voice_distance` taxonomy forwarding on both target-classification and match
  blocks, including unavailable, with real-envelope mutation tests.
- **[P1] Public semantics left to builder discretion.** All six former open questions are
  resolved: exact canonical/legacy mappings, taxonomy ownership, runtime coupling, family
  names, and exact-tie behavior are frozen. The corrected H1/H2 decomposition moves the
  corpus sweep into separately reviewed Spec 73.

Adversarially reviewed (verdict NEEDS-REWORK, 1 P1 + 5 P2; all code-grounded findings
re-verified against head during the fold). Recorded so the build honors each:

- **[P1] The spec's own gates were mutually unsatisfiable.** `register_match`'s target is
  `classify_register`'s `primary` — already a family slug — but the draft routed both sides
  through `CANONICAL_REGISTER_TO_FAMILY`, whose domain the drift test pins to canonical slugs
  only; an already-family target would resolve to `unknown` and the draft's own `personal`
  pin would fail as `mismatch`. Fixed with the single family-first `resolve_family` used at
  all three call-sites (design call §3; Tests 5/7 assert both spellings).
- **[P1-adjacent, firewall seam] Family collapse needed an operator-visible disclosure.**
  Precision correction folded: the render at `voice_distance.py:508-517` does print
  `rationale` for strong/moderate (the `elif strength:` branch) — what was missing is a
  family-collapse caveat *in* the rationale (and the ⚠️ tier, which deliberately stays
  weak/mismatch-only). Chosen: option (a)-via-rationale — producer-side disclosure sentence
  in `rationale` (§4a), pinned end-to-end through the render (Test 13); no render-code
  change.
- **[P2] Two read-sites, mismatched keys → D5.** `voice_distance.py:938` reads
  `match.verdict`, which live emission has never carried (it emits `strength`) — the
  claim-license `comparison_set.register_match` is dead (`None`) at head. Repaired under the
  expanded `voice_distance` integration scope + Test 12.
- **[P2] Cross-repo consumers added to blast radius.** voicewright's and APODICTIC's vendored
  `tests/setec-contract/fixtures/voice_distance.json` hardcodes old register values and the
  stale `verdict` shape; its contract test parses structurally so drift is silent. The
  `taxonomy` marker is named as the migration trigger; both pin-bump re-syncs are
  release-blocking consumer follow-ons; canonical and deprecated classifier inputs stay
  accepted via the resolver.
- **[P2] Scorer re-key made provably mechanical.** Design §2 asserts (and Test 4 pins) that
  scorers consume only the feature vector, never their registration key.
- **[P2] Universality claim scoped.** "Canonical" scoped to voiceprint manifests; explicit
  boundary note that voicewright's front-matter/RAG + `FICTION_REGISTERS` regime is out of
  scope (and stays out of scope for the spec-36 M2 sweep).
- **[P2] Land-after-#343 + re-ground-truth promoted to hard preconditions** (own section):
  #343 is now present at the pinned base; re-verify all counts/anchors and exact mapping
  domain at build head.
- **[Minor]** "now reads `strong`" softened to "removes the structural guarantee of
  `mismatch`" (accuracy disclaimed consistently); Test 11's `__doc__` check labeled a
  presence check, not posture enforcement.
