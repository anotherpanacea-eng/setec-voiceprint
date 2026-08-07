# SVP hermetic-mode amendment — narrowing the claim-license guard

> Amends `specs/svp-packaging-conversion.md` §3 (the claim-license deficit lock) and
> §5/Phases (which named this document as "the queued hermetic-mode amendment and its
> own reviewed PR"). This is that document. It authorizes its own follow-up PR; it does
> not itself land code.

- **Status:** BUILD-READY (v1) · **Date:** 2026-08-06 · **Repo:** `setec-voiceprint`
- **Provenance:** an Opus reviewer's ruling on PR #387 (packaging P1). Hermetic backend
  mode (`SETEC_HERMETIC_BACKEND=stub` at the judge/embedding/surprisal backend
  construction boundaries, plus a scratch-copy consumer-case gate) was built, then
  extracted, because it collided with the just-landed no-change claim-license guard.
  The reviewer's ruling, verbatim intent: don't widen the guard's `moves` mechanism
  (a laundering channel already closed once), don't narrow the protected-set closure
  (that closure is doing its job), don't merge with the gate red — land an amendment
  first, "because the guard must never be the thing that bends." This document is that
  amendment.
- **Depends on:** `specs/svp-packaging-conversion.md` §3 (`tools/check_claim_license_guard.py`),
  §5 (`tools/check_zero_install.py`'s bare-copy gate). Authorizes a new phase,
  **P1-hermetic**, sequenced after packaging P1 and independent of P2–P5.
- **Historical branch:** `build/svp-hermetic-mode` (local, predates this amendment and
  PR #387's fix rounds; main has moved since). Reference only — nothing here reuses its
  code, and its guard version differs from the one on `origin/main` today.

## 1. Finding — the §3/§5 collision, and what it actually is

`specs/svp-packaging-conversion.md` §5 scoped P1's scratch-copy gate to structural
reachability only, deferring "the broader hermetic consumer matrix" to this amendment.
§3 landed the same phase's no-change claim-license guard with, verbatim, "**No semantic
claim-license change is authorized by this packaging spec.** Any canonical change fails
CI with no override." Both are P1. The first hermetic-mode build, adding
`SETEC_HERMETIC_BACKEND=stub` short-circuits to `judge_backends.make_api_judge`,
`EmbeddingBackend._load`, and `SurprisalBackend._load`, tripped the second.

**The verified mechanism, read from `tools/check_claim_license_guard.py` on
`origin/main`.** The guard (`build_protected_set`) seeds the protected set from every
module whose AST defines a `_claim_license*`-prefixed name or calls `ClaimLicense(...)`,
then closes it by resolving plugin-local `Name` loads inside — for a seed, only its own
`_claim_license_relevant_subtrees()`; for anything pulled in purely as a supplier, its
**whole module body** (`build_protected_set`'s in-code rationale: a prior, narrower,
symbol-scoped revision "silently excluded a real supplier," so the whole-module rule is
the deliberately conservative, fail-closed reading). Independently of any hermetic-mode
edit, `judge_backends.py` is already in that closure today — `narrative_judge.py`
imports it bare (`import judge_backends`) inside `build_judge()`, and `narrative_judge.py`
is itself a supplier of the seed module `narrative_decision_long_form.py` — confirmed by
running `build_protected_set()` against this worktree's `origin/main` tree. Once a
module is protected, `compare_protected_modules` requires the **whole-module**
`ast.dump(..., include_attributes=False)` to be byte-identical to the merge-base copy —
not just the claim-license subtree — with no override clause at all. So the
`make_api_judge` hermetic short-circuit, though it touches nothing inside
`judge_backends.py`'s `_claim_license*`-relevant surface (there is none; `judge_backends.py`
is a supplier, not a seed), still fails CI, because *any* AST delta to a protected
module fails, additive or not.

`embedding_backend.py` and `surprisal_backend.py` are the same class of module —
construction-boundary suppliers of exactly the identifier/model-provenance information a
`_claim_license` block legitimately reports (`surprisal_backend.py`'s own docstring:
"Mirrors `embedding_backend.EmbeddingBackend.identifier_block()`"), and the original
PR's own extraction commit (`build/svp-hermetic-mode`, `d40c1ae`) records them as
"legitimate protected suppliers" at the guard revision in effect when it was reviewed.
Under the closure algorithm live on `origin/main` today, neither is currently a supplier
of any seed's protected subtree (confirmed by patching a copy of `embedding_backend.py`
with the historical stub diff and re-running `build_protected_set()`: it does not enter
the closure, because no seed's `_claim_license*`/`ClaimLicense(...)` call site currently
references it by name). That is not a contradiction — it is the reason this amendment
does not chase a fixed file list. **Protected-set membership is a property of the live
AST import graph, recomputed on every run.** A later, entirely unrelated edit — for
example, a `_claim_license` block starting to report which embedding model backed a
Tier‑3 signal, which is exactly the kind of provenance detail these modules exist to
supply — pulls `embedding_backend.py`/`surprisal_backend.py` into the closure the same
way `narrative_judge.py`'s bare import already pulled in `judge_backends.py`. Naming
today's three files in a closed carve-out would "fix" this collision for exactly one
PR and silently stop protecting the next one. That is the blanket-ban-for-one-instance
shape this amendment is required not to repeat.

**The fix cannot be: widen `moves`, narrow the closure, or merge red.** Per the review
ruling and per `build/svp-hermetic-mode`'s own follow-up commit (`ebcf1ef`), a prior
`moves[].old_symbol`/`new_symbol` mechanism — an author-supplied JSON row instructing
the guard to apply a raw string substitution to the *dumped AST text* before comparing —
was proven exploitable: a row naming `old_symbol="REPORTS"` / `new_symbol="REFUSES"`
silently inverted a license sentence inside an unrelated `Constant` string while the
guard reported a clean relocation and exited 0. That mechanism is dead. Section 2 below
is deliberately shaped to be impossible to express as a `moves[]`-style row: it takes no
author input, performs no text substitution of any kind, and never touches a `Constant`
node inside a protected subtree under any circumstance.

## 2. Narrowed rule: semantic-only protection, structurally verified additivity

**§3's edited text** (the exact replacement — see the parallel edit to
`specs/svp-packaging-conversion.md` itself):

