### Added

**`agent_host` host-delegated judge rolled into the three tuple-gated judge
families (spec 35 follow-on).** The mechanical follow-on the spec named: after
M1 wired `agent_host` into `argument_judge` / `narrative_judge`, this widens the
last three hardcoded `("anthropic","openai","gemini")` provider gates —
`argquality_judge` / `fallacy_judge` / `warrant_judge` — to read
`judge_backends.PROVIDERS`, and threads `agent_host` through their audit CLIs
`argquality_dimension_profile`, `fallacy_scan`, and `warrant_probe`. Per family:
the `build_judge` gate widened + an `agent_host` no-model branch (defaults
`model="host-resolved"`, so `--judge-model` is not required); `agent_host` added
to each audit's `--judge` argparse `choices` (default/required unchanged, so it
stays opt-in); a new `compose_envelope` claim-license caveat naming the
host-runtime model, its NON-DETERMINISTIC / host-version-fluid nature, and the
`agent_host:<host>:<model>` identity a consumer drift gate reads to assert judge
model != generator model; and a `judge_host` field on `comparison_set` (the
firewall hook). The judge delegates to the host runtime's model (MCP sampling /
subagent), key-free, records provenance, and adds a caveat without removing any
no-verdict refusal. See specs/35-host-delegated-judge.md.
