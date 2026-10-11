### Changed

**External-mirror harness is now discoverable.** `external_mirror/workflow.py`,
the operator entry point for the external-mirror discrimination method
(`prepare`, `status`, `score`), had no capability fragment, so recommenders
could not route users to it. It now has a curated `heuristic` entry,
`external_mirror_workflow`, on the `external_mirror_discrimination` surface,
and declares that `TASK_SURFACE`. The Phase B emitter `compose_evidence_pack`
stays an internal step that the harness runs. No script behavior changed.
