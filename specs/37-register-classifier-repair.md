# 37-register-classifier-repair

> Repair the heuristic register classifier so its taxonomy, scorer coverage, and tie behavior
> are coherent with the manifest schema it exists to guard: **`manifest_validator.ALLOWED_REGISTER`
> becomes the canonical register vocabulary**, the classifier emits a small set of **register
> FAMILIES** (one honest scorer per family, no alias collisions), and a **documented, total,
> many-to-one mapping** connects canonical slugs to families — so `register_match` stops
> reporting taxonomy misalignment as register mismatch. Posture unchanged: heuristic,
> not-labeled-corpus-validated, no-verdict — the output is *a prompt to ask register-match
> questions*, never a measured distribution.

- **Status:** Draft (reworked after adversarial spec-review: 1 P1 + 5 P2 folded — see
  "Review findings folded in")
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

This repair is also the named precondition for the deferred **M2 register-composition sweep**
cut from spec 36 (`specs/36-passage-level-corpus-hygiene.md`, § "Deferred — M2
register-composition sweep"): a sweep CLI over the current classifier "is a wrapper around a
defect: it would report taxonomy misalignment as corpus mixture." The sweep gets its own spec
**after** this lands; it is not specced here.

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

`manifest_validator.ALLOWED_REGISTER` (`manifest_validator.py:67`) — the schema authority the
validator warns against (`manifest_validator.py:309`) and the vocabulary **voiceprint
manifests** declare — currently holds **13** slugs on `main`:

```
literary_fiction, blog_essay, academic_philosophy, testimony_policy, personal,
policy_advocacy, literary_horror, policy_brief, scholarly_article, legal_brief,
grant_proposal, expert_affidavit, regulatory_comment
```

(Spec 36's deferred-M2 note says 15 — that counts the two additions `professional_letter` and
`teaching` made **on the in-flight spec-36 branch (PR #343)**, not yet on `main`. This spec
makes landing after #343 a hard precondition: see "Hard preconditions".)

Overlap with `KNOWN_REGISTERS` is exactly **6**: `blog_essay`, `literary_fiction`,
`literary_horror`, `academic_philosophy`, `policy_advocacy`, `testimony_policy`. And the
divergence compounds with D1/D2 at the integration seam:

- Of the 6 overlapping slugs, `policy_advocacy` has **no scorer** (D1) and `literary_horror` is
  **alias-shadowed** (D2) — so only **4 of the 13** schema slugs (`blog_essay`,
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
`_SCORERS`, so the hint silently no-ops for **8 of the 13** schema slugs, including `personal`
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
be assumed in-scope by the follow-on spec-36 M2 sweep either.

**But the classifier must NOT align 1:1 to the canonical set.** The 13 canonical slugs are
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
   aliases: `formal_legal_policy` (← `_score_legal_or_policy_memo`), `testimony`
   (← `_score_testimony_policy`), `academic` (← `_score_academic`), `journalism`
   (← `_score_journalism`), `narrative_fiction` (← `_score_literary_fiction`),
   `first_person_essay` (← `_score_blog_or_personal_essay`), `promotional`
   (← `_score_marketing`), `short_social` (← `_score_social_thread`), plus the `unknown`
   sentinel (refusal only; never scored).
2. A module-level exported table **`CANONICAL_REGISTER_TO_FAMILY`** maps **every**
   `ALLOWED_REGISTER` slug to exactly one family (proposed assignments below; several flagged
   for maintainer confirmation). Totality is **mechanically enforced**: a test imports both
   modules and asserts the mapping's domain `== manifest_validator.ALLOWED_REGISTER` — so any
   future schema addition fails CI loudly until mapped. This is a structural gate, not prose
   (per the mechanical-not-rhetorical guard standard).
3. **One shared resolver, family-first (the P1 fix).** All register-valued inputs — the
   `classify_register` hint, the `register_match` target, and every baseline declaration —
   resolve through a **single** helper:

   ```
   resolve_family(value) ->
       value                                if value in REGISTER_FAMILIES   (identity; incl. "unknown")
       CANONICAL_REGISTER_TO_FAMILY[value]  elif value in the mapping        (canonical slug)
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
   resolver handles them, and a disjointness gate keeps the two branches from ever
   overlapping (Design §6).
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
6. **Alias-tie bias removed by construction.** With no two families sharing a scorer, the
   equal-by-construction tie class disappears. Genuine numeric ties between *different*
   scorers remain possible; the deterministic stable sort stays (determinism is a feature), and
   the existing `secondary` band (within 0.10) is the honest reporting channel for near-ties —
   now listing genuinely competitive families rather than mirror-image aliases.
7. **Family collapse is disclosed, not hidden (the firewall seam).** A family-space `strong`
   between, say, a `grant_proposal` target declaration and a `legal_brief`-dominated baseline
   is **document-type-blind within `formal_legal_policy`** — and that must reach the operator,
   not just the JSON. Chosen mechanism: the disclosure lives in **`rationale`** (see Design
   §4a), because the render at `voice_distance.py:508-517` already prints `rationale` for
   **every** strength (the ⚠️ marker is weak/mismatch-only, but the `elif strength:` branch
   renders `strength` + `rationale` for strong/moderate too — the caveat is *not* dropped from
   the render path, it just has to be *in* the rationale). The ⚠️ severity tier stays
   weak/mismatch-only: a strong family match is a scope disclosure, not an alarm condition.

Proposed `CANONICAL_REGISTER_TO_FAMILY` (the 13 slugs on `main` today, plus the two PR #343
adds; the table is **re-derived from the actual `ALLOWED_REGISTER` contents at build time** —
see "Hard preconditions"):

| canonical slug | family | note |
|---|---|---|
| `literary_fiction` | `narrative_fiction` | |
| `literary_horror` | `narrative_fiction` | |
| `blog_essay` | `first_person_essay` | |
| `personal` | `first_person_essay` | fixes the D3 structural mismatch |
| `academic_philosophy` | `academic` | |
| `scholarly_article` | `academic` | |
| `testimony_policy` | `testimony` | |
| `expert_affidavit` | `testimony` | sworn first-person + statutory — flagged, see Open questions |
| `policy_brief` | `formal_legal_policy` | |
| `legal_brief` | `formal_legal_policy` | |
| `regulatory_comment` | `formal_legal_policy` | |
| `grant_proposal` | `formal_legal_policy` | weakest fit in the family — flagged, see Open questions |
| `policy_advocacy` | `formal_legal_policy` | first *scored* home it has ever had — flagged, see Open questions |
| `professional_letter` (#343) | `first_person_essay` | `_FORMAL_ADDRESS` fires on salutations — flagged, see Open questions |
| `teaching` (#343) | `academic` | flagged, see Open questions |

Families with no canonical slug mapping to them (`journalism`, `promotional`, `short_social`)
**remain emittable**: the classifier may honestly detect prose kinds the corpus schema doesn't
declare, and `register_match` will then honestly report the divergence. Old `KNOWN_REGISTERS`
slugs that were classifier-only fictions (`email`, `report_prose` — never scored, never
declared) are **dropped**; old alias slugs (`commercial_fiction`, `newsletter`,
`academic_general`, `legal_memo`, `policy_memo`, `personal_essay`) fold into their families
and disappear as distinct labels.

## Design (the repair, concretely)

All in `register_classifier.py` except one deliberately small `voice_distance.py` fix (§5).

1. **Taxonomy.** Replace the 18-slug `KNOWN_REGISTERS` tuple with the **9-entry family tuple**
   (8 families + `unknown`). Keep the name `KNOWN_REGISTERS` exported with its existing
   semantics ("the set `classify_register` can return as `primary`"); add `REGISTER_FAMILIES`
   as an explicit alias and export `CANONICAL_REGISTER_TO_FAMILY` and `resolve_family`.
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
   - `taxonomy: "register_families/v2"` — a version marker so any consumer can detect the
     vocabulary change mechanically. This marker is the **migration trigger** for downstream
     fixture/golden re-syncs (see Consumer-visible change).
   - **Hint repair (D4):** the hint resolves through the shared `resolve_family` (design call
     §3) — identity for a family slug, mapped for a canonical slug; a hint that resolves to
     `unknown` sets a non-fatal `warning` naming the ignored value (never a silent no-op).
     The `+0.05` bonus and `min(1.0, …)` clamp are unchanged.
   - Thresholds (`min_words=100`, `< 0.30` → `unknown`, secondary band `0.10`) unchanged.
4. **`register_match`.** Signature unchanged (`target_register, baseline_registers`). Both
   sides resolve via `resolve_family` before counting — so the function accepts family slugs
   (what `voice_distance` actually passes as target) **and** canonical slugs (what baselines
   declare and what any downstream caller may pass) interchangeably. Return dict keeps
   `strength` (same four labels; the consumer-side `unavailable` untouched), `rationale`,
   `target` (raw input value, back-compat), `baseline_distribution` (**raw declared slugs**,
   back-compat) — and adds `target_family` and `baseline_family_distribution` (the space the
   strength was actually computed in).

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
5. **`voice_distance.py` — one read-site fix (D5).** Re-key `voice_distance.py:938` from
   `register_match.get("match", {}).get("verdict")` to read `strength`, restoring a live
   value to the claim-license `comparison_set.register_match` (dead-always-`None` at head).
   This is the only `voice_distance` code change; the render path and
   `_build_register_match` are untouched.
6. **Coherence gates (tests, mechanical):**
   - domain(`CANONICAL_REGISTER_TO_FAMILY`) `== manifest_validator.ALLOWED_REGISTER` (imports
     both modules; schema growth fails loud until mapped);
   - codomain ⊆ families; `set(_SCORERS) ==` families minus `unknown`;
   - `len(set(_SCORERS.values())) == len(_SCORERS)` (no shared scorer functions, ever again);
   - `REGISTER_FAMILIES ∩ domain(CANONICAL_REGISTER_TO_FAMILY) == ∅` (family names never
     collide with canonical slugs — keeps the resolver's two branches disjoint; without this
     gate a future slug/family name collision would silently change resolution order).
7. **Registration hygiene.** `capabilities.d/register_classifier.yaml` is a seeded TODO stub
   (`status: todo`, `_seeded_at: 2026-05-28`) — fill it honestly as part of this behavior
   change: `status: heuristic`, real `purpose`/`use_when`/`do_not_use_when`, `registers:` the
   family list, `consumers: [voice_distance]`. Update the matching per-id drop-in
   `_golden_capabilities/register_classifier.json` fragment to mirror it. **No `==N` count
   literal anywhere** (post-#170 drop-in standard). No `references/contract_fixtures/` entry
   exists for this id in voiceprint (grep-confirmed; `position_pair_register.json` is a
   different capability) — nothing to sync in-repo; the **cross-repo** vendored fixture is
   handled under Consumer-visible change. Ship a
   `changelog.d/<slug>-register-classifier-repair.md` fragment; never edit `CHANGELOG.md`
   directly.

### Consumer-visible change (stated, not hidden)

The **shapes** of `classify_register` / `register_match` results are backward compatible
(strict supersets; no key removed, no signature change, strength labels unchanged), so
`voice_distance` runs with only the one-line D5 read-site fix. The **values** of
`primary` / `secondary` / `scores` keys change vocabulary (family slugs), and `register_match`
strengths change where the old behavior was the defect (the structural mismatch guarantee on
`personal`-declared baselines is removed). Migration contract:

- **Migration trigger:** the `taxonomy: "register_families/v2"` marker in
  `classify_register` output. Any consumer string-matching old slugs (`personal_essay`,
  `legal_memo`, …) migrates keyed on that marker.
- **In-repo blast radius:** `voice_distance.py` (D5 one-liner; report text now renders
  family slugs), `tests/test_register_classifier.py` (rewritten with the module), and
  `tests/test_voice_distance_register_guard.py` (fixture register values re-checked so
  same-slug pairs still land same-family and the mismatch fixture crosses families).
- **Cross-repo blast radius (setec-voicewright):** the vendored contract fixture
  `tests/setec-contract/fixtures/voice_distance.json` hardcodes register values
  (`literary_fiction`, a `registers: [literary_fiction]` baseline) **and a stale
  `register_match.match.verdict = "match"` shape that live voiceprint emission has never
  produced** (live emits `strength`; see D5). The contract test parses structurally, so
  value drift passes **silently** — therefore the voicewright **pin bump past this repair
  MUST re-sync that fixture** (and any other register-carrying goldens) to the family
  vocabulary and the new keys (`target_family`, `baseline_family_distribution`, `taxonomy`),
  replacing the stale `verdict` shape with `strength`. This is a consumer follow-on chore
  named here, not implemented here.
- **Downstream canonical-slug inputs stay accepted:** any caller passing canonical
  manifest slugs (e.g. a dispatcher forwarding `--register personal`) keeps working — the
  shared resolver maps them, never a silent no-op (a resolver-miss surfaces a named
  warning).
- **Out-of-scope regime:** voicewright's own free-form front-matter/RAG register values and
  its `FICTION_REGISTERS` set are a separate vocabulary not governed by this mapping; they
  interact with voiceprint only through the CLI seams above.

## Hard preconditions (build-ordering, load-bearing)

1. **Land after PR #343 (spec 36).** #343 grows `ALLOWED_REGISTER` by `professional_letter`
   and `teaching` (13 → 15). The domain-equality gate hard-fails on any unmapped slug, so
   this repair must be cut from a `main` that already contains #343 — or, if ordering flips,
   whichever branch lands second adopts the gate and maps the new slugs before merge (the
   re-freshen-adopts-new-gates rule). Treat "after #343" as the default plan, not a
   preference.
2. **Re-ground-truth D1–D5 at build time.** Every count and symbol this spec relies on
   (18 `KNOWN_REGISTERS` / 14 `_SCORERS` keys / 8 scorer functions and their names / 13-or-15
   `ALLOWED_REGISTER` slugs / the `voice_distance.py` anchors :508, :577, :599, :852, :938)
   must be re-verified against setec-voiceprint@head immediately before build, and the
   `CANONICAL_REGISTER_TO_FAMILY` table **re-derived from the actual `ALLOWED_REGISTER`
   contents then** — not copied from this spec. Any discrepancy stops the build and comes
   back to the maintainer (the anchor-verify rule).

## Contract (the testable interface)

- **task_surface:** `validation` (existing; the module already declares it). **No new
  surface, no new id, no CLI** — this module stays library-only; its runtime consumer remains
  `voice_distance.py`. (A CLI belongs to the spec-36 M2 sweep, specced separately after this.)
- **Public API:** `classify_register`, `register_match`, `render_register_match_block`,
  `KNOWN_REGISTERS` — signatures unchanged; exports gain `REGISTER_FAMILIES`,
  `CANONICAL_REGISTER_TO_FAMILY`, and `resolve_family` (all in `__all__`).
- **JSON envelope:** N/A (library module; no `build_output` envelope today, none added). The
  family vocabulary and mapping surface to envelopes only through `voice_distance`'s existing
  `register_match` block (shape: strict superset of today's) and the repaired
  `comparison_set.register_match` claim-license field (D5: `None`-always → live `strength`).
- **Claim license:** unchanged posture, carried by the consumer as today. The module's honest
  framing — heuristic, not labeled-corpus-validated, "a prompt to ask register match
  questions" — is preserved verbatim in the docstring and the capability fragment's
  `do_not_use_when` (no definitive register call; no verdict; scores are compatibility
  heuristics, not probabilities). Family-collapse disclosure added to `rationale` (§4a).
- **capabilities.yaml entry:** existing `register_classifier` id upgraded from seeded stub to
  a real fragment (`status: heuristic`), golden fragment updated to match; drop-in only, no
  count bumps.
- **Dependencies / footprint:** stdlib only, unchanged. The one new intra-repo edge —
  `register_classifier` tests import `manifest_validator` for the domain gate — is test-side;
  whether the runtime module also imports it is an implementation choice (see Open questions).

## Test contract (names + invariants the build must satisfy)

`plugins/setec-voiceprint/scripts/tests/test_register_classifier.py` (rewritten) plus fixture
re-checks in `test_voice_distance_register_guard.py` and one claim-license check in the
voice_distance schema tests:

1. **taxonomy-coherence (the D3 gate):** domain of `CANONICAL_REGISTER_TO_FAMILY` equals
   `manifest_validator.ALLOWED_REGISTER` exactly; codomain ⊆ `REGISTER_FAMILIES`; `unknown`
   not in the mapping's domain or codomain; `REGISTER_FAMILIES` disjoint from the mapping's
   domain (resolver branches never overlap).
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
   `taxonomy == "register_families/v2"`; `register_match` output carries all four legacy keys
   plus the two family keys; strength label set unchanged
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
    fixture register values drawn from `ALLOWED_REGISTER`; the `unavailable` path
    (no-register baselines) is untouched; the strong-match and mismatch fixtures are
    re-checked to be same-family and cross-family respectively.

## Calibration posture

Unchanged and explicitly not upgraded: `status: heuristic`, not labeled-corpus-validated, no
band, no verdict. This repair fixes *coherence*, not *accuracy* — no claim is made that the 8
family scorers are good, only that they are now the honest, complete, collision-free
resolution of what the module can actually discriminate, and that the structural
false-mismatch guarantee is gone. A labeled register-tagged corpus evaluation (per-family
precision against declared registers) would calibrate it later and is exactly what the
spec-36 M2 sweep becomes able to support once this lands; any such run records a PROVENANCE
entry before any `status` promotion.

## Out of scope / non-goals

- **No new scorers, no scorer-arithmetic changes** — coverage and identity are repaired;
  heuristic quality is a separate concern.
- **No CLI, no new task_surface, no new capability id** — the spec-36 M2 register-composition
  sweep is the follow-on spec this unblocks, not a passenger here.
- **No `ALLOWED_REGISTER` changes** — the canonical set is consumed, not edited (PR #343's
  additions are absorbed via the mapping, not negotiated).
- **No manifest re-tagging, no validator changes** — `manifest_validator.py` is untouched.
- **No voicewright-side changes in this repair** — the vendored-fixture re-sync is a named
  consumer follow-on on the next pin bump; voicewright's own register regime
  (front-matter/RAG, `FICTION_REGISTERS`) is a separate vocabulary this mapping does not
  govern.
- **No verdict, no band, no calibration claim** — posture frozen.

## Open questions

1. **`policy_advocacy` family assignment.** Proposed `formal_legal_policy` (statutory/policy
   vocabulary cues), but advocacy prose spans op-ed-like first-person argument through formal
   comment letters. Maintainer call; the mapping table makes the choice explicit and cheap to
   revise.
2. **`grant_proposal` and `expert_affidavit` assignments.** `grant_proposal` →
   `formal_legal_policy` is the weakest fit (little statutory signal in real proposals);
   `expert_affidavit` → `testimony` vs `formal_legal_policy` is arguable. Same cheap-to-revise
   posture.
3. **PR #343's `professional_letter` / `teaching`.** Proposed `first_person_essay` /
   `academic` respectively — confirm at build time against the merged set (hard precondition
   2 re-derives the table then).
4. **Runtime import vs test-only coupling.** Should `register_classifier` import
   `manifest_validator` at runtime to derive the mapping domain, or keep the mapping literal
   with the equality enforced test-side only? Test-side (proposed) avoids a runtime
   cross-script import in a module `voice_distance` pulls on every run; the drift gate
   provides the same guarantee. Confirm.
5. **Family slug names.** `formal_legal_policy`, `first_person_essay`, `narrative_fiction`,
   `promotional`, `short_social` are proposals; names are consumer-visible once emitted, so
   bikeshed **before** build, not after.
6. **`secondary` tie semantics.** With aliases gone, should exact cross-scorer ties (rare but
   possible) be surfaced more loudly than the 0.10 band (e.g. a `tied_primary` note), or is
   the existing band sufficient? Proposed: existing band is sufficient; note it in the
   docstring.

## Review findings folded in

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
  claim-license `comparison_set.register_match` is dead (`None`) at head. Repaired as the
  one-line D5 fix + Test 12.
- **[P2] Cross-repo consumer added to blast radius.** voicewright's vendored
  `tests/setec-contract/fixtures/voice_distance.json` hardcodes old register values and the
  stale `verdict` shape; its contract test parses structurally so drift is silent. The
  `taxonomy` marker is named as the migration trigger; the pin-bump re-sync is a named
  consumer follow-on; canonical-slug CLI inputs stay accepted via the resolver.
- **[P2] Scorer re-key made provably mechanical.** Design §2 asserts (and Test 4 pins) that
  scorers consume only the feature vector, never their registration key.
- **[P2] Universality claim scoped.** "Canonical" scoped to voiceprint manifests; explicit
  boundary note that voicewright's front-matter/RAG + `FICTION_REGISTERS` regime is out of
  scope (and stays out of scope for the spec-36 M2 sweep).
- **[P2] Land-after-#343 + re-ground-truth promoted to hard preconditions** (own section):
  re-verify all counts/anchors at head immediately before build; re-derive the mapping table
  from the actual `ALLOWED_REGISTER` contents then.
- **[Minor]** "now reads `strong`" softened to "removes the structural guarantee of
  `mismatch`" (accuracy disclaimed consistently); Test 11's `__doc__` check labeled a
  presence check, not posture enforcement.