> **No semantic claim-license change is authorized by this packaging spec.** Any change
> to a protected module's `licenses`, `does_not_license`, `comparison_set`,
> `additional_caveats` content, or `ai_status` semantics — that is, any delta inside a
> `_claim_license*`/`ClaimLicense(...)` call-site subtree, exactly as
> `tools/check_claim_license_guard.py` already identifies those subtrees to seed the
> closure — fails CI with no override, full stop. A change confined to a protected
> module's code **outside** those subtrees is eligible only for the structurally
> verified additive exemption defined in `specs/svp-hermetic-mode-amendment.md` §2 —
> never for an author-asserted, data-driven, or text-substitution override of any shape
> (see that amendment's record of the closed `moves[].old_symbol` channel). A genuine
> semantic content change, inside a protected subtree, must still be a later,
> separately reviewed PR under its own contract.

Everything else in §3 is unchanged: whole-module comparison remains the default; the
merge-base-only, Git-object comparison is unchanged; P1's empty move map is unchanged;
CI still fails if the merge base is unavailable; PR-body prose is still irrelevant.

**The exemption is fully mechanical: no manifest, no author assertion, no opt-in flag.**
The checker evaluates it automatically for every protected module that would otherwise
fail the whole-module diff, before failing. There is nothing for a PR author to fill in
— no YAML row, no `--override` flag, no `moves[]` entry — because the entire point of
the prior finding is that anything an author can *assert* is something an author can
assert falsely. The checker derives everything from the two ASTs (merge-base and
candidate) it already has open for the comparison it's already making.

**Definitions.** For a protected module present at path `p` in both trees, let `B` and
`C` be its merge-base and candidate ASTs. Let `PS(T)` be the ordered subtree list
`_claim_license_relevant_subtrees(T)` — the exact function the guard already runs to
*seed* the closure (no new code, reused as-is). The module qualifies for the exemption
iff all five clauses hold; any single failure falls through unchanged to today's
whole-module hard fail — no partial credit, no "mostly additive":

1. **Zero drift inside every claim-license subtree.** Match each entry of `PS(B)` to an
   entry of `PS(C)` by stable anchor — the enclosing `_claim_license*`-named
   function/assignment's own name, or (for a bare `ClaimLicense(...)` call site) the
   name of its immediately enclosing function — never by raw list position, which
   `ast.walk`'s traversal order can shift when unrelated statements are inserted
   elsewhere in the module. `PS(B)` and `PS(C)` must have the same cardinality and every
   matched pair's `ast.dump(..., include_attributes=False)` must be byte-identical. An
   unmatched entry on either side, or any dump mismatch, fails the module out of the
   exemption immediately — this clause alone is what makes it impossible for a license
   string, `does_not_license` sentence, `comparison_set` key, `additional_caveats`
   entry, or `ai_status` branch to change and still qualify.
2. **Named-definition pass.** For every top-level `FunctionDef`/`AsyncFunctionDef`/
   `ClassDef` present in both `B.body` and `C.body` under the same `(kind, name)`: if
   its signature-bearing fields (`args`, `decorator_list`, `bases`, `keywords`,
   `returns`, each dumped with `include_attributes=False`) differ at all, the module
   fails the exemption — a signature change is a modification, not an insertion, and is
   never additive by construction. If the signature is unchanged, recurse this entire
   §2 procedure (clauses 2–5, scoped to that def's own body) one level in; matched pairs
   are then removed from both statement lists before clause 3.
3. **Insertion-only at every remaining level.** For the top-level statement lists (and,
   recursively, every matched-def body from clause 2) with matched named-definitions
   removed, diff the two `ast.dump(...)` string lists (e.g.
   `difflib.SequenceMatcher.get_opcodes()`); every opcode must be `equal` or `insert` —
   never `delete` or `replace`. This is the clause that admits the real hermetic-mode
   diff: a brand-new sibling `if _hermetic_stub_active(): raise ...` statement inserted
   immediately before an existing `try:` block, inside an otherwise-untouched `_load()`,
   is a pure insertion at this level — nothing existing is deleted, reordered, or
   rewritten.
4. **Module-level insertions are declaration-shaped only.** Any statement `insert`-
   classified by clause 3 **at module top level** (not inside a recursed function/class
   body, where any statement shape is permitted, since it only executes when that
   function is called) must be an `Import`, `ImportFrom`, `FunctionDef`,
   `AsyncFunctionDef`, `ClassDef`, or `Assign`/`AnnAssign` binding only brand-new names
   (clause 5). A bare expression-statement, augmented assignment, subscript- or
   attribute-assignment, `Global`, `Nonlocal`, or `Delete` inserted at module level
   fails the module out of the exemption — these are exactly the import-time
   side-effect shapes (mutating a shared object a protected subtree later reads,
   without ever "modifying" or "shadowing" its binding) that clauses 1–3 alone do not
   close, and they are cheap to insert if left open.
5. **No shadowing, no dynamic dispatch.** No name introduced anywhere under clauses 3–4
   may coincide with a name already bound at module scope in `B` — this is what stops a
   new statement from silently rebinding `ClaimLicense`, an existing supplier alias, or
   any existing constant the guard already trusts. No inserted statement or inserted
   function body may call `eval`, `exec`, `compile`, `__import__`,
   `importlib.import_module`, `getattr`/`setattr` with a non-literal attribute
   argument, or contain a star import — the same "fail closed on unresolved
   provenance" posture §3 already applies to the seed/supplier resolution itself,
   applied here to newly inserted code.

A module that satisfies all five clauses is exempt from the whole-module hard fail; the
checker instead emits a passing, auditable line — e.g. `judge_backends.py: additive-only
delta (3 new module-level statement(s); zero drift in 0 claim-license subtree(s) — not
a seed)` — so the exemption's use is visible in every CI run, not silent. A module that
fails any clause gets **exactly today's** error, unchanged: `"whole-module AST differs
from the merge-base version — P1 authorizes no claim-license or relocation delta."`

## 3. General form achieved — and its one honest limit

This is the general structural form, not a fallback. It is not scoped to
`judge_backends.py`, `embedding_backend.py`, or `surprisal_backend.py` by name; it
applies to any protected module, present or future, seeded or supplied, under any
future closure the live AST graph produces. It directly answers the owner principle
this amendment was asked to apply: the guard's blanket "no override" was never meant to
forbid a purely additive, non-license edit to a construction-boundary module — it was
meant to forbid exactly one thing, a silent semantic license change — and clause 1 is
that one thing, expressed as a standalone, independently-checkable rule rather than
folded into "no AST delta at all."

**Honest limit.** Clauses 2–3's insertion-only diff is *conservative by construction*: a
PR that is genuinely additive everywhere it touches a protected module, but also makes
one unrelated *modification* elsewhere in the same module (a rename, a reordered
statement, a changed default), does not qualify — the whole module falls through to the
hard fail, and the two changes must ship as separate PRs. That is a process cost, not a
soundness gap; it is the same "fails closed rather than guessing" posture §3 already
applies everywhere else, and it is strictly safer than a partial-credit scheme that
tries to approve the additive half and flag the rest. No clause here accepts an
author's claim that a change "is additive" — every clause is computed from the two
committed ASTs, exactly as the existing whole-module comparison already is.

## 4. Acceptance gates for the hermetic-mode PR

In addition to inheriting `specs/svp-packaging-conversion.md`'s general acceptance-gate
conventions (violations are errors and exit non-zero by default; an exemption is never
silent; `--strict` rejects expired/unmatched rows — not applicable here, since §2 takes
no rows at all), the hermetic-mode PR must clear:

1. **The §2 exemption covers the three construction-boundary edits and nothing else.**
   `judge_backends.py`, `embedding_backend.py`, `surprisal_backend.py` each pass all
   five §2 clauses against `origin/main` at PR time; `tools/check_claim_license_guard.py`
   reports the additive-only audit line for each, and the whole-module hard fail does
   not fire. A unit-test pair per clause is required: one fixture proving the real
   hermetic-mode shape (new sibling `if`/`raise` before an existing `try:`, new
   module-level `import os` + a new `_HERMETIC_ENV_VAR` constant + a new
   `_hermetic_stub_active()` function) passes; a second, adversarial fixture that
   changes one character inside an existing `_claim_license` function's `does_not_license`
   string in the *same* module, alongside an otherwise-identical additive edit, fails —
   proving clause 1 isn't accidentally satisfied by clauses 2–3's leniency. A third
   fixture inserts a module-level statement that mutates an existing dict a protected
   subtree reads (`SOME_TABLE["k"] = "v"` as a new top-level statement) and must fail
   under clause 4.
2. **Two live defects found in the original build are not re-earned.**
   a. **Bypass modules.** `voice_fingerprint.py` constructs `AutoTokenizer.from_pretrained`
      / `AutoModel.from_pretrained` directly in its **own** `_load()`-shaped methods
      (three separate construction sites, confirmed at `plugins/setec-voiceprint/scripts/voice_fingerprint.py:204`,
      `plugins/setec-voiceprint/scripts/voice_fingerprint.py:328`, and
      `plugins/setec-voiceprint/scripts/voice_fingerprint.py:420`) — none of which route
      through `surprisal_backend.py`, so `SETEC_HERMETIC_BACKEND=stub` does not stop
      them. The same own-construction shape, confirmed present on `origin/main`, also
      exists in: `plugins/setec-voiceprint/scripts/edit_magnitude_audit.py`
      (`AutoTokenizer.from_pretrained` / `AutoModelForSequenceClassification.from_pretrained`
      at lines 138/140), `plugins/setec-voiceprint/scripts/variance_audit.py` (a second,
      *separate* unguarded bypass at `_get_st_model()` — `SentenceTransformer("all-MiniLM-L6-v2")`
      constructed directly at `plugins/setec-voiceprint/scripts/variance_audit.py:622`,
      distinct from its own already-guarded `_get_embedding_backend()` path that
      correctly routes through `embedding_backend.py`), and
      `plugins/setec-voiceprint/scripts/biber_features.py` (a lazy `import torch` /
      `import transformers` pair for its Neurobiber tagger, around
      `plugins/setec-voiceprint/scripts/biber_features.py:352`). Each site needs one of
      two outcomes, chosen per site and stated in the PR, not silently defaulted: (i)
      route it through the corresponding guarded backend's construction boundary so
      `SETEC_HERMETIC_BACKEND=stub` actually stops it, or (ii) leave it unguarded and
      record it — by name, file, and line — as a known-uncovered gap in the hermetic
      gate's own report/docstring, so "hermetic" is never claimed for a surface it
      doesn't actually cover. Silence (neither routing nor recording) fails review.
      `plugins/setec-voiceprint/scripts/calibration/train_edit_magnitude.py` currently
      only *mentions* `AutoTokenizer.from_pretrained`/`AutoModelForSequenceClassification.from_pretrained`
      in a comment describing planned behavior (`plugins/setec-voiceprint/scripts/calibration/train_edit_magnitude.py:212-213`)
      — the PR must re-check its state at build time, since a comment today does not
      guarantee the shape is still absent by the time this amendment builds.
   b. **`ai_status` assertions that cannot fail.** The consumer-case fixture's
      `expected_ai_status` field must not be pinned to `null`/`None` for every row while
      the check reads it as `if "expected_ai_status" in case and envelope.get("ai_status")
      != case["expected_ai_status"]`. A pinned `null` and an absent `ai_status` key are
      indistinguishable there — `None != None` is always `False`, so the assertion
      passes whether the envelope actually carries `ai_status: null` or omits the key
      entirely, and can never fail regardless of what the real envelope contains. The
      fix must distinguish the two: either require every fixture row to name an actual
      expected `ai_status` value the real envelope should carry (not a placeholder), or
      replace the sentinel with an explicit `expected_ai_status_present: bool` alongside
      a separate presence check (`"ai_status" in envelope`) that a truly-absent key
      fails and a truly-`null`-valued key passes. Whichever shape is chosen, the PR
      must include a fixture where the real envelope's `ai_status` is deliberately wrong
      and prove the case row fails — a red test proving the green path isn't vacuous,
      the same falsification discipline the rest of this codebase's gates already carry.
3. **Poisoned-`sys.modules` regression coverage.** Each of the three guarded backends
   (`judge_backends.py`, `embedding_backend.py`, `surprisal_backend.py`) keeps its own
   test proving `SETEC_HERMETIC_BACKEND=stub` raises before the provider SDK /
   `sentence_transformers` / `transformers` import is even attempted, using a
   sentinel module/meta-path finder that raises `AssertionError` if touched — and a
   companion test proving the stub is off by default (`_hermetic_stub_active()` is
   `False` with the env var unset). Any newly-routed bypass site from gate 2(a) gets the
   same pair.
4. **The claim-license guard's own test suite gains the §2 exemption's negative
   space.** Beyond the fixtures in gate 1, at least one existing whole-module-hard-fail
   regression test (proving a genuine semantic change to a protected module still fails
   with no override) must keep passing unmodified — the exemption must be additive to
   the guard's existing behavior, never a replacement of it.
5. **`packaging_move_map.json` stays exactly `{"schema":1,"moves":[],"path_rewrites":[]}`.**
   The hermetic-mode PR authorizes zero relocation rows; it is a pure in-place edit to
   three already-resident files. If the guard's `load_move_map` or `compare_protected_modules`
   ever needs to change to support §2, that change must be reviewed as part of this PR
   and must not resurrect any row shape resembling `old_symbol`/`new_symbol` text
   substitution — see §1's record of why that shape is permanently closed.

## 5. Fixture requirement: the honest bare-copy, not a synthetic parent

The hermetic gate's own scratch-copy fixture must build on
`tools/check_zero_install.py`'s bare-copy mechanism — `shutil.copytree` of
`plugins/setec-voiceprint/` directly to a scratch root, with **no synthetic `plugins/`
parent directory reconstructed around it**. The original build's scratch-copy gate
(`build/svp-hermetic-mode`'s `tools/check_hermetic_scratch_gate.py`) recreated the
two-level `plugins/<name>/` nesting inside its scratch dir specifically because
`setec_run.py` derives `REPO_ROOT` as `PLUGIN_ROOT.parent.parent` — a fixture-shaping
choice a prior P1 finding rejected: the spec's own invariant is that a **bare** copied
`plugins/setec-voiceprint/` subtree (no reconstructed grandparent) must work, and a
fixture that quietly reconstructs the very structure the real "copy this to a foreign
machine" scenario doesn't have was validating the wrong claim. `check_zero_install.py`
already carries the corrected, honest form (bare copy, `symlinks=False`,
repository-relative prefix stripped and rejoined onto the bare root); the hermetic
gate's 21-case consumer matrix must run each case's `setec_run.py <id> <argv> --json`
dispatch against that same bare-copy fixture — not a new, separately maintained scratch
mechanism, and not the synthetic-parent shape. Reusing `check_zero_install.py`'s
existing bare-copy helper (extended, not duplicated) keeps the two gates from silently
diverging on what "zero-install" actually means.

## Sequencing

This amendment authorizes phase **P1-hermetic**: lands after packaging P1 (already
merged) and this document (reviewed independently), before or in parallel with P2–P5 —
it touches none of their files. P1-hermetic's own PR:

- Edits exactly `plugins/setec-voiceprint/scripts/judge_backends.py`,
  `plugins/setec-voiceprint/scripts/embedding_backend.py`,
  `plugins/setec-voiceprint/scripts/surprisal_backend.py` for the construction-boundary
  stub (§4.1), plus whichever bypass sites from §4.2(a) are chosen for routing.
- Extends `tools/check_claim_license_guard.py` with the §2 exemption (five clauses,
  no manifest, no new CLI flag).
- Lands (or extends) the consumer-case gate per §5, with the §4.2(b) fix.
- Does not touch `packaging_move_map.json`'s emptiness, `pytest.ini`, layering, or any
  P2–P5 file.

## Out of scope

- Any change to what a `ClaimLicense` block may say — this amendment authorizes zero
  semantic license changes; §2 clause 1 is exactly the boundary that stays untouched.
- Generalizing the §2 exemption into a standing, always-on relaxation of the whole-module
  rule for non-protected modules — it only ever applies to a module already in the
  protected closure, and only ever removes the *no-override* consequence, never the
  closure computation itself.
- Resolving whether the env var should be `SETEC_HERMETIC_BACKEND` (this codebase's
  all-caps convention) or the spec-prose's lowercase form — carried over as an open flag
  from the original build; the implementing PR states its choice and why.
