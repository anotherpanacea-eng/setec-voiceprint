# Build / review preflight

The P1/P2 failure modes to root out before a PR exists — distilled from a retrospective over ~350 external code-review findings (≈68 P1, 115 P2, 167 P3) across several build and spec waves, in a workflow where one model builds and a second, independent model reviews every PR. Examples are sanitized to the abstract pattern; the **CHECKs are the evergreen part.**

The reviewer is token-gated to roughly one round per multi-hour window, so a P1/P2 that survives to review — or a new one a fix introduces in round 2 — costs a whole window. The goal is **first-pass-clean**.

> **~40% of all P1s are the same mistake:** the spec/build *asserts* something about existing source — an API, a field, a line number, an env-var, a flag, a sibling spec, a precedent, an invariant — that it never **opened the file to verify.** Pattern-matching a plausible API from memory is the single biggest defect source.

---

## The checklist (at a glance)

Reviewers run the full list against the diff. **Builders carry only mode 1** — the one failure that is build-time-irreducible. For a validator / gate / linter / parser / config PR, weight the adversarial lens on mode 10 + the [SHALLOW-CHECK trap](#the-shallow-check-trap).

1. **[API-anchor drift](#1-api-anchor-drift)** — grep or open every symbol, field, API, env-var, flag, and sibling-spec before you cite it; if grep finds nothing, it does not exist. *(~40% of all P1s.)*
2. **[Posture leaks](#2-posture-leaks)** — for a descriptive tool, ship no scalar or label that is one hand-edit from a threshold or that names the inference target; fail closed.
3. **[Capability-boundary overclaim](#3-capability-boundary-overclaim)** — don't call a path pure / additive / lightweight unless `import` proves it and CI actually runs it.
4. **[Stale registration and count conventions](#4-stale-registration-and-count-conventions)** — drop-in fragments over editing a shared file; no `==N` count literals anywhere.
5. **[Untestable or undelivered claims](#5-untestable-or-undelivered-claims)** — every AC, and every "guarantee / invariant / bounded" word, needs a test that would FAIL if it were violated.
6. **[Math and data-structure errors](#6-math-and-data-structure-errors)** — bounds hold on saturated / empty / tie input; immutables aren't "added to"; consumed iterables are materialized.
7. **[Process discipline](#7-process-discipline)** — honor the PR-split; complete the paper trail; re-count every prose count against the diff.
8. **[Provenance and identity rebinding](#8-provenance-and-identity-rebinding)** — imported data carries its own recorded provenance or null, never the current run's; no stamp without backing records.
9. **[Measurement validity](#9-measurement-validity)** — confirm the control isolates the named confound and is not correlated with (or identical to) the signal you are measuring.
10. **[Override and gate hygiene](#10-override-and-gate-hygiene)** — a bogus id silences nothing; classify on parsed structure, not substrings; cover the format's full spec; the gate obeys its own rule; one matcher (a state machine), adopted everywhere.

Plus the round-2 discipline:

- **[The fix loop](#the-fix-loop)** — a fix *is* a build: re-run the whole checklist on it. Round 2 should be empty because *you*, not the reviewer, caught the regression.
- **[Verify on the minimum runtime](#verify-on-the-minimum-runtime)** — test the floor (oldest supported shell + locale, pinned interpreter), not your comfortable dev shell.
- **[The SHALLOW-CHECK trap](#the-shallow-check-trap)** — a guard that's added but not complete: cover all siblings, shapes, and directions — not just the one site the reviewer named.

---

## The modes in full

### 1. API-anchor drift
DOMINANT (~40% of P1s). Phantom APIs, wrong signatures, non-existent env-vars / tiers / flags, mis-cited precedents, dangling sibling-spec citations. Patterns that reach review:

- a loader or flag that doesn't exist, asserted as an "established pattern."
- env-vars asserted as a convention — none of them actually exist.
- a config value claimed as a valid enum member when it isn't.
- a function called with a config argument it doesn't take.
- citing a sibling spec repeatedly as load-bearing when it doesn't exist yet.
- a belief about what a module *is* ("module X is not a consumer of Y") that the imports flatly contradict — and the SAME false premise copied across several specs.

**CHECK:** for EVERY symbol / field / line / env-var / tier / flag / sibling-spec / precedent you cite, grep or open it. If grep returns nothing, **it does not exist — do not assert it.** Verify the actual *signature* (args, kwargs, return shape) AND the actual *return value* (including truncation / caps). Never describe a precedent file you have not opened this session.

### 2. Posture leaks
A thresholdable scalar or verdict-adjacent label. For any tool meant to be *descriptive* rather than a classifier, the danger is shipping an artifact that quietly becomes a decision input.

- a bare, formula-less `[0,1]` "concentration scalar" — the single most thresholdable artifact.
- a label whose top value names the *inference target* instead of the *measured property*.
- intermediate scores exposed as manifest fields, one hand-edit from a selection input.
- an inverted threshold that fails **open** (re-admits exactly what it exists to remove).

**CHECK:** does the result carry ANY scalar thresholdable into a selection / verdict? Does any label name the inference TARGET vs the measured property? Is the fail-direction **closed**? Walk the output and confirm no field is one hand-edit from a back door.

### 3. Capability-boundary overclaim
Claiming a component is dependency-free / pure / additive / lightweight when it isn't — or when it literally doesn't run.

- a "no-dependency core" that is actually a learned predictor or needs the heavy import.
- "purely additive, no second pass" — false (it added a pass).
- a code path that calls into a surface not registered / valid, so it raises and the "core" never builds.

**CHECK:** prove the claimed-pure path is import-clean (does `import` pull the heavy dependency?). Does every claimed path actually RUN in CI? If the core needs the dependency, label it that way — don't claim the lighter tier.

### 4. Stale registration and count conventions
Hand-maintained registries and counts that drift.

- `==N → N+1` count literals; a retired golden-file row; a registration manifest missing a required wrapper or field.

**CHECK:** prefer drop-in fragments over editing a shared file plus a count literal — a per-id fragment, **NO `==N` literal anywhere**, the manifest fragment matching a real sibling (open one). `git add` the fragment explicitly; run the drop-in + drift + docs-freshness gates.

### 5. Untestable or undelivered claims
Two faces of one defect: a *test* that asserts something false, and a *docstring / spec* that promises a runtime property the code doesn't deliver.

- "byte-for-byte identical" while the change adds fields to every record.
- "copied from `test_X:116`" when line 116 says something else.
- an AC asserting a refusal *envelope* the cited seam can't emit (it `sys.exit`s).
- tests that pin the WRONG posture and thereby *mask* a real inversion.
- a docstring promising a property the code does NOT deliver: a "finite-sample guarantee" that's actually bare empirical error; "permutation-invariant" logic that seeds by input order; an "N-min guarantee" computed *before* the filter that removes unusable items.
- an invariant the stored state **can't even check** (only one side of a pair is recorded, so any id "passes") — an unverifiable invariant is a data-model gap, not a comment.
- a **tie / parity test that recomputes from the artifact UNDER TEST** instead of the independent source it claims to tie to — it stays green when the two silently diverge. A parity test MUST read the other side (assert `a.fn is b.fn` for a genuinely shared primitive, or compute the expected value *from b*), never recompute from `a` and compare `a` to itself.

**CHECK:** every AC must be runnable against REAL behavior (frozen-fixture test for "byte-identical"; open line X before "copied from line X"). Every "guarantees / invariant / bounded / validated / N-min" word in a docstring OR spec must be backed by code that delivers it AND state that can support the check — if you can't write a test that would FAIL when the property is violated, you haven't delivered it.

### 6. Math and data-structure errors

- "add to" an **immutable** structure (impossible from the caller — the structure itself must change).
- a containment metric exceeding its own `[0,1]` bound on saturated input.
- a generator iterated twice (exhausted on the 2nd pass).

**CHECK:** bounds hold on adversarial input (saturation, ties, empties); immutable structures aren't "added to"; once-consumed iterables are materialized. Run the edge cases, not just the happy path.

### 7. Process discipline

- one commit bundling sub-PRs the spec wanted **separate** (defeats a per-PR review gate).
- a missing changelog / glossary / doc paper-trail entry CI doesn't enforce but a reviewer reads.
- a **hand-typed count or enumeration in a doc that has already drifted** — a "(12 items)" bullet missing the 13th the SAME diff adds. Any count / list in prose must be DERIVED or re-counted against the actual change (mode-4's no-`==N`-literal rule extends to docs).

**CHECK:** honor the spec's PR-split; complete the paper trail even where CI doesn't gate it; re-count every prose count against the diff (don't copy the spec's number).

### 8. Provenance and identity rebinding
RECURRING. Imported or foreign data gets re-stamped with the *current run's* identity, falsely certifying it. A copied anti-pattern that recurs across files.

- an imported manifest's judgment stamped with the **current code's** fingerprint, so a stale artifact reads as if produced today — and the **identical** bug in a second file.
- a "this is a MIX / derived" stamp written with **empty backing records** — a provenance claim with nothing behind it.

**CHECK:** a result READ from a manifest / import carries its **own recorded** provenance (fingerprint, source, author list) or **null** — never the current run's. "Produced here" and "read from elsewhere" must stamp differently. Any "MIX / derived / attributed / verified-under-X" claim requires the backing records to actually exist (≥ the asserted count); if they don't, **refuse** — don't fabricate the stamp.

### 9. Measurement validity
The rarest and subtlest: the code runs, is internally consistent, and is *conceptually wrong* — it measures the wrong thing or defeats its own stated purpose.

- a "topic control" variable that is actually a *style* dimension, so holding it fixed partials out the very signal being measured.

**CHECK:** does the control / normalization variable actually isolate the named confound, or is it correlated with — or identical to — the signal? Stratifying or normalizing on a dimension REMOVES that dimension from the result; confirm it is a confound you want gone, not the property you are trying to measure.

### 10. Override and gate hygiene
The dominant class in validator / gate builds. A check, its OVERRIDE, and any meta-gate are themselves code that fails the same ways the checked artifact does. Whole review rounds land here — and almost none is a live crash; they are robustness / scope / claim defects a happy-path run and a naive diff-review both pass.

- **Override too broad / wrong-scoped.** An override must (a) validate its target id RESOLVES — a bogus id must silence NOTHING (a non-resolving marker was disabling the whole check); (b) be scoped to the right UNIT, not global — one legitimate per-id override silenced an entire map; (c) reject DECOYS — a marker in prose, in a code span, with a suffix collision (`slug-extra`), or an unrelated cross-reference must not satisfy it. Derive scope from a real ANCHOR (the active section heading), NOT "the nearest matching token."
- **Substring classification / matching is fragile.** Gating, classifying, or matching a marker on a raw `"<!-- marker" in text` (or `grep -F`) also matches prose, code spans, and suffixes. Boundary-match the token, strip code spans, and classify on PARSED structure — never a bare substring.
- **A new matcher must cover its format's FULL spec AND every accepted spelling.** A first cut that stripped only single-backtick + fenced blocks and detected only the one-space spelling let the multi-backtick inline, tilde fence, zero-space, string-prefix, and digit-bearing variants sail straight through on the next round. When you "strip code spans," enumerate EVERY form of the format; when you match a marker / literal, match EVERY spelling the parser accepts (string prefixes, zero / extra whitespace, digits). A partial matcher reads as "closed the class" but isn't.
- **The gate must obey its own rule (dogfood).** A linter that bans hand-typed counts shipped a WRONG hand-typed count in its own changelog; a meta-linter false-positived on its own source and stayed green only via a silent exemption. A silent exemption that HIDES a real violation is load-bearing — remove the need for it, don't paper over it.
- **Gate completeness vs the claim.** If you harden / gate a class, gate it EVERYWHERE the class occurs, or scope the claim to what shipped. When a change says "closes the X class," grep the WHOLE tree for X and either fix all sites or name the survivors — and add a gate so the class can't return.
- **Two implementations of one check must agree.** A primary + degraded fallback (or a mirrored cross-language pair) must accept the SAME inputs, and the fallback must not be MORE permissive — a shell fallback honored a fenced-code decoy the primary path correctly rejected.
- **Don't hand-roll a structured-format parser twice — collapse to ONE state machine + delegation.** If you catch yourself regex-patching a matcher for a structured format (Markdown code spans, nested fences, HTML comments) — *especially in two languages* (a regex AND a shell `awk` / `sed`) — STOP: every edge is now two chances to diverge, and each regex patch breeds the next sibling. *When you've got a problem and you reach for a regex, you now have two problems.* Several rounds burned on multiline-inline / tilde-fence / triple-quote edges until the two parsers were replaced by ONE state-machine matcher that every caller (incl. the shell side, over a stdin CLI) DELEGATES to — the divergence class then vanished structurally, not patch-by-patch. **A second edge-case finding on the same matcher is the signal to change the ABSTRACTION, not add another alternation.** (The true ceiling is a real parser; a single state machine is the right *floor* only when no parser is available offline.)
- **Introducing the single source of truth ≠ adopting it — migrate (and DELETE) every old copy.** Collapsing a duplication class to one shared helper is only half done; the job finishes when you `grep` the OLD pattern everywhere and route EVERY instance through the helper, deleting the local copy. The sites most likely left behind are the ones hardened EARLIEST — built before the helper existed — which silently keep their own now-bypassable version. **The grep for the dead pattern IS the completeness check** — better still, add a lint rule that FAILS on the local pattern, so a stray copy cannot survive (the helper existing is not evidence the helper is used).

**CHECK:** for every override / exemption / gate — does a bogus id silence nothing? is scope a real anchor, not a token? do prose / code-span / suffix / cross-ref decoys fail in EVERY form (single + multi-backtick, fenced + tilde)? does the matcher catch every spelling (string prefixes, zero / extra whitespace, digit ids)? does the tool satisfy its own rule? is the class gated EVERYWHERE (grep, don't trust "I fixed the named site")? do the primary and fallback implementations accept the identical input set — confirmed on the MIN runtime, not just the dev shell? And: **have I now patched this format-matcher more than once?** If so, replace it with a state machine / parser before the next round — don't ship a third alternation.

---

## Round-2 prevention

### The fix loop
A fix responding to a review finding **is a build** — give it the same pre-flight. After folding: (a) confirm the fix FULLY resolves the finding; (b) self-review the fix against modes 1–10 (a fix that adds a field can introduce a mode-1/2/4 defect; a docstring claim a mode-5/8; a control a mode-9; a matcher / override a mode-10); (c) re-run the full suite; THEN push. Round 2 should be empty because *you*, not the reviewer, caught the regression.

### Verify on the minimum runtime
A green dev-shell run is NOT a green min-runtime run, and the gap is a free review round. Shell gates commonly run under `set -euo pipefail` on macOS `/bin/bash` **3.2** in a **UTF-8 locale** — where an unbraced `$VAR` adjacent to a multibyte character is parsed as one (unbound) name and aborts under `set -u`, a break that newer bash and Linux CI both hide. Before "ready," run the shell aggregate on the oldest supported shell under `LC_ALL=…UTF-8`, and the interpreted languages on their pinned versions. Brace expansions adjacent to non-word characters; **test the floor, not the ceiling.**

### The SHALLOW-CHECK trap
The most common round-2 P1 is a fix that adds the *right kind* of check but stops short, so the next review round walks straight around it. The guard exists; it just doesn't cover the whole surface.

- **count-only**, not contents: `len(x) >= 2` accepts `[{}, {}]` — validate every leg's required fields + recompute any integrity digest, don't just count.
- **per-record**, not cross-record: validating membership on each row still lets one key name two different sets across rows — bind the key to its member set.
- **sampled**, not exhaustive: a self-audit that tried string and list-of-non-dict inputs but not a non-iterable (`x: 1`) or a truthy-non-dict (`x: ["y"]`) declared the path clean; both then escaped as tracebacks. A sampled probe is NOT a clean bill.
- **one type / branch**, not all: a blank-string guard that misses whitespace; an `or {}` that misses a truthy non-dict; a boundary that handles `.` but not `,`.
- **one site**, not all siblings (RECURRING): the fix guards the exact field / branch the reviewer named but leaves an IDENTICAL-shape sibling on the same path open. When a fix reads as "guard field X against shape Y," grep for EVERY other field / site that takes shape Y down the same path and guard them in the same pass. (This recurses into the *review* too: a self-review that fingers only the site the reviewer would name can itself be a shallow check — the regression test is what exposes the missed sibling.)
- **early-return before the whole-surface check (RECURRING):** an `if not <parsed_blocks>: return ok` skips the checks that run on the RAW / prose surface, so a violation expressed in prose passes because parsing found "nothing to check." The prose / whole-surface checks must run even when structured parsing is empty; only genuinely block-bound checks may no-op.
- **one direction of a membership invariant, not both:** rejecting UNDECLARED keys while never requiring all DECLARED members be covered — an item omitted a declared member and passed. Check no-extra AND no-missing.
- **a guard that lets input pass check A while disabling check B:** a malformed item satisfied check A yet its unreadable field silently escaped check B. If passing A is the precondition for B to run, validate the input FULLY at A so B can never be skipped by a malformed-but-A-passing payload.

**CHECK:** ask "what's the SMALLEST input that satisfies my new guard but still breaks downstream?" and test THAT. Enumerate the input shapes (empty / whitespace / wrong-type-but-truthy / non-iterable / duplicate-across-records / forged-but-well-shaped) and cover the axis, not one point on it. And before pushing a fix, **read the existing self-tests / regressions** — a too-aggressive guard that breaks a legitimate pinned case is itself a round-2 regression.
