# 35-host-delegated-judge

> A host-delegated LLM-judge backend — the agent runtime (Claude Code / Codex / Gemini
> Antigravity) is the judge — so the judge tier runs **key-free** in-harness for
> development, validation, and interactive use, with the API key demoted to a
> production-unattended dependency.

- **Status:** Draft
- **Tier:** near-term (M1 provider seam + stub = stdlib, CI-runnable); research-grade (M2 real host transports, gated)
- **GPU required:** no
- **Upstream / prior art:** MCP **sampling** (`sampling/createMessage` — the standardized
  "server asks the host's LLM to complete this" primitive, modelcontextprotocol.io);
  the M2-wave empirical finding (`D:\Code-PC\_m2_results.md`, 2026-06-23): a host subagent
  judges authorship at **0.90** (vs a local 3B's 0.50 and the LUAR embedding's 0.85) and
  argument-quality at 6/8 (vs 1/8) — the host model is a capable judge available free in-harness.
- **License decision:** N/A — no weights wrapped; the judgment is produced by the host
  runtime's own model. (No key bundled, ever — see Considered & rejected.)

## Motivation

`judge_backends.py` exposes exactly three providers — `anthropic`, `openai`, `gemini` —
all **API-keyed**. So every LLM-judge surface (`argument_judge`, `narrative_judge`, and the
LLM-judge M2 shortlist: gaqcorpus argument-quality, llm-verifier authorship, judge-auditing,
cross-doc-originality's QUD lens) requires an API key **even to validate that the signal
exists**. During the M2 wave that read as a hard blocker ("can't run the judge cluster without
a key").

But **SETEC is a plugin harness**: it runs *inside* agent runtimes — Claude Code, Codex,
Gemini Antigravity — each of which already carries a frontier model and a subagent/sampling
facility. Empirically those runtimes judge *better than a keyed small model and better than the
embedding baseline* (the 0.90 vs 0.50 vs 0.85 finding above). So the capable judge is already
present, for free, in the harness that's running SETEC.

This spec adds a **`agent_host` judge provider** that delegates the judgment to the host
runtime instead of calling an API. It makes the judge tier runnable **key-free** for dev,
validation, and interactive use, and demotes the API key from "needed to judge at all" to
"needed only for **production-unattended** runs (CI/cron) where no host agent is in the loop."

**Orthogonality:** this is **not a new signal axis** — it is a new *backend* for the existing
judge surfaces. The new thing is **operational**: the judge can be the host runtime's model,
resolved at run time, with no key and no weights. Every judge surface's data contract,
JudgeResult shape, claim-license, and no-verdict refusals are **unchanged** — only the source
of the JSON judgment changes.

## Method

`make_api_judge` already abstracts the provider behind a `(client, call, read)` triple
returned by `_provider_setup(provider, ...)`: `call(content) -> response` issues the request,
`read(response) -> (text, identity_extras)` extracts the model's text and identity. The
returned `_run(judge_input)` builds the user content, calls, extracts JSON, and hands the
payload to the family's unchanged `build_result`.

A host-delegated judge is therefore **a new `_provider_setup` branch**, not a redesign:

- `call(content)` routes the request — `{system_preamble, user_content, response_schema,
  no_verdict_instruction}` — to a **host judge transport** (a callable `host_judge(request)
  -> str` that returns the model's JSON text). The transport is **injectable / resolved from
  the environment** (see Design); the JSON schema is included in the request so the host's
  model returns a parseable object, mirroring `openai`'s `response_format=json_object` and
  `gemini`'s `response_mime_type=application/json`.
- `read(text) -> (text, {"delegated": True, "host": <runtime-id>})`, so the envelope's judge
  identity becomes `{"kind": "agent_host", "model": <host-model-or-"host-resolved">, "host":
  <runtime-id>, "delegated": True}` — the delegation is recorded as provenance.
- `build_result`, the JudgeResult shapes, and the claim-license refusals are **untouched**.

## The load-bearing design question (and its answer)

**The host runtime is often also the GENERATOR.** In the voicewright consumer the host agent
may be the very thing writing the candidate text. If the same model both *generates* a
candidate and *judges* it on a **HOLDOUT** validator, the selection/validation firewall
collapses — the generator grades its own homework. That is the exact circularity
`HOLDOUT_SURFACES` exists to prevent (cf. spec 28's selection/validation disjointness).

The answer — the producer makes the judge identity **checkable**, and names the consumer-side
gate it cannot itself enforce:

- **Provenance, recorded.** Every `agent_host` envelope + claim-license carries
  `backend_id = agent_host:<host>:<model>`. The opaque "an LLM judged this" becomes a
  checkable identity.
- **Opt-in, never the default.** `agent_host` is selected explicitly. The default provider is
  unchanged. Routing judgments through "whatever model the host carries" must be a deliberate
  choice, not a silent one — both because it changes behavior and because it is the move that
  can introduce the generator=judge circularity.
- **Posture-gated, with the gate named.** `agent_host` is appropriate on **descriptive /
  SELECTION-family** judge surfaces and for **dev / interactive validation**. A consumer that
  routes a judge surface into a **HOLDOUT validator or a selection signal** MUST assert, at its
  drift gate, that the `agent_host` judge model **≠** its generator model. The producer cannot
  know the generator; it makes the judge identity readable so the consumer can. This is the
  same producer-names-it / consumer-enforces-it split spec 28 uses for the encoder-disjointness
  gate — **import/identity disjointness is NECESSARY, the model-≠-generator assertion is the
  consumer drift gate's job**.
- **Honest non-determinism.** A host judgment is non-deterministic and host-version-fluid; the
  claim-license says so. We do **not** memoize to fake determinism (that would hide it). The
  no-verdict posture already makes the output descriptive/advisory, which is what makes a
  non-deterministic judge tolerable.

## Design

### M1 — provider seam + transport abstraction + posture guards (stdlib, CI-runnable, no live host)

The honest M1 claim: **"`agent_host` is a selectable judge provider; it resolves a host
transport, runs a judge end-to-end through it, records the delegation as provenance, and the
posture/refusals are unchanged — all proven against a STUB transport, without a live host."**
It does **not** claim the host judges *well* — that is the M2 real-transport smoke's to show
(and the M2-wave finding already evidences it).

- **The lever is FOUR coordinated edits, not one (review [P1]·1/2).** `make_api_judge` is
  genuinely **unchanged**, but `_provider_setup`/`make_api_judge` **do not read `PROVIDERS`** —
  the provider gate is the `if provider == ...` chain in `_provider_setup` ending at
  `raise judge_error("unknown api judge provider")` (`judge_backends.py:200`). So `agent_host`
  needs: **(a)** a new `agent_host` branch in `_provider_setup` (the `(client, call, read)`
  contract covers it); **(b)** `PROVIDERS += ("agent_host",)` — read **only** by
  `voice_verifier.py:605` (`kind in judge_backends.PROVIDERS`); **(c)** each target family's
  provider gate widened — `argument_judge.py:509` and `narrative_judge.py:374` gate on a
  **hardcoded** `("anthropic","openai","gemini")` literal (→ `raise JudgeError("unknown judge
  kind")`), so they must read `judge_backends.PROVIDERS` (the single-source-of-truth fix) or be
  widened to include `agent_host`; **(d)** the audit CLI argparse gate — `--judge` (NOT
  `--provider`) with `choices=("manifest","mock","anthropic","openai","gemini")`
  (`argument_decision_audit.py:728`, `narrative_decision_audit.py:587`) — must gain `agent_host`,
  or argparse exit-2 rejects it before `build_judge` runs.
- **`_provider_setup("agent_host", ...)`** returns `(transport, call, read)`:
  - **Transport resolution (from the environment, host-registered).** In order: an importable
    entry-point `SETEC_HOST_JUDGE="module:function"` (the host registers a callable wired to its
    own subagent/sampling), or a subprocess command `SETEC_HOST_JUDGE_CMD` (stdin=request JSON,
    stdout=judgment JSON). If neither resolves → `judge_error` with a registration hint (graceful,
    mirroring the missing-SDK errors), **never a traceback**.
  - `call(content)` packages `{system_preamble, content, response_schema, no_verdict}` and
    invokes the transport; `read(text)` returns `(text, {"delegated": True, "host": <id>})`.
  - A **STUB transport** (returns a canned schema-valid judgment) is injectable for tests — the
    same discipline the API providers use (their SDK client/call is never hit in CI; tests inject
    a fake). The whole `agent_host` path is CI-testable with **no live host and no key**.
- **No `--judge-model` required (review [P2]·5).** Each family raises `JudgeError("<kind> judge
  requires --judge-model")` when `model` is falsy (`argument_judge.py:510`,
  `narrative_judge.py:375`). `agent_host` resolves the model from the host, so its `build_judge`
  branch must **not** require `model` — it defaults to `model="host-resolved"`, which flows into
  `make_api_judge`'s identity dict `{"kind": provider, "model": model, ...}` (`judge_backends.py:81`)
  when the host doesn't expose an id.
- **Claim-license caveat — ADD a new `judge_kind` branch (review [P1]·3/4); the structure is in
  the AUDITS, not `judge_backends`.** The claim-license/caveat list is built in each audit's
  `compose_envelope`, keyed on `judge_kind`, with branches **only for `mock` and `manifest`**
  (`argument_decision_audit.py:607`, `narrative_decision_audit.py:421`); the API providers get
  **no** per-provider caveat. So this **adds the first real-provider caveat branch** (`agent_host`),
  alongside `mock`/`manifest`, naming: produced by the **host runtime's model** (not a pinned
  API model@revision); **non-deterministic / host-version-fluid**; identity recorded as
  `agent_host:<host>:<model>` **so a consumer can assert disjointness from a generator**.
  `does_not_license` is a **single CLI-default string** (`DEFAULT_DOES_NOT_LICENSE`,
  `argument_decision_audit.py:80`), provider-independent — `agent_host` threads it through
  **unchanged**: delegation adds a *caveat*, never removes a refusal or earns a verdict.
- **Make the `backend_id` readable (review [P3]·8).** The envelope's `comparison_set` currently
  carries `judge_kind` + `judge_model` (`argument_decision_audit.py:635`) but **no `host` field**.
  Add `host` there so the `agent_host:<host>:<model>` triple the firewall depends on is actually
  present for a consumer drift gate to read. This is the concrete, testable firewall hook.
- **Structural posture guards (tests):** provider resolves + runs end-to-end via the stub;
  the identity records `kind=agent_host` + the host id + `delegated=True`; the refusal set is
  unchanged vs the API providers; an unresolved transport yields a graceful `judge_error`; and
  the `agent_host` judge identity is present in the envelope so a consumer drift gate can read it
  (the firewall hook, made checkable).

### M2 — the real host transports + MCP sampling + elicitation (gated maintainer smokes, never CI)

- **MCP sampling transport (primary, host-agnostic).** When SETEC runs as / behind an MCP
  server with a sampling-capable client, `host_judge` issues a `sampling/createMessage`
  (system + user + JSON schema) and reads the completion. **One transport covers any
  sampling-capable host**; the round-trip is synchronous, so it fits `call` cleanly.
- **Per-host subagent transports (fallback where sampling isn't exposed).** Thin `host_judge`
  adapters: Claude Code (Agent/subagent), Codex (sub-task), Gemini Antigravity (agent) — each
  spawns a judge subagent and returns its JSON; registered via `SETEC_HOST_JUDGE`.
- **Two-phase elicitation entrypoint (hosts with neither in-band sampling nor a callback).** A
  surface-level mode `--emit-judge-requests` / `--judge-responses FILE`: SETEC writes the judge
  requests and exits, the host runs its subagents, SETEC re-runs to aggregate. This is the only
  piece that touches a surface's CLI (not just `judge_backends`) — flagged as the heaviest M2
  sub-item; **may become its own spec**.
- M2 records real-host smoke observations (a host judgment parses + matches the schema; the
  identity is recorded; the M2-wave 0.90 authorship result is the standing evidence) as
  **maintainer notes, never a CI gate**. License/key checks N/A (no weights, no key).

## Contract (the testable interface)

- **task_surface:** **none new.** This extends the existing `argument_judge` / `narrative_judge`
  (and any future LLM-judge surface) with a provider. No `output_schema.VALID_TASK_SURFACES`
  change, no `claim_license.TASK_SURFACE_LABELS` change.
- **CLI:** the audit surfaces' **`--judge`** flag (not `--provider`) gains `agent_host` in its
  argparse `choices` (`argument_decision_audit.py:728`, `narrative_decision_audit.py:587`). The
  default stays `--judge manifest` (`:729`/`:588`), so `agent_host` cannot become default by
  omission — opt-in is structurally free (review [P2]·7). No new script in M1.
  (`--emit-judge-requests` / `--judge-responses` is the M2 elicitation entrypoint.)
- **JSON envelope:** unchanged shape; the judge-identity block now records
  `kind=agent_host`, `host`, `delegated=True`. Still carries the family's `ClaimLicense`.
- **Claim license:** unchanged refusals (no verdict); a new `agent_host` caveat (host-model,
  non-deterministic, identity-recorded-for-disjointness).
- **capabilities.yaml entry:** **no new id, no count bump.** The existing judge surfaces'
  `capabilities.d/*.yaml` provider list / cost note is reworded to name `agent_host` (key-free,
  host-resolved) and re-blesses only their own golden fragments (the spec-28 discipline:
  `test_capabilities_dropin.py` derives the count from the fragment set — nothing to bump).
- **Dependencies / footprint:** **none new at import** (the host transports are lazy / env-
  resolved; MCP sampling rides the existing MCP channel). The API SDKs stay `python_optional`.

## Test contract (names + invariants the build must satisfy)

`plugins/setec-voiceprint/scripts/tests/test_host_delegated_judge.py` (+ small additions to the
two families' judge tests):

1. **provider selectable end-to-end (all four gates):** `"agent_host" in judge_backends.PROVIDERS`;
   `_provider_setup("agent_host", ...)` returns a `(transport, call, read)` triple (not the
   `unknown api judge provider` raise); each target family's `build_judge("agent_host", ...)`
   resolves (not `unknown judge kind`); and `agent_host` is in the audit `--judge` argparse
   `choices`.
2. **end-to-end via stub (no host, no key):** with a STUB transport injected, a family's
   `agent_host` judge runs `_run(judge_input)` and returns the family's normal `JudgeResult`
   (schema-valid) — exercising `build_user_content` → transport → `extract_json` → `build_result`.
3. **identity / provenance recorded:** the result's judge identity is
   `{"kind": "agent_host", "model": "host-resolved"|<id>, ...}` and the envelope's
   `comparison_set` carries a `host` field (the added firewall hook).
4. **no model required:** an `agent_host` run with **no** `--judge-model` succeeds (defaults
   `model="host-resolved"`), where the same omission on `anthropic` raises
   `"... requires --judge-model"`.
5. **refusal threaded unchanged (delegation earns no verdict):** an `agent_host` envelope carries
   the **same `does_not_license`** string as a `mock`/`manifest` run (it is the provider-
   independent CLI default; delegation adds a caveat, removes no refusal) — NOT a "byte-identical
   across four providers" assertion (there is no per-provider refusal set).
6. **per-provider caveat present:** the new `agent_host` branch in the audit's `compose_envelope`
   caveat block names host-model + non-determinism + identity-recorded-for-disjointness; a
   `mock`/`manifest`/api run does **not** carry it.
7. **graceful unresolved transport:** with `SETEC_HOST_JUDGE` / `SETEC_HOST_JUDGE_CMD` unset,
   building the `agent_host` judge raises the family `JudgeError` with a registration hint (not a
   bare traceback / not an SDK import error).
8. **non-JSON host body wrapped:** a stub returning non-JSON surfaces as
   `"agent_host judge returned non-JSON"` (the existing wrapping path).
9. **default unchanged:** the audits' default `--judge` is still `manifest` (opt-in firewall:
   `agent_host` cannot be reached by omission).
10. **import stays stdlib:** importing `judge_backends` pulls no host SDK and no transport.
11. **M2 (skipif-gated, never CI):** a real-host transport smoke (MCP sampling or a subagent
    adapter) returns a schema-valid judgment and records its identity. Maintainer-run.

## Calibration posture

Unchanged: the judge surfaces ship **no-verdict, descriptive**; `agent_host` does not alter
that. A host judge is non-deterministic and uncalibrated — the claim-license says so. Nothing
here promotes any surface's `status`. (The M2-wave evidence — 0.90 authorship, beating the
embedding — is recorded as motivation/maintainer note, not a shipped band.)

## Out of scope / non-goals

No new task_surface, no new capability id, no count bump. No change to JudgeResult shapes /
claim-license refusals / no-verdict posture. No default-provider change (`agent_host` is opt-in).
No consumer (voicewright) code — the model-≠-generator drift gate is **named** as a consumer
contract, not implemented here. No bundled key, ever. The two-phase elicitation surface-CLI
change is M2 / possibly its own spec.

**M1 scope across the six provider gates (review [P2]·6).** There are six provider gates in the
repo: `argument_judge.py:509`, `narrative_judge.py:374`, `argquality_judge.py:444`,
`fallacy_judge.py:364`, `warrant_judge.py:309` (hardcoded tuples), and `voice_verifier.py:605`
(reads `judge_backends.PROVIDERS`). **M1 fully wires only `argument_judge` + `narrative_judge`**
(gate → `PROVIDERS`, `--judge` choices, the `compose_envelope` caveat + `host` field, the
no-model branch) **plus the `judge_backends` `agent_host` provider** itself. Because
`voice_verifier` gates on `PROVIDERS`, adding `agent_host` there makes it accept the provider
**through the same `make_api_judge` path** — the build must **confirm** that path resolves (it
should, since `_provider_setup` is shared) and not leave `voice_verifier` half-wired; if its
envelope/caveat wiring differs, it joins the follow-on. The three tuple-gated families
(`argquality`/`fallacy`/`warrant`) **keep their literals in M1** (they do **not** silently gain
`agent_host`) and are an explicit, mechanical **follow-on** PR — not a hidden partial rollout.

## Open questions

- **Host model-id resolution for provenance** — does each runtime expose the model id it used
  (for `agent_host:<host>:<model>`)? If not, record `<model>="host-resolved"` and rely on
  `<host>` + the consumer's own knowledge of its generator. (Affects how tight the disjointness
  assertion can be.)
- ~~**Hard refusal vs consumer gate**~~ **RESOLVED (review [P2]·7) → consumer-side.** The
  producer does **not** own the holdout/selection taxonomy (that lives in the voicewright
  consumer's `HOLDOUT_SURFACES`/`SELECTION_SURFACES`), and it cannot know the generator
  (stated above). So the producer makes the judge identity readable (the `host` field +
  `agent_host:<host>:<model>` backend_id) and **names** the `judge model ≠ generator model`
  assertion as the consumer drift gate's job — exactly spec 28's producer-names-it /
  consumer-enforces-it split. No producer-side holdout refusal.
- **Should the two-phase elicitation be its own spec** (it touches surface CLIs, not just
  `judge_backends`)? Leaning yes.
- **MCP sampling availability** across Codex / Gemini Antigravity at build time — if absent,
  those hosts use the subagent-callback transport until they expose sampling.

## Review findings folded in (M1)

Adversarially reviewed (verdict **GO-WITH-CHANGES**, posture clean, M1 buildable). The review
caught that the original draft mis-described the seam mechanics in four load-bearing places — a
builder following the draft would have shipped an **unreachable** provider and hunted for a
caveat structure that doesn't exist. Every finding is folded into the text above; recorded here
so the build honors each against the real file:line:

- **[P1] The lever is four coordinated edits, not "add to `PROVIDERS`".** `_provider_setup`/
  `make_api_judge` never read `PROVIDERS`; the gate is the `if provider==` chain
  (`judge_backends.py:200`). `PROVIDERS` is read only by `voice_verifier.py:605`. The two target
  families gate on hardcoded `("anthropic","openai","gemini")` literals (`argument_judge.py:509`,
  `narrative_judge.py:374`). Folded into Design (the four-edit lever) + acceptance #1.
- **[P1] The CLI gate is `--judge` argparse `choices`, not `--provider`.** `choices=("manifest",
  "mock","anthropic","openai","gemini")` (`argument_decision_audit.py:728`,
  `narrative_decision_audit.py:587`) rejects out-of-choices with exit-2 before `build_judge`.
  Folded into Contract/CLI + acceptance #1.
- **[P1] The claim-license/caveat lives in the audits' `compose_envelope`, keyed on `judge_kind`,
  with branches only for `mock`/`manifest`** (`argument_decision_audit.py:607`,
  `narrative_decision_audit.py:421`) — the API providers get NO per-provider caveat. The draft's
  "branch the existing per-provider caveat structure" described a structure that doesn't exist
  (the spec-28 trap, one worse). This **adds the first real-provider caveat**. Folded into the
  claim-license bullet + acceptance #6.
- **[P1] "Refusals byte-identical across four providers" is vacuous.** `does_not_license` is a
  single CLI-default string (`argument_decision_audit.py:80`), provider-independent. Reframed to
  "agent_host threads the same `does_not_license` as mock/manifest, unchanged." Acceptance #5.
- **[P2] `agent_host` "no pinned model" collides with the `if not model` gate** (`argument_judge.py:510`).
  The `build_judge` branch must default `model="host-resolved"`, not require `--judge-model`.
  Folded into Design + acceptance #4.
- **[P2] Six provider gates, not two.** M1 scope made explicit (two families fully wired;
  `voice_verifier` accepts via the shared `make_api_judge` path — build must confirm;
  `argquality`/`fallacy`/`warrant` keep literals, follow-on). Folded into Non-goals.
- **[P2] "Opt-in never default" is structurally free** (default `--judge manifest`,
  `argument_decision_audit.py:729`). Stated as tested (acceptance #9). The producer-refuse Open
  Question is **resolved consumer-side** (spec-28 precedent).
- **[P3] The `backend_id` the firewall depends on requires actually adding a `host` field to
  `comparison_set`** (currently only `judge_kind`+`judge_model`, `argument_decision_audit.py:635`).
  Made the concrete, testable firewall hook (acceptance #3) instead of a restatement.
