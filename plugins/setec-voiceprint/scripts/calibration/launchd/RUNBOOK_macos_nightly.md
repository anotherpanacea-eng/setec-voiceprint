# RUNBOOK: nightly sharded-calibration on macOS via launchd

**Audience**: a maintainer who wants to run sharded calibration
overnight on their Mac, with the worker:

  * auto-starting at a scheduled hour (e.g., 23:00 local),
  * preventing idle sleep for the duration (caffeinate -i),
  * exiting cleanly at the morning end of the window (e.g., 06:00),
  * resuming the next night where it left off.

**Scope**: macOS only (Catalina or later). For Linux / WSL2 hosts
see `internal/RUNBOOK_calibration_host.md` (which uses
`systemd-run` or a cron-driven equivalent). For multi-machine runs
see v1.44.2's multi-machine sync RUNBOOK (ships in a follow-up).

**Time to install**: ~10 minutes once `shard_runner shard` has
already produced the run directory.

---

## 0. Prerequisites

```bash
# You already have a sharded run set up:
ls "$BASELINES_DIR/calibration_runs/$RUN_ID/state.json"
# state.json should exist; if not, run `shard_runner shard` first.

# You're using a Python interpreter that has the framework's deps:
which python3
python3 -c "import shard_runner" 2>&1 | head -5
# No ImportError. (Homebrew python3 is the usual choice on
# Apple-Silicon Macs.)
```

You do NOT need:

  * `sudo` (the agent installs at user level).
  * Any system-wide changes to Sleep / Energy preferences. `caffeinate`
    handles idle-sleep blocking process-locally.

---

## 1. Render and inspect (dry-run)

Run `setup_launchd.py` in its default dry-run mode first. This
renders the plist + wrapper to a staging directory and prints the
`launchctl` commands you'd run to install:

```bash
cd "$(dirname "$(python3 -c 'import shard_runner, os; print(os.path.dirname(shard_runner.__file__))')")"

python3 launchd/setup_launchd.py \
    --run-id "raid_tier1_fpr0.01_2026-05-13" \
    --base-dir "$HOME/Documents/ai-prose-baselines-private" \
    --time-window "23:00-06:00" \
    --workers 4 \
    --use validation
```

You'll see:

```
Rendered launchd plist:    ~/.setec-voiceprint/launchd/com.anotherpanacea.setec-voiceprint.shard-worker.plist
Rendered wrapper script:   ~/.setec-voiceprint/launchd/run_shard_worker.sh

Dry-run complete. To install, either re-run with --install, or run
these commands manually:

  cp ~/.setec-voiceprint/launchd/com.anotherpanacea.setec-voiceprint.shard-worker.plist ~/Library/LaunchAgents/com.anotherpanacea.setec-voiceprint.shard-worker.plist
  launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.anotherpanacea.setec-voiceprint.shard-worker.plist

To uninstall later, run this helper with --uninstall.
```

Open the rendered files and sanity-check the paths:

```bash
cat ~/.setec-voiceprint/launchd/com.anotherpanacea.setec-voiceprint.shard-worker.plist
cat ~/.setec-voiceprint/launchd/run_shard_worker.sh
```

You're looking for:

  * The wrapper's `PYTHON_BIN` points at the Python you expect
    (Homebrew on Apple Silicon: `/opt/homebrew/bin/python3`).
  * The wrapper's `SHARD_RUNNER` points at this repo's
    `shard_runner.py`.
  * The wrapper's `BASE_DIR` and `RUN_ID` match your intended run.
  * The plist's `StartCalendarInterval` has `Hour=23, Minute=0`
    (the start of your time window).

---

## 2. Install

Re-run with `--install`:

```bash
python3 launchd/setup_launchd.py \
    --run-id "raid_tier1_fpr0.01_2026-05-13" \
    --base-dir "$HOME/Documents/ai-prose-baselines-private" \
    --time-window "23:00-06:00" \
    --workers 4 \
    --install
```

Output:

```
Installed plist: ~/Library/LaunchAgents/com.anotherpanacea.setec-voiceprint.shard-worker.plist
  Ran: launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.anotherpanacea.setec-voiceprint.shard-worker.plist
```

Verify the agent is loaded:

```bash
launchctl print "gui/$(id -u)/com.anotherpanacea.setec-voiceprint.shard-worker"
```

You should see a block showing `state = waiting` (the agent is
registered and waiting for its scheduled time). The plist's
`KeepAlive` and `StartCalendarInterval` should be visible in the
output.

---

## 3. Test the wrapper manually (optional)

Before waiting for the first 23:00 fire, you can prove the wrapper
works by invoking it directly. **Caution**: this will run a real
worker, scoring real shards. If you want to test without scoring,
either pause the run first (`shard_runner pause-all --run-id ...`)
or use a synthetic run-id.

