# RUNBOOK: multi-machine sharded calibration (v1.44.2)

**Audience**: a maintainer running sharded calibration across two or
more hosts (e.g., a Mac + an AMD desktop), sharing one logical
sharded run.

**Scope**: the cross-host coordination layer. Per-host setup (launchd
on macOS, systemd/cron on Linux) is in
`launchd/RUNBOOK_macos_nightly.md` and the host-specific
`internal/RUNBOOK_calibration_host.md`.

---

## 0. When to use this

Use multi-machine sync **only** when you actually have two or more
hosts that can each contribute scoring time to the same calibration
run. Common scenarios:

  * Mac (slow but always-on) + AMD desktop (fast but only on when
    the user is home). Mac runs nights; desktop runs daytimes.
  * Two desktops in different rooms / households (a colleague helps).
  * A laptop that the operator carries between locations with
    different networks but the same git remote.

Single-host setups are simpler with no sync at all: state.json
lives in `~/Documents/ai-prose-baselines-private/calibration_runs/`,
no git involvement, ``--no-sync-state`` is unnecessary because
auto-detect sees no `.git` ancestor and skips sync.

---

## 1. Two coordination strategies

The framework supports two patterns. Per spec §2.7, **deterministic-split
is the recommended default** for 2-host setups because it eliminates
the coordination protocol entirely.

### 1.1 Deterministic split (recommended for 2 hosts)

Each host claims a fixed subset of shard IDs. No git sync is
required: state.json is local to each host, and each host writes
to its own subset only. The aggregation step combines both hosts'
shard caches at the end.

```bash
# Host A (Mac): claim shards 000..039 only.
python3 shard_runner.py work --run-id RUN \
    --workers 4 \
    --no-sync-state \
    --time-window 23:00-06:00
# (manually delete shards/040/.claim..shards/079/.claim from Host A's
# disk so the worker never picks them up — or just trust that the
# faster Host B will claim them first)

# Host B (AMD desktop): claim shards 040..079 only.
python3 shard_runner.py work --run-id RUN \
    --workers 8 \
    --no-sync-state
# (same: manually fence off shards 000..039)
```

This is the **dumbest path that works**. It scales poorly past 2
hosts (each new host needs a hand-allocated shard range), and the
"manual fence" step is error-prone, but for 2 hosts it's robust
and trivially debugged.

Aggregation: copy each host's shard cache directory into a shared
location (Obsidian sync, rsync, or a USB stick), then run
`shard_runner aggregate` against the merged tree. The aggregator
doesn't care which host produced which shard — it just consumes
the `state.json` and the per-shard caches.

### 1.2 Git-synced state (recommended for >2 hosts, or when shard
allocation should be dynamic)

When `calibration_runs/<run_id>/state.json` lives inside a git
working tree with a configured remote, the framework auto-detects
and starts syncing. Each state transition (claim, done, failed,
resume) becomes a commit + push. Each worker pulls before
reading. This gives **eventual consistency across hosts** with no
hand-allocation of shard ranges.

The tradeoffs:

  * **Pro**: dynamic allocation; the faster host claims more shards;
    new hosts can join mid-run.
  * **Pro**: state.json is auto-backed-up via git history.
  * **Con**: every state transition pays a git round-trip (typically
    1-5 seconds for github.com over reasonable internet). For a
    100-shard run with ~3 transitions per shard, that's ~5-15
    minutes of total git overhead across the run.
  * **Con**: requires network. A worker with no connectivity
    silently falls back to local-only and the next sync catches up.
  * **Con**: rare same-shard cross-host races require
    `resolve-conflict`.

---

## 2. Setting up git-synced state

### 2.1 Prerequisites

