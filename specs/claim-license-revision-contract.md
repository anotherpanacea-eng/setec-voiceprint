# Claim-license revision contract

> A sanctioned, reviewed path for deliberately CHANGING claim-license content
> (`licenses`, `does_not_license`, `comparison_set`, `additional_caveats`,
> `ai_status`-routed caveats), asymmetric by direction, that closes the
> current gap: the existing freeze guard blocks every content change with no
> override, including safety fixes.

- **Status:** Draft
- **Repo:** `setec-voiceprint`
- **Kind:** process/governance spec (like `specs/svp-packaging-conversion.md`),
  not a capability spec — this introduces no new `task_surface`, no
  `capabilities.d` entry, and no production code. It is the contract a later
  build PR implements against.
- **Depends on:** `specs/svp-packaging-conversion.md` §3 (the freeze lock this
  contract adds an explicit, narrow override channel to — non-destructively;
  the freeze default is preserved for anything not covered by an approved
  artifact) and reuses `tools/check_claim_license_guard.py`'s existing
  git-objects-only comparison machinery.
- **Companion evidence base:** the 108-call-site claim-license inventory and
  consumer-impact research this spec was built from (source-channel
  classified, reproduced independently below — see §0 and the Appendix).

## 0. The problem, verified against `origin/main`

`tools/check_claim_license_guard.py` runs unconditionally in CI (wired at
`.github/workflows/tests.yml:79-85`, the "Packaging P1 gates" step, `if:
always()`), comparing the candidate tree against the fetched merge base
using Git objects only. Per its own docstring and `specs/svp-packaging-conversion.md`
§3: **"No semantic claim-license change is authorized by this packaging
spec. Any canonical change fails CI with no override."** The protected set
is whole-module: every script that directly calls `ClaimLicense(...)` or
defines a `_claim_license*`-named symbol is a seed
(`tools/check_claim_license_guard.py:19-33`), and once a module is
protected, the WHOLE module's AST is compared, not just the seeding symbol
(`tools/check_claim_license_guard.py:540-547`,
`compare_protected_modules`). `plugins/setec-voiceprint/scripts/claim_license.py`
itself is confirmed protected — it is asserted as a member of the protected
set in `plugins/setec-voiceprint/scripts/tests/test_check_claim_license_guard.py:38-40`
— because `with_state_caveats` constructs `ClaimLicense(...)` directly
(`plugins/setec-voiceprint/scripts/claim_license.py:426,439`). Every
judge-backed audit script in defect 2 below is independently a seed for the
same reason. **There is no mechanism today by which a deliberate,
reviewed content change can land** — the guard cannot distinguish a
safety fix from a softening attempt, so it blocks both.

Two confirmed defects are stuck behind this gap:

### Defect 1 — silent missing caveat on the owner's own material

