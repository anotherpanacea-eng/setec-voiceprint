### Changed

**ROADMAP status-reconciliation refreshed for the v1.117.0 release (#196).** The
most recent authoritative reconciliation was dated 2026-06-13 / "now at 1.116.0"
and predated the v1.117.0 release, so the strategic doc again lagged the shipped
version — the residual ROADMAP item on #196 (the glossary item was closed by
#203; the `capabilities.d/` `status: todo` audit is split out as its own tracked
effort per the maintainer's #196 comment). Adds a dated
`## Status reconciliation (2026-06-17)` section (supplementing, not rewriting,
the 2026-06-13 and 2026-06-06 passes) recording the 1.116.0 → 1.117.0 delta:
ArgScope B5 collapse-dynamics + C0 register-baseline plumbing on
`argument_decision_audit` (both additive, both verdict-neutral), the
`narrative_decision_audit` setec-voicewright consumer-membership add, the shared
`judge_backends.py` judge-plumbing dedup, and the fleet release-train runbook /
release-and-sync infra — released as v1.117.0. Also corrects the 2026-06-13
header so it no longer reads as the current version and notes that
`judge_backends.py` (listed there under repo-hygiene) actually landed in the
v1.117.0 window. Docs only — no code, capability, golden, or version change.