```bash
~/.setec-voiceprint/launchd/run_shard_worker.sh
```

Watch the log:

```bash
tail -F ~/Library/Logs/setec-voiceprint/shard-worker-$(date +%Y-%m-%d).log
```

Headers should show your run config; subsequent lines should be
shard-worker progress.

To stop the manual test: `Ctrl-C`. The worker's SIGINT handler
exits cleanly between shards.

---

## 4. Observe the first nightly fire

At your `--time-window` start hour (23:00 local in the example),
launchd will fire the agent. Confirm:

```bash
# launchctl-level log (process started, exited):
tail -n 50 ~/Library/Logs/setec-voiceprint/launchd.log

# Worker-level log (per-shard progress):
tail -F ~/Library/Logs/setec-voiceprint/shard-worker-$(date +%Y-%m-%d).log
```

At your time-window end hour (06:00), the worker will detect that
local time is outside the window and exit cleanly. Because the
plist's `KeepAlive.SuccessfulExit=false`, launchd will NOT respawn
the agent — it goes back to `waiting` state until the next 23:00.

---

## 5. Pause / resume mid-run

If you need to stop the nightly worker before its window ends:

```bash
# Cooperative — worker exits between shards.
python3 shard_runner.py pause-all --run-id "raid_tier1_fpr0.01_2026-05-13"

# OR signal-driven — worker exits between shards too, but immediately.
python3 shard_runner.py terminate-all --run-id "raid_tier1_fpr0.01_2026-05-13"
```

To resume on the next scheduled fire, clear the pause marker:

```bash
python3 shard_runner.py pause-all --clear --run-id "raid_tier1_fpr0.01_2026-05-13"
```

The next 23:00 fire will pick up where the previous night left off.
Any shards that were `claimed_pending_resume` get re-claimed by
this host (per spec §2.4, only the original host may resume).

---

## 6. Uninstall

When the run completes (or you want to reconfigure for a different
run_id), uninstall the agent:

```bash
python3 launchd/setup_launchd.py \
    --run-id "raid_tier1_fpr0.01_2026-05-13" \
    --base-dir "$HOME/Documents/ai-prose-baselines-private" \
    --time-window "23:00-06:00" \
    --uninstall
```

This runs `launchctl bootout` and removes the plist from
`~/Library/LaunchAgents/`. The wrapper script and rendered plist
in `~/.setec-voiceprint/launchd/` are left in place so you can
re-install later without re-rendering.

---

## 7. Troubleshooting

### The agent never fires at the scheduled time

```bash
# Is it loaded?
launchctl list | grep setec-voiceprint
# Expected: a line with the label and an integer pid or - (waiting).

# Is the plist syntactically valid?
plutil -lint ~/Library/LaunchAgents/com.anotherpanacea.setec-voiceprint.shard-worker.plist
# Expected: "OK"
```

If `plutil` reports an error, the rendered plist has a bug; please
file an issue with the output.

### The agent fires but exits immediately

```bash
tail ~/Library/Logs/setec-voiceprint/launchd.log
```

Common causes:

  * Wrapper script not executable: `chmod +x ~/.setec-voiceprint/launchd/run_shard_worker.sh`
  * `PYTHON_BIN` path moved (Homebrew upgrade): re-run `setup_launchd.py --install` to regenerate.
  * `BASE_DIR` doesn't have the expected state.json: `shard_runner status --run-id ...`

### The worker logs show "outside time window" and exits immediately

The worker is correctly detecting that local time is outside the
configured window. If this happens at 23:01 when your window is
`23:00-06:00`, double-check the time-window parser:

```bash
python3 -c "
import sys; sys.path.insert(0, '$(pwd)')
import shard_runner as sr
import datetime as dt
w = sr.parse_time_window('23:00-06:00')
print('window:', w)
print('now:', dt.datetime.now())
print('in window:', sr.is_within_time_window(w))
"
```

If `in window: False` when it should be True, the system clock or
timezone is misconfigured.

### Mac wakes from sleep but display stays off

Expected. `caffeinate -i` (the wrapper's choice) blocks **idle**
sleep but allows the display to dim and turn off. Use `caffeinate
-dimu` if you want to keep the display on — but you almost
certainly don't. (You can patch this in the wrapper script if you
need it.)

---

## Reference

  * `SPEC_sharded_calibration.md` §2.8 — launchd + caffeinate design.
  * `SPEC_sharded_calibration.md` §2.5 — time-window semantics.
  * `shard_runner.py work --help` — flag-level reference.
  * `setup_launchd.py --help` — installer-level reference.

For multi-day calibration runs split across multiple machines (Mac
+ AMD desktop, say), see the v1.44.2 multi-machine sync runbook
once it ships. v1.44.1.C only handles the single-host nightly path.