`plugins/setec-voiceprint/scripts/normalize_author_registry.py:28-34`
(`ALLOWED_AI_STATUS`) allows 9 `ai_status` values — 7 taxonomy states plus
two "historical personal-manifest labels," `mixed_pre_and_post_ai` and
`post_june_2025_uncertain` (comment at line 31: "review-or-exclude for
training"). `plugins/setec-voiceprint/scripts/claim_license.py`'s
`TARGET_STATE_CAVEAT_TEMPLATES` (lines 266-313) covers exactly the other 7:
`pre_ai_human`, `ai_generated`, `ai_generated_from_outline`, `ai_assisted`,
`ai_edited`, `mixed`, `unknown`. `state_routed_caveats()`
(`plugins/setec-voiceprint/scripts/claim_license.py:365-386`) does:

```
tmpl = TARGET_STATE_CAVEAT_TEMPLATES.get(target_ai_status)
if tmpl is not None:
    caveats.append(tmpl)
```

On a miss — i.e. exactly the two historical labels — this appends
**nothing**. No error, no fallback, no signal to the operator. Compare the
comparison-side sibling, `_comparison_caveat()`
(`plugins/setec-voiceprint/scripts/claim_license.py:332-362`), which DOES
have a generic fallback for an unrecognized single state (lines 356-362).
The asymmetry is the bug: one side degrades gracefully, the other side goes
silent.

12 production surfaces route `target_ai_status` through
`with_state_caveats`/`state_routed_caveats` and are therefore all exposed to
this silent gap: `general_imposters.py`, `mimicry_cosplay_audit.py`,
`semantic_preservation_check.py`, `surface_disagreement_resolver.py`,
`stance_modality_audit.py`, `discourse_move_signature.py`,
`confounder_audit.py`, `construction_signature_audit.py`,
`punctuation_cadence_audit.py`, `adversarial_robustness_card.py`,
`calibration/paraphrase_ladder.py`, `calibration/pan_replay.py` (verified by
grep for `target_ai_status=` inside each; a 13th caller,
`setec_run_set.py`, calls `with_state_caveats` without a `target_ai_status`
argument and is not exposed).

### Defect 2 — undisclosed self-evaluation risk

Six surfaces emit a judge-backend caveat when `judge_kind == "agent_host"`.
Five carry the disjointness clause — the judge model must differ from the
generator model — verbatim or near-verbatim:
`plugins/setec-voiceprint/scripts/warrant_probe.py:173-182`,
`plugins/setec-voiceprint/scripts/argument_decision_audit.py:619-627`,
`plugins/setec-voiceprint/scripts/argquality_dimension_profile.py:195-204`,
`plugins/setec-voiceprint/scripts/fallacy_scan.py:179-189`, and
`plugins/setec-voiceprint/scripts/narrative_decision_audit.py:437-444`. Each
reads, in substance: *"The identity is recorded as
`agent_host:<host>:<model>` so a consumer can assert it is disjoint from
any generator it validates (the consumer's drift gate must enforce judge
model != generator model on holdout/selection surfaces; see
`specs/35-host-delegated-judge.md`)."*

`plugins/setec-voiceprint/scripts/agd_move_scan.py:170-175` is the sole
holdout — it has the same `elif judge_kind == "agent_host":` branch but its
caveat is strictly shorter and omits the disjointness sentence entirely:

```
elif judge_kind == "agent_host":
    caveats.append(
        "Judge backend is `agent_host` — the inventory was produced by the "
        "HOST runtime's model (see judge.judge_identity.host), not a pinned "
        "API model@revision; NON-DETERMINISTIC and host-version-fluid."
    )
```

A consumer reading this surface's output has no textual signal that it must
verify judge/generator disjointness — the exact risk the other five surfaces
disclose.

Both defects are **additive fixes to `does_not_license`-shaped text** — one
adds caveat coverage for two previously-uncovered states, the other adds one
sentence that five sibling surfaces already carry. Neither weakens anything.
Both are exactly the case this contract exists to make cheap.

## 1. Governing rule

The freeze default in `specs/svp-packaging-conversion.md` §3 stands
unmodified for anything not explained by an approved revision artifact: an
unexplained delta in a protected module still fails CI with no override,
exactly as today. This contract adds one narrow, mechanical override
channel: **a delta is authorized if and only if it is fully and exactly
reconstructed by applying one or more approved, committed revision
artifacts to the merge-base source.** CI regenerates the "old" side from
the fetched merge base via `git show` — never from a PR-writable file — the
same posture `tools/check_claim_license_guard.py` already uses. PR-body
prose authorizes nothing, matching the existing rule
(`specs/svp-packaging-conversion.md` §3: "PR-body prose is irrelevant").

## 2. The revision artifact

### 2.1 Storage: one file per revision, drop-in

Each revision is its own committed JSON file at
`plugins/setec-voiceprint/claim_license_revisions/<revision-id>.json`,
`revision-id` = `<yyyy-mm-dd>-<slug>` (filename doubles as the id). This
mirrors two existing, real conventions in the same repo rather than
inventing a third: the drop-in fragment pattern of
`plugins/setec-voiceprint/scripts/claim_license_surfaces/` ("Drop in a new
file — do not edit a shared dict or list," per its own README, so parallel
PRs never collide on one insertion point) and the top-level authorization
file `plugins/setec-voiceprint/packaging_move_map.json` (a committed,
CI-diffed JSON authorization the guard already knows how to read). A
directory of independent files, not one accumulating ledger, avoids merge
conflicts between two unrelated in-flight revision PRs — the same reason
`claim_license_surfaces/` is drop-in rather than a shared dict.

Placing the directory under `plugins/setec-voiceprint/` (a sibling of
`scripts/`, not inside it) keeps every artifact a plain `.json` file outside
`build_protected_set`'s `scripts_root.rglob("*.py")` walk
(`tools/check_claim_license_guard.py:342`) — artifacts are never
themselves candidates for AST protection, and adding one can never change
the protected-module set.

### 2.2 Schema

```json
{
  "schema": 1,
  "revision_id": "2026-08-06-target-state-caveat-coverage",
  "changed_symbols": [
    "plugins/setec-voiceprint/scripts/claim_license.py::TARGET_STATE_CAVEAT_TEMPLATES",
    "plugins/setec-voiceprint/scripts/claim_license.py::state_routed_caveats"
  ],
  "hunks": [
    {
      "file": "plugins/setec-voiceprint/scripts/claim_license.py",
      "old_text": "<exact byte-for-byte substring, verified present in the merge-base file>",
      "new_text": "<exact byte-for-byte substring, verified present in the candidate file>",
      "field_label": "TARGET_STATE_CAVEAT_TEMPLATES (dict literal)"
    }
  ],
  "direction": "narrow",
  "rationale": "Two ai_status values accepted by normalize_author_registry.py's ALLOWED_AI_STATUS silently produced zero caveat text. Adds coverage; removes nothing.",
  "review_track": "cheap",
  "owner_signoff": null
}
```

Field notes:

- **`hunks`** — one or more `(file, old_text, new_text)` triples. A single
  revision may span multiple hunks in one file (the state-routed-caveats
  fix needs two: the dict addition and the fallback fix) or multiple files
  (a true cross-file revision). `old_text` and `new_text` are exact
  substrings, not diffs or line ranges — byte-for-byte, so the
  reconstruction check in §2.3 is unambiguous.
- **`changed_symbols`** — dotted `file::name` identifiers naming exactly
  what changed, used mechanically in §6 to auto-derive the affected-surface
  list. Not free text; each entry must resolve to a name defined in the
  named file (CI-checkable the same way `check_claim_license_guard.py`
  already resolves AST names).
- **`direction`** — the author's claim. CI **independently recomputes**
  this from the hunks (§3) and hard-fails on mismatch. The field exists so
  the artifact is human-legible and so a mismatch has something concrete to
  disagree with; it never gates anything by itself.
- **`review_track`** — `"cheap"`, `"heavy"`, or `"migration"`, likewise
  recomputed, never trusted.
- **`owner_signoff`** — `null` for a cheap (narrow) revision; a structured
  object for a heavy (widen) one (§4.2). Its presence/absence is itself
  part of what CI cross-checks against the computed `direction`.

### 2.3 The reconstruction check (the actual override mechanism)

For every protected module with a merge-base/candidate AST delta
(`tools/check_claim_license_guard.py`'s existing `compare_protected_modules`
loop), the extended guard:

1. Collects every revision-artifact file present in the candidate tree but
   **absent at the merge-base SHA** (same git-objects-only posture as
   today — `git show <merge_base_sha>:plugins/setec-voiceprint/claim_license_revisions/` listing,
   analogous to `list_tree_py_files` already used for scripts). Only
   artifacts new in this PR count; already-landed revisions are already
   baked into the merge-base content and produce no delta to explain.
2. Filters to artifacts whose `hunks` name this module's path.
3. For each such hunk, verifies `old_text` occurs **verbatim, exactly
   once** in the merge-base file content (`git show <merge_base_sha>:<path>`)
   — ambiguous or absent `old_text` is a hard fail (fails closed, same
   posture as an unresolved star import in the existing guard).
4. Applies all matched hunks for that file, in the order listed, to the
   merge-base content: `old_text` → `new_text`, non-overlapping spans only
   (overlapping hunks are a hard fail — a human must re-express them as one
   hunk).
5. Compares the reconstructed text to the actual candidate file content
   **byte-for-byte** (full-file equality, strictly stronger than the
   existing AST-dump comparison — it also catches a whitespace-only change
   riding along uncited).
6. If reconstruction equals the candidate exactly: this module's delta is
   authorized; skip the AST-diff failure for it. If not: the guard fails
   exactly as it does today, with no override — an unexplained residual
   delta (planted softening, or an honest hunk transcription error) is
   caught precisely because it isn't reconstructed by anything approved.

This is additive to, not a replacement for, the existing check: the AST
comparison still runs first to find deltas; the reconstruction check only
explains deltas that exist, and any protected module with zero committed,
matching artifacts still fails with the original message. **Silent
softening remains impossible** — a change not covered by an approved
artifact fails exactly as today, because step 3's exact-match requirement
means an artifact can't be written loosely enough to cover a
different, unreviewed edit.

## 3. Direction classification — mechanical, not author-asserted

The task's hardest honesty requirement: `direction` must be **recomputed by
CI from the hunks**, not trusted from the artifact's own field. Several hunk
shapes, several rules — §3.1 below was revised after an adversarial review
found the original sentence-superset rule unsound; the finding and the fix
are recorded in place rather than silently folded in, because the failure
mode is instructive for every other rule in this section.

### 3.1 Refusal-shaped text — the co-occurrence hazard

**The hole (found in review, confirmed against this spec's own text).** The
first draft of this rule was: tokenize `old_text`/`new_text` into sentences
and call it narrow whenever `S_old ⊆ S_new` — every old sentence survives,
only new ones are added. That rule is unsound. Sentence-set inclusion can
only see *membership*; it cannot see a *relation* between sentences. An
added sentence that **quantifies over** the existing ones defeats it
completely:

```
old:  "This surface does not license an authorship verdict."
      "Thresholds are operator-side and PROVISIONAL."

new:  (both sentences above, retained verbatim)
      + "These limitations describe the uncalibrated default; with a
         supplied baseline the output may be read as provisional
         evidence of AI provenance."
```

`S_old ⊆ S_new` holds — nothing old is removed — yet the added sentence
grants back, conditionally, exactly what the two old sentences refuse. The
old superset-only rule would compute **narrow** and route this to the cheap
track with no owner sign-off. It does not, because the rule below replaces
it. No clause in the original §3.1–§3.3 stopped this: §3.2 (licensing text)
and §3.3 (restructure) are different code paths entirely, and §3.5's
mismatch check only catches a *self-declared* direction disagreeing with
the (unsound) computed one — it does nothing if the computation itself is
wrong. The gap was real.

**The fix — bounded refusal vocabulary, with a structural fast path.**
Considered and rejected: (a) escalate on scope-marker keywords
("unless"/"except"/"provided that"/etc.) alone — rejected as the *sole*
mechanism because a keyword blocklist is evadable by paraphrase (the
example above uses none of those words) and, independently, has real false
positives (a legitimate strengthening caveat like "treat as unknown-
equivalent unless a per-section breakdown is available" is not an
undercut, and would still wrongly get escalated by a broad blocklist). (b)
force every refusal-field addition to `widen` — rejected outright: it is
unevadable but it makes both worked examples in §5 expensive, which was the
explicit failure condition this contract was built against. **Chosen: (c),
a bounded vocabulary with a structural fast path for the case where
co-occurrence is provably impossible**, described as three tiers below,
plus a narrow keyword backstop retained from (a) — not as the mechanism,
but as a cheap, honestly-caveated defense-in-depth layer.

**Dependency direction, stated explicitly**: §7's coming vocabulary
consolidation is what eventually makes Tier 2 (below) the common case
instead of the exception — once most refusal/caveat prose is drawn from a
shared, individually-vetted template set, MOST future additions become
template instantiations, which are mechanically verifiable by construction.
That means the consolidation work in §7 *strengthens* this contract as it
proceeds (more of the corpus becomes cheaply, soundly narrow-able) rather
than straining it, and this section's Tier 3 default-to-widen bucket should
shrink over time rather than grow. This contract does not wait for that
work to land — Tier 1 and the existing shipped-text corpus already make
both worked examples cheap today (§5) — but the design is chosen so
consolidation pays down Tier 3, not so consolidation is required to make
the contract usable.

**Decision procedure.** First, the structural check that survives from the
original rule, unchanged: compute `S_old`, `S_new` as before. If `S_old`
is **not** a subset of `S_new` — any old sentence missing, reworded, or
reordered-with-wording-changed — the hunk is **widen** immediately; no
tier analysis runs, exactly as the original rule already said for this
case, and it was never the unsound part. If `S_old == S_new` (set-equal,
only formatting differs), route to the restructure check (§3.3) as before.
**Only when `S_old ⊊ S_new`** — a strict, pure addition, which is exactly
the case the original rule got wrong — do the *added* sentences
(`S_new − S_old`) each get classified individually by Tier 1/2/3 below.
The hunk's direction is the worst of its added sentences' individual
classifications: if every added sentence is Tier 1 or Tier 2, the hunk is
narrow; if any added sentence is Tier 3 (or trips the backstop), the hunk
is widen.

#### Tier 1 — mutually exclusive selection (structural exemption)

If the hunk adds a key to a `dict[str, str]`-shaped (or
`dict[frozenset[str], str]`-shaped) template table, and **every** reference
site to that table — resolved the same way `build_protected_set` already
resolves plugin-local name references
(`tools/check_claim_license_guard.py:305-439`) — performs only a
single-key lookup (`.get(k)`, `d[k]`, or an `in`-membership test followed by
a single subscript) and never iterates, joins, or concatenates multiple
values from the table in one call, then an added entry can **provably never
co-occur** with any existing entry in a single rendered output — there is no
call path by which two entries from the same table appear in the same
document, so a new entry cannot quantify over an old one no matter what it
says. Narrow, unconditionally, regardless of the new entry's wording.

Verified today: `TARGET_STATE_CAVEAT_TEMPLATES`
(`plugins/setec-voiceprint/scripts/claim_license.py:266-313`) has exactly
one reference site, `TARGET_STATE_CAVEAT_TEMPLATES.get(target_ai_status)`
at `plugins/setec-voiceprint/scripts/claim_license.py:380` — a single-key
lookup, nothing else. `COMPARISON_STATE_CAVEAT_TEMPLATES`
(`plugins/setec-voiceprint/scripts/claim_license.py:316-329`) has two
reference sites, both in `_comparison_caveat`
(`plugins/setec-voiceprint/scripts/claim_license.py:346-347`): an
`in`-membership test and a same-key subscript — also single-key. Both
tables qualify for Tier 1 today; this is not a hypothetical.

#### Tier 2 — verbatim template reuse

For a concatenated field (a single prose string such as `does_not_license`,
or one new item appended to an `additional_caveats`-shaped list — see the
list-specific note below), an added sentence is narrow iff it is a
byte-for-byte match — normalized whitespace only, plus a small,
**explicitly declared** set of surface-local parameter substitutions (a
task-surface name, a script name — never a clause boundary) — against a
sentence that **already exists verbatim in at least one other already-
shipped, protected file at the merge base**. Reusing language that has
already survived review in its original home cannot introduce a *new*
undercutting relationship that wasn't already possible there; the sentence
carries its prior scrutiny with it.

#### Tier 3 — everything else: widen

A genuinely novel sentence in a concatenated field — no existing
precedent, not a Tier 1 dict entry — is **widen**, full stop. This is the
residual bucket where "does this sentence quietly scope the paragraph
above it" is a real question only a human can answer; making it the
*default* rather than the *whole rule* is what keeps §5's worked examples
cheap without pretending the general case is mechanically decidable.

#### Backstop — license-granting-verb screen (applies to every tier, including Tier 1)

Independent of tier, an added sentence is escalated to widen if it contains
a **license-granting verb pattern** ("may be read as," "constitutes,"
"is evidence of," "should be treated as," "licenses," "entitles") — this is
deliberately narrower than a general exception-keyword blocklist (which
would false-positive on legitimate conditional caveats like "unless a
per-section breakdown is available"): a `does_not_license`/caveat field
exists specifically to *negate* license, so any sentence that *grants* it
back, even conditionally, is polarity-anomalous inside that field
regardless of paraphrase risk on the exact verb chosen. This is a
backstop, not the mechanism — stated honestly, it is still a keyword
screen and a sufficiently creative paraphrase with no listed verb ("a
supplied baseline shifts what this result supports") can still evade it.
It exists to catch the cheap, common case for free, not to close the gap
by itself; Tiers 1–3 are what actually closes it, because they never grant
the cheap path to a novel sentence in a co-occurring field regardless of
what the screen finds.

**Walking the attack diff through the fix**: the coordinator's example
sentence is (i) not a Tier 1 dict entry — `does_not_license` is a plain
concatenated string; (ii) not a Tier 2 match — no existing shipped file
carries this sentence verbatim; so it lands in Tier 3 → **widen** on
structural grounds alone, before the backstop is even consulted. The
backstop also independently trips ("may be read as" + "evidence of"),
which is redundant here but is the intended belt-and-suspenders posture:
Tier 3 does the real work, the screen catches obvious cases even faster
and flags them for the `owner_signoff.statement` to address by name. See
gate G7 in §9 for the required must-fail fixture.

**`additional_caveats` (list-shaped) carries the identical hazard and the
identical fix.** `additional_caveats` renders as a bulleted list
(`render_block`, `plugins/setec-voiceprint/scripts/claim_license.py:171-176`)
— every item co-occurs in the same block exactly like sentences within one
`does_not_license` string, so a new bullet can scope away an earlier one
just as readily as a new sentence can. A new list item is classified by
the same three tiers: Tier 1 does not apply to a list (nothing about a
Python list structurally prevents two items co-occurring — that is in fact
the whole point of a list), so every `additional_caveats` addition is
either Tier 2 (verbatim match against an existing caveat elsewhere in the
shipped corpus) or Tier 3 (widen).

**`comparison_set` — narrative values carry the same hazard; structured
values do not.** Most `comparison_set` values are short, non-narrative
(an id, a count, a hash, a fingerprint, an enum-like label) — adding such a
key is narrow by default, unchanged from this contract's original
treatment, because a bare identifier cannot narrate an exception. A value
that is itself a free-text/descriptive string, however — the kind of entry
that could say something like "results are only meaningful for baselines
collected after the January calibration" — carries exactly the same
co-occurrence risk as a caveat, because it sits in the same rendered
`### Comparison context` block as every other entry
(`_comparison_context_lines`,
`plugins/setec-voiceprint/scripts/claim_license.py:189-209`) and a reader
integrates it with the rest of the license block. A new `comparison_set`
key whose value is a narrative string (heuristic: longer than ~12 words, or
containing a verb) goes through the same Tier 1/2/3 pipeline as a
refusal-field addition (Tier 1 is inapplicable — `comparison_set` is a
plain dict assembled per-call, not a shared mutually-exclusive template
table); a short structured value does not. This is distinct from, and
additional to, §8.1's separate carve-out for the two specific keys
(`prompt_fingerprint_sha256`, `length_range_words`) that `setec-voicewright`
parses structurally — that rule protects a code consumer from a shape
change; this rule protects a human/model reader from a narrated exception.
Both can apply to the same hunk.

### 3.2 Licensing-shaped text (`licenses`)

Inverse polarity from 3.1 — this is what's *claimed*, not refused, so
removing content narrows exposure and adding content widens it.

- `S_new ⊆ S_old` and `S_new ≠ S_old` → **narrow** (a claim retracted,
  nothing new claimed).
- `S_old == S_new` → restructure candidate (§3.3).
- Otherwise (anything added, or any existing sentence reworded) → **widen**.

**Checked for the same hole and found immune**: `licenses` additions are
already `widen` by this rule — any addition at all fails the "`S_new ⊆
S_old`" test, since adding content to what's claimed is definitionally not
a subset of what was claimed before. There is no cheap path here for an
added sentence to exploit, quantifying or not; the vulnerability in §3.1
was specific to a field whose whole purpose is negation, where addition
looked safe and wasn't. `licenses`'s inverted polarity means addition was
never given the benefit of the doubt in the first place.

### 3.3 Restructure (semantic-neutral)

A hunk in either 3.1 or 3.2 whose sentence sets are set-equal but text
differs (reflow, reordering, moving a literal into a shared constant ahead
of the vocabulary-consolidation migration in §7) is not auto-approved by
set equality alone — set equality only proves the same sentences are
present, not that a reader-facing rendering is unchanged. It must
additionally pass a **golden-output equality proof**: render the affected
`ClaimLicense` block (or call the specific text-producing function) at
both the merge-base and candidate revisions of the code, over the same
representative input set, and assert byte-identical rendered output. The
repo already has the right oracle for this shape of check —
`plugins/setec-voiceprint/scripts/gen_contract_fixtures.py` — the fixture
generator "executes registered builders" against real code paths
(`specs/svp-packaging-conversion.md`, "Verified constraints" section); this
contract reuses that same executes-real-code-and-diffs-output posture
rather than inventing a new one. A restructure hunk that fails the
golden-output proof is reclassified: if it turns out to *add* uncovered
sentences, it's narrow; anything else is widen.

### 3.4 Code-level (helper) revisions

Not every fix is a string literal edit — `state_routed_caveats`'s fallback
fix (§6) changes control flow, not just text. For a hunk that edits a
function body rather than a string constant, the artifact must include a
**behavioral characterization proof**: run the merge-base and candidate
versions of the named function (checked out into an isolated scratch
import, the same "materialize the merge-base tree" technique
`build_protected_set_at_revision`
(`tools/check_claim_license_guard.py:551-566`) already uses for whole-tree
comparison) over an enumerated golden input domain, and diff the outputs.

- Direction is **narrow** iff, for every input already handled before the
  change, `old_output` is a subset/prefix of `new_output` (nothing already
  emitted is removed or altered), AND for every input NOT handled before
  (empty output), `new_output` is non-empty (strict, safe gain — never a
  silent-to-silent no-op stays silent).
- Any input where `old_output` is non-empty and NOT a subset of
  `new_output` → **widen**.

For `state_routed_caveats`, the golden input domain is exactly
`normalize_author_registry.ALLOWED_AI_STATUS`
(`plugins/setec-voiceprint/scripts/normalize_author_registry.py:28-34`, all
9 values) plus `None` — a small, enumerable, already-named set; the proof
is a parametrized test over 10 cases, not an open-ended claim.

**The subset/prefix check on `old_output ⊆ new_output` is necessary but
not sufficient**, for the identical reason §3.1's original rule was
insufficient: `state_routed_caveats` returns `list[str]`, and a code
change could add a *new list entry* for an already-handled input rather
than only adding entries for newly-handled ones. A new list entry is a new
co-occurring string exactly like a new `additional_caveats` bullet, so it
is independently subject to §3.1's Tier 1/2/3 pipeline — Tier 1 already
covers this specific fix (the new entries originate from
`TARGET_STATE_CAVEAT_TEMPLATES`, a Tier 1 table, so at most one such entry
is ever contributed per call; see §5.1) but a *different* future code-level
revision that appends a second, always-present sentence to every call's
output would not get Tier 1's exemption and must clear Tier 2 or 3 like
any other new sentence in a co-occurring field.

### 3.5 Mismatch handling

CI computes `direction` per hunk using 3.1-3.4, then combines: a revision's
overall direction is the **worst** (most cautious) of its hunks' computed
directions — `widen` if any hunk is widen, else `restructure` if any hunk
needed the golden-output proof, else `narrow`. If the artifact's
self-declared `direction`/`review_track` disagrees with the computed value,
CI fails with the computed value shown — the artifact must be corrected
and, if it's now `widen`, must gain an `owner_signoff` block before it can
pass again. **This is what makes the classification resist author
assertion**: the field is legible to a human reviewer, but it is never the
input to a review-weight decision — the hunks are.

## 4. Asymmetric review by direction

### 4.1 Narrow — cheap

Requirements, in full:

1. Reconstruction check (§2.3) passes.
2. Computed direction (§3) is `narrow`.
3. `rationale` is non-empty.
4. No file outside `plugins/setec-voiceprint/claim_license_revisions/` and
   the specific protected file(s) the hunks target may change in the same
   PR (mechanically checked against the PR's file list — a narrow revision
   bundled with unrelated code is not narrow anymore, it's an unreviewed
   surface).

That's it — reviewable and mergeable by any maintainer through ordinary PR
review, the same weight as any other doc/test PR. No owner sign-off, no
extra approvers, no waiting period. This is deliberately as cheap as the
two worked examples in §5 need it to be.

### 4.2 Widen — heavy

All of §4.1's requirements 1-2 (with `direction == "widen"`), plus:

1. **`owner_signoff` is a required, structured, hash-bound object**, not
   PR-body prose (the artifact is what CI diffs — never the PR
   description):

   ```json
   "owner_signoff": {
     "name": "Joshua Miller",
     "date": "2026-08-06",
     "statement": "Reviewed and approved: removing the pre-June-2025 caveat because <specific, falsifiable reason>.",
     "hunks_sha256": "<sha256 of the exact concatenated new_text values, in hunk order>"
   }
   ```

   CI recomputes `hunks_sha256` from the artifact's own `hunks` and rejects
   a mismatch. This binds the sign-off to the *exact* approved text — a
   sign-off can't be typed once and silently reused if a hunk is edited
   after the fact, and it can't be copy-pasted onto an unrelated widen.

2. **Scope restriction is stricter than 4.1's**: the PR may touch
   *nothing* besides the revision artifact and the exact hunked files —
   no test changes, no doc changes riding along, so a widen's review
   surface is exactly the text change plus the sign-off.
3. **A `changelog.d/` entry is required**, following the repo's existing
   unreleased-changelog convention (`tools/check_docs_freshness.py`'s
   changelog-coverage check already enforces this pattern for capability
   entries; this extends the same idea to widen-classified claim-license
   revisions specifically — it is a new, parallel check, not a reuse of
   the capability-id coverage rule, since a revision has no
   `capabilities.d` entry). The entry must name every surface whose
   rendered output changes.
4. **Non-vacuous justification**: `rationale` must state what changed and
   why the old text was wrong or the new text is required — a bare
   "approved" is rejected by a minimum-length + must-not-equal-template
   check (weak, but non-zero; see the weakest-point note in the final
   report).

**On identity, honestly**: the `owner_signoff` object proves *what* was
approved (hash-bound to the exact text) from git objects alone, in keeping
with the repo's git-objects-only posture. It does **not** cryptographically
prove *who* wrote it — a JSON field is typeable by anyone with write
access. This contract recommends, as a defense-in-depth layer outside its
own mechanical scope, adding a GitHub CODEOWNERS entry for
`plugins/setec-voiceprint/claim_license_revisions/` gated by branch
protection requiring the owner's approving review — but that is a
platform-side setting this spec cannot verify or enforce from the checked-
out tree, so it is a recommendation for the implementing PR, not a claim
this contract already satisfies identity verification.

### 4.3 Restructure

The golden-output proof from §3.3 IS the review requirement — a passing
proof (byte-identical rendered output across the representative input set)
is sufficient for the cheap path; a failing proof reclassifies the hunk
into narrow or widen per §3.3's fallback rule, which then carries that
track's requirements instead.

## 5. Worked examples

### 5.1 Defect 1 — missing `ai_status` caveat coverage

Artifact: `2026-08-06-target-state-caveat-coverage` (schema sketch in §2.2).

**Hunk 1** — `plugins/setec-voiceprint/scripts/claim_license.py`,
`TARGET_STATE_CAVEAT_TEMPLATES`: `old_text` is the exact dict literal
spanning lines 266-313 today (7 entries); `new_text` is the same 7 entries
plus 2 new key/value pairs for `mixed_pre_and_post_ai` and
`post_june_2025_uncertain`. Sentence-set check (§3.1): every sentence in
`old_text` reappears unchanged in `new_text`; two new entries' sentences
are net-new. → **narrow**.

Because `TARGET_STATE_CAVEAT_TEMPLATES` qualifies for **Tier 1** (§3.1 —
its sole reference site, `plugins/setec-voiceprint/scripts/claim_license.py:380`,
is a single-key `.get()` lookup, so no two entries can ever co-occur in one
rendered block), the two new entries are narrow regardless of their exact
wording — Tier 1 is a structural exemption, not a text check, so this hunk
does not need to clear Tier 2/3 or the backstop screen at all.

**Hunk 2** — same file, `state_routed_caveats`: `old_text` is the current
two-line no-op body (`tmpl = ...get(...); if tmpl is not None: ...`);
`new_text` is the fixed version (§6). This is a code-level hunk, not a
text hunk — classified per §3.4's behavioral-characterization rule: for
all 7 previously-recognized states, output is unchanged (`old_output ==
new_output`, trivially a subset); for the 2 new states, `old_output == []`
and `new_output` is non-empty. → **narrow**. §3.4's own caveat about new
list entries applies here too, but is satisfied by the same Tier 1 fact as
Hunk 1: the only new content this fix can ever emit into the returned list
is drawn from `TARGET_STATE_CAVEAT_TEMPLATES`, and at most one entry from
that table is ever contributed per call — no new co-occurring sentence is
introduced by this hunk beyond what Hunk 1 already cleared.

Combined direction (§3.5): worst of {narrow, narrow} = **narrow**. Review
track: cheap (§4.1). No owner sign-off. The PR touches exactly
`plugins/setec-voiceprint/claim_license_revisions/2026-08-06-target-state-caveat-coverage.json`
and `plugins/setec-voiceprint/scripts/claim_license.py`. Because this is
also a cross-surface (helper-level) revision, it additionally carries the
§6 auto-derived affected-surface enumeration in its `changelog.d/` entry —
required regardless of track for any revision whose `changed_symbols`
resolves to more than one caller (§6.3) — but that enumeration is
machine-generated, not hand-typed, so it adds no review burden.

### 5.2 Defect 2 — missing disjointness clause

Artifact: `2026-08-06-agd-move-scan-disjointness-clause`.

**Hunk 1** — `plugins/setec-voiceprint/scripts/agd_move_scan.py`,
lines 170-175: `old_text` is the current 3-sentence `agent_host` caveat
string; `new_text` is the same string with one sentence appended, **byte-
identical** to the disjointness sentence already shipped verbatim in four
sibling surfaces — confirmed by extracting each surface's `agent_host`
caveat string via AST and comparing them directly: *"The identity is
recorded as `agent_host:<host>:<model>` so a consumer can assert it is
disjoint from any generator it validates (the consumer's drift gate must
enforce judge model != generator model on holdout/selection surfaces; see
`specs/35-host-delegated-judge.md`)."* is byte-identical across
`plugins/setec-voiceprint/scripts/warrant_probe.py`,
`plugins/setec-voiceprint/scripts/argument_decision_audit.py`,
`plugins/setec-voiceprint/scripts/argquality_dimension_profile.py`, and
`plugins/setec-voiceprint/scripts/fallacy_scan.py` (the fifth surface,
`narrative_decision_audit.py`, carries the same disjointness content in
different wording — not a byte match, so it isn't cited as the Tier 2
precedent here; it's a fifth confirmation the *content* is standard, not a
fifth reusable string). Tier check (§3.1): not a dict entry (Tier 1
inapplicable — `does_not_license`-shaped string), and the appended
sentence is an exact match against an already-shipped sentence → **Tier
2, narrow**. It also clears the backstop screen (no license-granting verb
— the sentence discloses an identity format, it doesn't grant back any
inference the surrounding caveat refuses). Single-surface, single-hunk,
single-file. This is the cheapest possible case: cheap track, no
sign-off, one file besides the artifact itself.

Both worked examples land on the cheap path with zero owner sign-off and a
same-day mergeable PR under the **corrected** §3.1 mechanism — Tier 1 for
5.1, Tier 2 for 5.2 — not under the unsound sentence-superset rule the
first draft used. Neither example needed the superset rule's leniency: 5.1
is cheap because its table structurally cannot co-occur with itself, and
5.2 is cheap because its addition is a proven-safe, already-shipped
sentence, not because "it's just an addition." That distinction is the
whole fix. Confirming the design goal: **narrowing a claim must be at
least this cheap, or the design is wrong** — and now, must be at least
this *justified*, or a narrow label is just as dangerous as no check at
all.

## 6. The `state_routed_caveats` fallback fix and cross-surface handling

### 6.1 The fix

Recommended: a **generic fallback that mirrors `_comparison_caveat`'s**
existing pattern (`plugins/setec-voiceprint/scripts/claim_license.py:356-362`),
not a hard fail-closed exception. Reasoning: `ALLOWED_AI_STATUS`
(`plugins/setec-voiceprint/scripts/normalize_author_registry.py:28-34`)
legitimately includes the two uncovered labels — they are real registry
values a caller may legitimately pass, not corrupted input. Raising would
turn a disclosure gap into an availability outage for every one of the 12
callers whenever a legitimately-labeled target happens to carry one of
these two states, which is a worse failure mode than the one being fixed.
Illustrative shape:

```python
def state_routed_caveats(
    *,
    target_ai_status: str | None = None,
    comparison_ai_statuses: list[str] | set[str] | frozenset[str] | None = None,
) -> list[str]:
    caveats: list[str] = []
    if target_ai_status:
        tmpl = TARGET_STATE_CAVEAT_TEMPLATES.get(target_ai_status)
        if tmpl is not None:
            caveats.append(tmpl)
        else:
            caveats.append(
                f"Target's ai_status is `{target_ai_status}`, a recognized "
                f"registry label with no dedicated caveat template. Treat as "
                f"a lower-confidence reading; consult the registry entry's "
                f"own notes before drawing state-specific conclusions."
            )
    comparison_caveat = _comparison_caveat(comparison_ai_statuses)
    if comparison_caveat is not None:
        caveats.append(comparison_caveat)
    return caveats
```

This keeps the function total (never raises on a legitimate registry
value) while guaranteeing non-empty disclosure — the actual defect fixed.
A narrower, genuinely-unrecognized string (a typo, not a registry member)
still gets *some* caveat under this design rather than an exception; that
is an intentional choice to keep the helper total. If the maintainer later
wants a stricter fail-closed posture for values outside
`ALLOWED_AI_STATUS` specifically (as opposed to the two known, deliberately
covered labels), that is a second, separate revision — this contract does
not bundle two design decisions into one hunk.

### 6.2 Why this is the hardest case for the contract

The change lives in exactly one file (`claim_license.py`) — the guard's
AST comparison only ever sees that one module's delta, because the 12
calling surfaces' own source is untouched; they still call
`with_state_caveats(lic, target_ai_status=...)` unchanged. But the
*rendered output* of all 12 changes the next time each surface runs. A
per-surface revision-artifact model would let this land with a **single**
artifact touching a **single** file and nobody would be forced to notice
that 12 downstream reports are affected. That is the failure mode this
section exists to prevent, without turning a narrow, single-file fix into
an expensive one.

### 6.3 The mechanical answer: derive, don't ask

The artifact declares `changed_symbols` (§2.2) — dotted names, not a
hand-typed surface list. CI derives the actual caller set by the same
name-resolution technique `build_protected_set` already uses for supplier
closure (`tools/check_claim_license_guard.py:305-439`): grep/AST-walk the
full `scripts/` tree for load-references to each named symbol, exactly the
check already performed by hand for this spec (`grep -rl
"with_state_caveats\|state_routed_caveats"` against
`plugins/setec-voiceprint/scripts/*.py`, confirming the 12-surface list in
§0). The derived set is then:

- **Compared against nothing the human wrote** — there is no
  hand-maintained `affected_surfaces` field to fall out of sync. The tool
  computes it fresh from the candidate tree every run, so it can never be
  stale.
- **Required in the `changelog.d/` entry**, verbatim, auto-inserted by the
  same tooling rather than transcribed — a human copy-paste error in a
  12-item list is exactly the kind of mistake mechanization should absorb.
- **Not a review-weight escalator by itself.** A cross-surface revision
  whose computed direction (§3.5, still evaluated per-hunk — here, one
  code hunk under §3.4) is narrow stays on the cheap track. Fan-out changes
  who needs to know, not how hard it is to approve. A cross-surface
  revision whose computed direction is widen gets the full §4.2 treatment
  *and* the derived surface list, so the owner sign-off statement can name
  the actual blast radius rather than guessing at it.

This is the general answer to "the contract must handle a cross-surface
revision, not just per-surface ones": treat the shared helper as the unit
of hunk-level review (one code-level direction proof, §3.4), and treat the
surface list as a **derived, mechanical annotation for downstream
awareness** (§6.3, feeding the consumer-impact rules in §8) rather than a
second thing to review. Trying to make 12 people (or one person, 12 times)
sign off on 12 identical narrow additions would be exactly the
"expensive-cheap-thing" failure the task warns against; deriving and
disclosing the list mechanically gets the transparency without the cost.

## 7. Interaction with schema 2.0 and vocabulary consolidation

### 7.1 Measured scale (reproduced independently for this spec)

Running the audit's shingle-overlap measurement
(`shingle_stats.py`'s method: lowercase word/punctuation tokens, 8-word
sliding shingles, `licenses`/`does_not_license` text only) against the
108-call-site inventory: 12,844 total shingle positions across 189 usable
documents; 727 positions whose shingle recurs in 2+ distinct files — **5.7%**
exact 8-word overlap. Clustering `does_not_license_text` into
near-duplicate groups (`clusters_does_not_license_text.json`) finds **9**
clusters with 2+ members — comfortably past the "≥6 recurring
refusal-template families" bar — the largest spanning 7 files (the
"differential diagnosis of cause is the confounder audit's job" family:
`function_word_grammar_audit.py:578`, `punctuation_cadence_audit.py:501`,
`stance_modality_audit.py:523`, `voice_distance.py:933`, plus 3 more
members in the cluster file). A `register_match` literal —
`register_match=["argument-shaped nonfiction (op-ed / policy /
testimony)"]` — is copy-pasted verbatim into **5** files (verified by
direct grep, not the originally-estimated 6):
`plugins/setec-voiceprint/scripts/agd_move_scan.py:200`,
`plugins/setec-voiceprint/scripts/argquality_dimension_profile.py:236`,
`plugins/setec-voiceprint/scripts/fallacy_scan.py:216`,
`plugins/setec-voiceprint/scripts/position_pair_register.py:422`,
`plugins/setec-voiceprint/scripts/warrant_probe.py:210`.

A mass migration collapsing this into shared templates plus per-surface
deltas is, on this evidence, on the order of dozens of distinct template
families across ~190 script files — thousands of individual string sites,
far more than the per-revision artifact model in §2 can absorb one file at
a time without becoming a rubber stamp (nobody meaningfully reviews
artifact #400 of 2,000 that all say "moved this sentence into a shared
constant").

### 7.2 Migration mode — distinct, and stronger, not weaker

A migration-mode revision is its own artifact `kind` (`"migration"`
alongside `"single"` in a `kind` field added to §2.2's schema), with rules
that trade *volume* for *stronger* mechanical proof, never a lighter human
check:

1. **Every migrated hunk must pass the restructure golden-output proof
   (§3.3), exhaustively, not sampled.** For every surface whose prose
   moves into a shared template, CI renders both the merge-base literal and
   the candidate template-plus-delta composition and asserts byte
   equality — across the full migrated set in one CI run, using the same
   `gen_contract_fixtures.py`-style executed-builder oracle already cited
   in §3.3. A migration artifact with even one hunk that fails this proof
   does not partially land — the whole artifact fails, and the failing
   hunk must be pulled out into its own normal-track (narrow or widen)
   revision, reviewed on its own merits, never smuggled back into the bulk
   pass on a second attempt.
2. **No widen may hide inside a migration artifact.** If collapsing
   surfaces N and M's near-duplicate text reveals they were never actually
   saying the same thing (a genuine content decision, not a formatting
   one), that hunk is excluded from the migration artifact by construction
   — the golden-output proof in (1) already forces this, since a
   real content change can never render byte-identical to two different
   surfaces' old text simultaneously.
3. **Two independent human reviewers, not one owner sign-off.** Because the
   mechanical proof absorbs the volume, the human review surface is
   *small* (a diff report summarizing which shared templates were created
   and which surfaces adopted them) but the migration nonetheless touches
   the entire protected surface at once, so this contract requires two
   distinct approving reviewers recorded in the artifact (structurally,
   same `owner_signoff`-shaped object, twice, from two different `name`
   values) rather than the single owner sign-off a normal widen needs —
   *stronger*, matching the task's explicit requirement, not a shortcut.
4. **Schema restructuring and content migration are temporally separate
   PRs.** This repo already has the precedent for this exact sequencing
   rule: `specs/svp-packaging-conversion.md` §7's "relocation lands first
   as a semantics-preserving commit; the semantic owner then edits the
   relocated implementation in a later PR." Applied here: the lossless
   content migration (dedup into shared templates, proven byte-identical
   output) must land and be verified green before any schema-2.0
   structural change touches the same files. A single PR may never claim
   both "we deduplicated the prose" and "we also restructured the schema"
   — that conflates two failure modes into one diff nobody can review.

This is the direct answer to "how the contract scales... without
degenerating into a rubber stamp": the *volume* is handled by a mechanical,
exhaustive (not sampled) byte-equality oracle that makes a false pass
essentially impossible for anything but a genuine no-op; the *human*
review budget stays small and fixed regardless of how many hundred hunks
are in the batch, because reviewers are checking a diff summary and two
independent sign-offs, not re-deriving byte-equality by eye.

## 8. Consumer impact rules

### 8.1 Structural (code) consumers — narrow, verified blast radius

`setec-voicewright`'s `src/voicewright/narrative.py` (line 198:
`claim_license = envelope.get("claim_license") or {}`) is the **only**
place in that repo that keys into named `claim_license` sub-fields by
name — `comparison_set.prompt_fingerprint_sha256` and `length_range_words`
(lines 212-213). Every other consumer touchpoint in that repo
(`src/voicewright/setec/runner.py`, `src/voicewright/author_corpus.py`,
`src/voicewright/beat_matched.py`) treats `claim_license` /
`claim_license_rendered` as an opaque, closed-key-set member — present,
copied, never opened. And even narrative.py's one structural read is
low-stakes by its own code comment (lines 208-210 area): the
`comparison_set` fingerprint is used only as a **display fallback**, never
the value gating a drift/transfer decision — that's reserved for the run's
own `prompt_fingerprint_sha256`, read from `results`, not `claim_license`.

**Implication for this contract**: a pure text revision (any hunk under
§3.1-§3.3) never breaks `setec-voicewright` structurally, regardless of
direction — no code there parses `licenses` or `does_not_license` content.
The **one** shape of claim-license revision that *would* break
`setec-voicewright` at the code level is a hunk that changes whether
`comparison_set` carries a `prompt_fingerprint_sha256` key or whether
`length_range_words` is present/shaped as a 2-element numeric list — i.e. a
revision to the dict *shape*, not its prose values. This contract adds one
rule not implied by §3's text-shape logic: **any hunk that changes the
presence, name, or shape of the `comparison_set.prompt_fingerprint_sha256`
or `length_range_words` keys is classified `widen` unconditionally**,
regardless of what the sentence-subset test would otherwise say, and its
`owner_signoff.statement` must explicitly note the `setec-voicewright`
compatibility impact. This is a narrow, specific carve-out justified by
being the one verified structural dependency, not a blanket "widen
anything touching metadata" rule.

### 8.2 Prose (model-authored) consumers — the real blast radius

`apodictic`'s craft-audit skills instruct the model to treat
`does_not_license` as load-bearing text to **quote or paraphrase in every
finding** — verbatim in the repo's own words: "Load-bearing; quote or
paraphrase it in every finding" appears at
`plugins/apodictic/skills/specialized-audits/references/craft/argument-decision-audit.md:116`
and, near-identically, at
`plugins/apodictic/skills/specialized-audits/references/craft/narrative-decision-audit.md:105`
(paths relative to the `apodictic` repo). This is the actual blast radius
the task asks about: **no parser breaks**, but the next time an apodictic
skill runs after a merged revision, the model quotes the *new* text into a
real, user-facing finding — immediately, the moment the consuming
checkout's vendored/copied `setec-voiceprint` plugin subtree is refreshed
(`setec-voicewright` discovers a `setec` install by a `min_setec_version`
floor check, not an exact pin — see `discover_setec` in that repo's
setec/discovery.py around line 147, and the `min_setec_version` floor
field in setec/capabilities.py around lines 55-60 — so exposure timing is
gated by when the consumer refreshes its copy, not by an explicit version
bump gate this contract can rely on).

**Implication**: for a `narrow` revision this is a non-issue — added
disclosure only ever makes a future quoted finding more accurate. For a
`widen` revision, the real-world effect is that every finding generated
after the merge quotes weaker text, with no code-level signal anywhere
that this happened — which is exactly why §4.2 requires the
`changelog.d/` entry to name every affected surface: it is the only
mechanism by which an apodictic-side maintainer, who has no reason to poll
`setec-voiceprint`'s commit history, learns that a quoted-verbatim string
they depend on has changed meaning. This contract does not (and cannot,
from inside `setec-voiceprint`) force apodictic to re-review its golden
fixtures on every widen; it can only guarantee the disclosure exists where
a maintainer doing due diligence would look.

## 9. Acceptance gates as computable predicates

Each gate below is stated as a predicate plus the non-vacuity test that
proves it can fail — per AGENTS.md's build-preflight posture, an untested
gate is not a gate.

| # | Predicate | Non-vacuity test |
|---|---|---|
| G1 | Every protected-module delta not explained by an approved artifact's reconstruction (§2.3) fails CI. | Plant a softening: weaken one word in an existing `does_not_license` string with **no** artifact committed. Expect: guard fails with the original "unexplained delta" message, unchanged from today's behavior. |
| G2 | An approved `narrow` revision (reconstruction exact, direction correctly computed narrow) passes with the cheap-track requirements only. | Submit §5.2's worked artifact (single sentence appended, matching wording pattern) with no `owner_signoff`. Expect: guard passes; no sign-off/scope/changelog gate blocks it. |
| G3 | An approved `widen` revision missing its heavier requirement (no `owner_signoff`, or `hunks_sha256` mismatch, or extra files in the PR) fails. | Take §5.2's artifact, edit its `new_text` to instead *remove* the existing "OBSERVATIONS ONLY" caveat sentence from `agd_move_scan.py` (a genuine widen — a sentence is deleted) but leave `owner_signoff: null`. Expect: computed direction is `widen` (§3.1's decision procedure — `S_old` is not a subset of `S_new`, a sentence is missing — widen immediately, no tier analysis needed), and the guard fails on missing `owner_signoff`, distinctly from G1's failure message. |
| G4 | A self-declared `direction` that disagrees with the computed direction fails, independent of whether reconstruction succeeds. | Take §5.1's Hunk 1 (a pure addition, computed `narrow`) and hand-edit the artifact's `direction` field to `"widen"` with a fabricated `owner_signoff`. Expect: guard fails on direction mismatch, not on the (structurally valid) sign-off — proving the sign-off can't buy a pass for a lie about direction, and proving direction is recomputed rather than read. |
| G5 | A cross-surface (`changed_symbols` resolving to >1 caller) artifact's `changelog.d/` entry must name the CI-derived surface list exactly; a hand-typed list that omits a real caller fails. | Take §5.1's artifact, hand-author a `changelog.d/` entry naming only 6 of the 12 real callers. Expect: the derived-list check (§6.3) fails, distinct from G1-G4, because the committed changelog entry doesn't match the mechanically re-derived caller set. |
| G6 | A migration-mode artifact with one hunk that fails the golden-output proof does not partially land. | Construct a migration batch of 3 hunks where 2 render byte-identical pre/post and 1 does not (a genuine content drift mislabeled as a dedup). Expect: the whole artifact fails, not just the bad hunk — proving "no widen may hide inside a migration artifact" (§7.2.2) is enforced at the artifact, not hunk, granularity. |
| **G7** | **Required, build-blocking must-fail fixture.** The additive-undercut attack — an added sentence to a refusal field that is a strict superset (`S_old ⊊ S_new`) but semantically quantifies over the retained old sentences — must compute `widen`, never `narrow`, regardless of the artifact's self-declared `direction`. | Submit the coordinator's exact attack diff (§3.1's opening example: `does_not_license` gains "These limitations describe the uncalibrated default; with a supplied baseline the output may be read as provisional evidence of AI provenance." while both old sentences survive verbatim) as a `direction: "narrow"`, `owner_signoff: null` artifact. **Expect: FAIL**, on two independent grounds — (1) the added sentence is neither a Tier 1 dict entry nor a Tier 2 verbatim match, so §3.1's decision procedure computes `widen`, contradicting the artifact's declared `narrow` (a G4-shaped mismatch failure); (2) even if an attacker also hand-edits `direction` to `"widen"` in the same submission, it then fails G3-shaped (no `owner_signoff`). **A green result on this fixture — the artifact passing on the cheap track — is a build-blocking regression in the classifier, not a passing test.** This fixture must be added to the same test file as G1-G6, run on every change to the §3.1 classifier logic, and never skipped or marked `xfail`. |

## Out of scope / non-goals

- This spec does not itself modify `tools/check_claim_license_guard.py`,
  `claim_license.py`, `agd_move_scan.py`, or any production code — it is
  the contract a later build PR implements against, per the task's
  document-only scope.
- It does not resolve the identity-verification gap noted in §4.2 (a
  committed sign-off field proves *what* was approved, not
  cryptographically *who* approved it) — that is explicitly named as this
  contract's weakest point, not silently assumed away.
- It does not make Tier 2 (§3.1) re-verify a reused sentence's safety *in
  its new context*. Tier 2 trusts that a sentence which survived review in
  file A carries that scrutiny into file B — true for the co-occurrence
  hazard this fix targets (the sentence's own wording can't newly
  quantify over anything just by moving), but not a guarantee that file
  B's *other*, different sentences don't combine with the reused one in
  some new way file A never had. This is a narrower, second-order version
  of the same problem §3.1 was written to close, left open rather than
  hidden.
- It does not design schema 2.0 itself — §7 defines only the migration
  *mode*'s review mechanics, not the target schema shape.
- It does not change `setec-voicewright` or `apodictic` — §8 states the
  consumer-impact *rules* this contract's own gates must honor; any actual
  fixture/skill update on the consumer side is a separate, coordinated PR
  in those repos, consistent with `specs/svp-packaging-conversion.md` §7's
  "a packaging PR never folds semantic consumer-contract changes into a
  move" posture applied by analogy.

## Open questions

1. Should `review_track: "migration"` be time-boxed (e.g. only accepted
   while a named schema-2.0 tracking issue is open) so the bulk path can't
   become a permanent side-door for ordinary widens dressed up as
   "consolidation"? This spec assumes yes but does not specify the
   mechanism.
2. Whether the two-independent-reviewer requirement in §7.2.3 should be
   enforced by a committed artifact field (as specified) or additionally by
   GitHub's native multi-approver branch protection — the same
   identity-verification gap from §4.2 applies here at larger scale.
3. Whether `owner_signoff.statement`'s minimum-length/non-template check
   (§4.2.4) is worth strengthening into something more substantive before
   this contract's first real widen, given it is currently the weakest
   gate in the whole design.
4. Whether Tier 3's default-to-widen bucket (§3.1) should eventually gain
   its own cheap sub-path once enough judgment calls have accumulated —
   e.g. a reviewed, named exception list of specific sentence *pairs*
   known not to interact, rather than requiring full owner sign-off for
   every genuinely novel addition forever. Deferred deliberately: minting
   that mechanism now, before any real Tier 3 cases exist, risks building
   the wrong shape of shortcut.
5. Whether the license-granting-verb backstop (§3.1) is worth dropping
   entirely, given it gates nothing that Tiers 1-3 don't already gate more
   soundly, and a keyword list that catches nothing structural can create
   false confidence ("it passed the screen") even though the screen was
   never load-bearing. Kept for now because it fires before the more
   expensive tier analysis and gives a reviewer a concrete phrase to point
   at, but this is a judgment call, not a settled one.
