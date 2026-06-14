### Added

**`references/fleet-release-runbook.md` — the fleet release-train runbook.** A
single authoritative, in-repo sequence for driving a release through the
four-repo fleet (setec-voiceprint → apodictic + setec-voicewright re-pin →
apodictic release → APODICTIC-Gemini re-pin). Documents the producer-before-
consumer hard rule and its two distinct tag resolvers, the exact producer
release + consumer `gh workflow run … -f ref=<tag>` dispatch commands, the three
weekly consumer crons (Gemini 14:00 / apodictic 15:00 / voicewright 16:00 UTC,
with the workflows as the source of truth), `release.sh`'s conditional step-9
tag/push behavior, apodictic's PUSH-side rsync/`--check-sync` as a skip-if-absent
local mirror (the pull chain is canonical), and the four gotchas (gh-OAuth
workflow-scope 403, golden re-splice, manual-bump-vs-weekly-bot, ordering).
Linked from `AGENTS.md` §Tagging. Docs only (class `docs` → PATCH); no new
TASK_SURFACE, capability, or golden. (Hub release-train item.)
