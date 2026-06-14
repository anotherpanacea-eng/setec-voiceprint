# Fleet release-train runbook

The authoritative, in-repo sequence for driving a release through the
four-repo fleet. This is the producer's copy of the release sequence it
initiates; follow it (human or agent) instead of re-deriving the chain from
the workflow yamls each time. It is self-contained: every load-bearing step
is here, so a cloud session (which holds only this one git repo and cannot
reach the maintainer's `Cowork/repo-fleet/` hub) can execute it. The hub
`PATTERNS.md` §1/§4 carries the meta-pattern ("the producer owns the shared
artifacts") and the verbose merge-bypass mechanics; this runbook is the
actionable sequence and points back to it only for optional depth.

## The chain

```
setec-voiceprint  ──tag v1.X.Y──▶  apodictic + setec-voicewright re-pin (bot/dispatch)
                                          │
                                   apodictic  ──tag vA.B.C──▶  APODICTIC-Gemini re-pin (bot/dispatch)
```

Two producer→consumer hops:

1. **setec-voiceprint** (this repo) cuts a tagged release → **apodictic** and
   **setec-voicewright** each re-pin the vendored SETEC contract (their weekly
   `sync-setec.yml` bot, or an immediate `workflow_dispatch`), run their
   offline drift gate, and auto-open a bump PR.
2. **apodictic** cuts its own tagged release → **APODICTIC-Gemini** re-pins the
   vendored apodictic plugin (its weekly `sync-apodictic-plugin.yml` bot, or an
   immediate dispatch), runs `generate:ui:check`, and auto-opens a bump PR.

The two hops are independent: SETEC's and apodictic's release cadences are
deliberately decoupled. A SETEC release does not force an apodictic release;
the consumer bot simply re-pins SETEC whenever apodictic next syncs.

## Hard rule: producer before consumer

**Cut and PUSH the producer tag before dispatching (or waiting on) any consumer
sync.** The consumer syncs resolve the producer's *latest* by querying the
GitHub release, so the producer release object must already exist:

- **apodictic + voicewright** resolve SETEC via
  `gh release view --repo anotherpanacea-eng/setec-voiceprint --json tagName`
  (the `Resolve SETEC release` step in their `sync-setec.yml`).
- **Gemini** resolves apodictic via `resolveLatestReleaseTag()` in
  `scripts/sync-plugin.mjs`, which hits `/repos/.../releases/latest`.

These are **two distinct resolvers** against two different producers — do not
treat them as one shared mechanism.

**`latest` vs an exact ref.** Both resolvers key off the newest *published
GitHub Release object*, not the newest git tag. If you push a tag but a Release
object has not been published yet, a `latest` dispatch resolves to the *prior*
release. For an immediate, release-object-independent dispatch, always pass the
exact tag: `-f ref=<tag>` (e.g. `-f ref=v1.117.0`). Reserve `latest` for the
Monday cron, which runs after the release object is normally up.

---

## Step A — cut the setec-voiceprint release

SETEC follows the accumulate-then-cut practice: PRs ship `changelog.d/<slug>.md`
fragments and do **not** pin a version; the version + CHANGELOG section are cut
at release time. After the merge commit for the last PR in the wave lands on
`main`:

```bash
git checkout main
git pull

# 1. Assemble the accumulated changelog.d/ fragments into a version section.
python3 tools/assemble_changelog.py --version X.Y.Z --date YYYY-MM-DD

# 2. Bump the plugin version.
#    Edit "version" in plugins/setec-voiceprint/.claude-plugin/plugin.json to X.Y.Z.

# 3. Commit the assembled CHANGELOG + plugin.json bump (merge-commit/PR per the
#    repo's normal flow; small release-cut commits may use the direct-push path).

# 4. Tag from main and push (v1.MAJOR.MINOR convention; CHANGELOG's versioning
#    preamble enforces the v1. prefix).
git tag v1.MAJOR.MINOR
git push origin v1.MAJOR.MINOR
```

Tags are required for the marketplace + plugin-install flow to find the right
version. See `AGENTS.md` §Tagging and §"PRs and merges" for the canonical
accumulate-then-cut text.

---

## Step B — propagate to apodictic + setec-voicewright

Each consumer's bot pulls the new SETEC release, re-derives the vendored
contract via `scripts/sync_setec.py`, runs the offline drift gate, and opens a
bump PR — no human input. Two ways to trigger it:

**Wait for the Monday cron** (no action needed), or **dispatch immediately**:

```bash
gh workflow run sync-setec.yml -R anotherpanacea-eng/apodictic        -f ref=<tag>
gh workflow run sync-setec.yml -R anotherpanacea-eng/setec-voicewright -f ref=<tag>
```

Both consumers use the same workflow filename (`sync-setec.yml`) and the same
dispatch input (`ref`, default `latest`), so the dispatch command is identical
apart from the repo. Pass `-f ref=<tag>` (the exact SETEC tag you just pushed)
for a deterministic pull.

Then **review and merge the auto bump PR** in each consumer.

**Never hand-edit the lock or the vendored fixtures.** Re-pinning is always the
sync script's job: in apodictic, `setec-plugin.lock` + `tests/setec-contract/`
fixtures are derived by `scripts/sync_setec.py`; in voicewright, by the
equivalent sync step. A hand-edited lock or fixture defeats the drift gate that
exists to catch contract drift. To re-derive locally for inspection, run the
sync script with `--check` (apodictic) rather than editing files.

> **Cron times — see the workflows, not this file, for the source of truth.**
> Current values (all Mondays UTC): **Gemini `0 14 * * 1`** (14:00),
> **apodictic sync-setec `0 15 * * 1`** (15:00), **voicewright sync-setec
> `0 16 * * 1`** (16:00, offset from the apodictic sync). If these drift, the
> yamls win.

---

## Step C — cut the apodictic release

apodictic has its own release pipeline. From a clean apodictic checkout:

```bash
bash scripts/release.sh X.Y.Z
```

`release.sh` is a 9-step pipeline: bump version, assemble apodictic's own
`changelog.d/` fragments, regenerate derived files from
`release-registry.json`, build the Codex + Antigravity host bundles, verify
repository consistency, then (steps 7–8) mirror+verify the Gemini public tree
**if** the sibling is checked out (see Step D note), then **step 9 tags and
pushes conditionally**:

- **Clean working tree, tag does not exist** → `release.sh` runs
  `git tag vX.Y.Z && git push origin vX.Y.Z` itself. **Do not push the tag a
  second time** — that would be a double-tag.
- **Tag already exists** → step 9 skips (prints "Skipped: tag … already
  exists").
- **Dirty working tree** → step 9 does **not** tag; it prints the exact manual
  command to run after you commit the release changes:
  `git tag vX.Y.Z && git push origin vX.Y.Z`.

So: run `release.sh X.Y.Z`, read its step-9 output, and only tag manually if it
told you to. apodictic's `.github/workflows/release.yml` then takes over from
the pushed tag — it rebuilds the per-host bundles on the tag and attaches them
to the GitHub Release (the generated `codex/`/`antigravity/` trees are not
committed; the workflow rebuilds them — GitHub #52).

---

## Step D — propagate to APODICTIC-Gemini

Gemini's bot pulls the new apodictic release, re-pins the vendored plugin via
`scripts/sync-plugin.mjs`, regenerates the UI (`npm run generate:ui`), runs the
drift gate (`npm run generate:ui:check`), and opens a bump PR. Trigger it the
same two ways:

**Wait for the Monday cron** (Gemini `0 14 * * 1`), or **dispatch immediately**:

```bash
gh workflow run sync-apodictic-plugin.yml -R anotherpanacea-eng/APODICTIC-Gemini -f ref=<tag>
```

(`ref` = the exact apodictic tag from Step C.) Then **review and merge the bump
PR**. **Never hand-edit the generated UI** (`App.tsx` / `LandingPage.tsx` /
`static-site`) — regenerate it via `generate:ui` (which the bot does for you).

### A note on apodictic's PUSH side (release.sh steps 7–8)

The **pull chain** above (`sync-apodictic-plugin.yml` → `sync-plugin.mjs` →
`generate:ui:check`) is the cross-repo propagation path this runbook drives:
Gemini pulls and re-pins on its own schedule.

Separately, `release.sh` steps 7–8 **push** a copy of the plugin into a
locally-checked-out Gemini sibling: step 7 `rsync`s `plugins/apodictic/` into
`../APODICTIC-Gemini/public/apodictic-plugin/`, and step 8 verifies parity with
`release-verify.mjs --check-sync`. These steps run **only if** the Gemini
sibling directory exists on the release machine; if it is absent, `release.sh`
prints a `WARN` and skips them (`GEMINI_AVAILABLE=0`) — the public release path
is not coupled to the private sibling. Treat the rsync/`--check-sync` push as a
local-mirror convenience that fires when you happen to have Gemini checked out,
**not** as the canonical propagation path. The canonical, machine-independent
path is the pull chain. (If you are cutting the apodictic release on a machine
with Gemini checked out, steps 7–8 are live, gating work and will run — let them
complete or remove the sibling dir to skip them.)

---

## Gotchas

### 1. `gh` OAuth workflow-scope merge 403 (public repos)

A PR that touches `.github/workflows/` cannot be merged with the `gh` OAuth
token on the **public** repos (apodictic, setec-voiceprint): you get a 403
("refusing to allow an OAuth App to create or update workflow"). The *git*
credential keeps the scope (it pushed the branch fine), so the fallbacks are:

```bash
# In a main worktree of the affected repo:
git merge --no-ff origin/<branch>
git push origin HEAD:main          # needs explicit OK for the direct-to-main push
```

…or merge via the **GitHub web UI**. PRs that do **not** touch
`.github/workflows/` merge via `gh pr merge` fine.

**Public vs private:** the 403 is observed on the public repos. **voicewright
is private and has not hit this block** — `gh pr merge` there has worked on
workflow-touching PRs. (Treat this as observed behavior, not a guaranteed
private-repo exemption; if voicewright ever 403s, use the same local-merge
fallback.) See `AGENTS.md` §"Merge mechanics" and hub `PATTERNS.md` §4 for the
verbose mechanics.

### 2. Golden re-splice on stacked / colliding releases

This applies only to **surface-addition** PRs (not docs PRs), but belongs in
the release driver's toolkit because stacked releases can collide on the golden.
When two surface PRs touch `_golden_capabilities.json` and stack, do a
**surgical splice** rather than regenerating the whole golden against a stale
tree: parse the committed golden, swap in only the new entry, and dump with
`json.dump(..., indent=2, sort_keys=False, ensure_ascii=True)` to match the
committed formatting (a full regen reorders unrelated entries and produces a
noisy, conflict-prone diff). For stacked PRs, **retarget the child PR onto
`main` before deleting the base branch**, or GitHub will close the child when
the base is deleted. Hub `PATTERNS.md` §2/§4 has the full surface-addition
checklist.

### 3. Manual bump vs the weekly bot

The consumer re-pins are fully autonomous: the bots resolve the latest producer
release, run the sync script + drift gate, and open a bump PR with no human
input. They run on the **Monday crons** above (Gemini 14:00, apodictic
sync-setec 15:00, voicewright sync-setec 16:00 UTC — *see the workflows for the
authoritative values*). To pull a release in **immediately** instead of waiting
for Monday, use `workflow_dispatch`:
`gh workflow run <sync-workflow> -R <repo> -f ref=<tag>`. **Never hand-edit the
lock or vendored fixtures** — always let the sync script re-derive them.

The bots run usefully **today**: apodictic's `setec-plugin.lock` is already
`tag: v1.116.0`, `provisional: false` (the real release has happened and the
pin is finalized), so `latest` resolves to a real published release and the
weekly sync produces a meaningful bump PR. Some older `FINALIZATION` comments in
apodictic's `sync_setec.py` and `sync-setec.yml` still say the bot "will not run
usefully until the R1 release exists" / "`latest` resolves to nothing" — that
text is **stale**; ignore it. (apodictic #93 retires those comments and the
`len(goldens) >= 9` magic-number guard; this runbook describes current
behavior, not the stale comments.)

### 4. Producer before consumer (restated)

Always cut and push the producer tag — and let the GitHub Release publish —
*before* dispatching the consumer sync, because the consumer resolvers key off
the producer's latest published Release (Step "Hard rule" above). Pass
`-f ref=<exact-tag>` to be release-object-independent.

---

## Cross-machine note

The entire release/dispatch chain is a **Code-Mac** operation. It needs no
models, no corpora, and no GPU — only `git`, `gh`, and the repos checked out.
**No Code-PC step is required** to cut or propagate a release; the calibration
host is only needed for signal calibration work, never for the release train.

## See also

- `AGENTS.md` (this repo) — §Tagging, §"PRs and merges", §"Merge mechanics",
  §"Keeping docs current".
- apodictic `scripts/release.sh` — the authoritative 9-step apodictic release
  pipeline (the source of truth for Step C).
- apodictic / voicewright `.github/workflows/sync-setec.yml`,
  APODICTIC-Gemini `.github/workflows/sync-apodictic-plugin.yml` — the
  authoritative cron times and dispatch inputs (the source of truth for the
  crons quoted above).
- Hub `Cowork/repo-fleet/PATTERNS.md` §1/§4 — the meta-pattern ("the producer
  owns the shared artifacts") and verbose merge-bypass mechanics (maintainer's
  local hub; not reachable from cloud containers).