A git remote that both hosts can push to:

  * The simplest path: a private GitHub / GitLab repo dedicated to
    `calibration_runs/`. No need to commit corpus data — just the
    state.json plus optionally the manifest files.
  * Alternative: a self-hosted git remote (gitolite, gitea) on the
    LAN. Lower-latency, no internet dependency.
  * **Not recommended**: the same repo as the framework itself.
    State.json commits clutter the framework's history and conflict
    with framework-development PRs.

### 2.2 One-time per-host setup

On Host A:

```bash
# Clone or init the calibration-state repo.
mkdir -p ~/Documents/ai-prose-baselines-private
cd ~/Documents/ai-prose-baselines-private
git init
git remote add origin git@github.com:youruser/private-calibration-state.git
git config user.email "you@example.com"
git config user.name "Host A (Mac)"

# Run the shard subcommand to set up the run directory.
python3 path/to/shard_runner.py shard \
    --source-manifest path/to/manifest.jsonl \
    --run-id raid_tier1_fpr0.01_2026-05-13 \
    --shard-size 100000 \
    --shuffle-seed 42 \
    --fpr-target 0.01 \
    --no-tier2 --no-tier3

# Commit and push the initial state.
git add calibration_runs/raid_tier1_fpr0.01_2026-05-13/state.json
git commit -m "init sharded run raid_tier1_fpr0.01_2026-05-13"
git push -u origin main
```

On Host B:

```bash
mkdir -p ~/Documents/ai-prose-baselines-private
cd ~/Documents/ai-prose-baselines-private
git clone git@github.com:youruser/private-calibration-state.git .
git config user.email "you@example.com"
git config user.name "Host B (Desktop)"
```

You also need to copy the shard MANIFESTS (not state.json) from
Host A to Host B — those are large and shouldn't live in git. Use
Obsidian, rsync, or a USB stick. The framework reads each shard's
manifest from disk before scoring it; the state.json is only the
coordination layer.

### 2.3 Verify auto-detect

```bash
cd ~/Documents/ai-prose-baselines-private
python3 -c "
import sys, pathlib
sys.path.insert(0, 'PATH/TO/shard_runner_parent')
import shard_state as ss
sp = pathlib.Path('calibration_runs/raid_tier1_fpr0.01_2026-05-13/state.json')
print('state.json:', sp)
print('is_git_synced:', ss.is_git_synced(sp))
"
# Expected: is_git_synced: True
```

If this prints False, check that the parent `.git/` exists (`ls
-la ~/Documents/ai-prose-baselines-private/.git`).

### 2.4 Run the worker

No special flag is needed — sync is auto-detected. The worker
runs ``git pull --rebase`` before each state transition and
``git add && git commit && git push`` after.

```bash
python3 shard_runner.py work --run-id raid_tier1_fpr0.01_2026-05-13 --workers 4
```

You can disable sync at any time with `--no-sync-state`. Typical
reasons: debugging, network down, or temporarily switching to the
deterministic-split mode for a stretch.

---

## 3. Failure modes

### 3.1 Network blip during pull or push

**Symptom**: worker log shows
`[sync] worker-N: pull failed (continuing with local state): ...`

**Behavior**: the worker logs the error and continues with its
local state. The next successful pull will bring in any updates
the worker missed. The next successful push will catch up the
remote.

**Recovery**: nothing required from the operator. The state is
eventually consistent.

### 3.2 Push race (non-fast-forward)

**Symptom**: invisible to the operator. The worker's `push_state`
catches the rejection, runs `git pull --rebase`, and retries.

**Behavior**: built-in retry up to 3 times (configurable in
`push_state`). After 3 failures the worker logs and continues
locally.

### 3.3 Real cross-host conflict on state.json

**Symptom**: worker exits with rc=6 and stderr:
```
worker-0: state.json sync conflict on claim of shard 042: ...
Run `shard_runner resolve-conflict --run-id RUN` to inspect.
```

This means git's pull-rebase ran into a real conflict in
state.json — typically because two hosts touched the same shard's
fields. The framework's atomic-rename claim files DO NOT prevent
this cross-host (claim files are local-filesystem only); the git
layer catches it.

