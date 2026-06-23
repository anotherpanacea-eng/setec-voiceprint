### Added

- **Host-delegated judge backend (`agent_host`)** — `argument_judge` / `narrative_judge`
  (and `voice_verifier` via the shared path) can delegate LLM judging to the **host agent
  runtime** (Claude Code / Codex / Gemini Antigravity) through a registered transport
  (MCP `sampling` / a subagent), instead of an API call. The judge tier now runs
  **key-free** for development, validation, and interactive use; the API key is demoted to
  a production-unattended dependency. Opt-in (`--judge agent_host`, defaulting
  `model="host-resolved"` — no `--judge-model` required); the default backend is unchanged.
  Delegation is **descriptive, no-verdict** (refusals threaded unchanged) and records
  provenance — `judge_identity.host` + `comparison_set.judge_host` — so a consumer can
  assert the judge model ≠ its generator model (the anti-Goodhart selection/validation
  firewall, named here and enforced at the consumer's drift gate). When the host **names
  the concrete model** it used — via a structured transport response
  (`{text|content|judgment, model, revision}`) or `SETEC_HOST_MODEL` /
  `SETEC_HOST_MODEL_REVISION` — that concrete id is recorded as `judge_identity.model`
  (overriding the `host-resolved` placeholder), so the load-bearing judge≠generator
  firewall has a real model to compare. The placeholder remains only when nobody names a
  model — and `judge_backends.assert_judge_generator_disjoint()` then **fails closed**:
  a non-concrete identity (the placeholder) is refused on a disjointness-required
  (holdout/selection) path rather than silently treated as disjoint, and a concrete
  judge model equal to the generator is refused as self-grading. Spec
  [35-host-delegated-judge](../../specs/35-host-delegated-judge.md). Motivated by the M2
  finding that a host subagent judges authorship at 0.90 (vs a local 3B's 0.50 and the
  LUAR embedding's 0.85).