**Failed-state precedence (v1.54.0+).** When `merge_state_files`
encounters two sides that disagree on the same shard, `failed` is
terminal — the only state that overrides `failed` is `done`. Any
non-`done` competing state (`pending`, `claimed`, or
`claimed_pending_resume`) yields to `failed`. This prevents a
remote-side `sweep-stale` from silently resurrecting a failed
shard back to `pending`. If the auto-merge surfaces a `failed`
shard you don't expect, the failure was real — check the host
that recorded it for the underlying scoring error before
manually overriding to any other state.

**Recovery**:

```bash
cd ~/Documents/ai-prose-baselines-private
python3 path/to/shard_runner.py resolve-conflict --run-id RUN

# If the auto-merge succeeds (no unresolved shards), the merged
# state.json is staged. Continue the rebase:
git rebase --continue
git push

# If the auto-merge reports unresolved same-shard claims, the
# helper exits rc=7 and prints which shards need manual review.
# Pick one host's claim (typically the more recent claim_at), edit
# state.json, then:
git add calibration_runs/RUN/state.json
git rebase --continue
git push
```

Pass `--continue-rebase` to the helper to do the `git add` +
`git rebase --continue` automatically when the merge is clean:

```bash
python3 shard_runner.py resolve-conflict --run-id RUN --continue-rebase
```

### 3.4 Permanently-offline host

If one of the hosts is permanently gone (laptop stolen, hardware
failure) and was holding `claimed_pending_resume` shards, the
remaining hosts can't normally pick those up (per spec §2.4 only
the original host may resume). Use `sweep-stale --include-resume`
from a remaining host to release them:

```bash
python3 shard_runner.py sweep-stale --run-id RUN --include-resume
```

The released shards return to `pending` with their partial-progress
fields cleared, ready for a fresh scoring run on any host.

---

## 4. Operator checks during a multi-machine run

### Daily progress check

```bash
cd ~/Documents/ai-prose-baselines-private
git pull --quiet
python3 shard_runner.py status --run-id RUN --json | jq .
```

Look for the `counts.done` figure to grow over time and `pending`
to shrink. `failed` should stay 0 (or near it).

### Identify which host claimed which shards

```bash
git pull --quiet
python3 -c "
import json, pathlib
sp = pathlib.Path('calibration_runs/RUN/state.json')
state = json.loads(sp.read_text())
from collections import Counter
by_host = Counter()
for sh in state['shards'].values():
    by_host[sh.get('claimed_by_host', 'unclaimed')] += 1
for host, count in by_host.most_common():
    print(f'{host}: {count}')
"
```

### Per-shard ownership history

```bash
git log -p calibration_runs/RUN/state.json | grep "claimed_by_host"
```

Each commit's diff shows when a host claimed a shard. Useful for
debugging "why did this take so long" or "did the AMD desktop
actually run last night."

---

## 5. Tearing down

When the run completes (all shards `done`), aggregate:

```bash
cd ~/Documents/ai-prose-baselines-private
git pull --quiet  # ensure latest state
# Make sure all shard caches are present locally; rsync from the
# other hosts if not.
python3 shard_runner.py aggregate \
    --run-id RUN \
    --fpr-target 0.01 \
    --out PROVENANCE-entry.json
```

Then commit the survey artifact and any updates to PROVENANCE.md
back to the framework repo (not the state-only repo). The
state-only repo's history is preserved as the audit trail of
which host did what when.

---

## 6. Reference

  * `internal/SPEC_sharded_calibration.md` §2.7 — multi-machine sync design.
  * `internal/SPEC_sharded_calibration.md` §4.3, §4.6 — network and conflict failure modes.
  * `shard_runner.py work --help` — flag-level reference.
  * `shard_runner.py resolve-conflict --help` — conflict resolution.
  * `shard_runner.py sweep-stale --help` — stale-claim release.
