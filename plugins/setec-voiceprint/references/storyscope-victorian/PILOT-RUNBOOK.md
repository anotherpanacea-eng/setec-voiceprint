# StoryScope atlas pilot runbook

Owner-approved 2026-09-23: four public-domain novels judged by Sonnet 5 and Opus 5.5.
The runner is `setec.calibration.storyscope_atlas`. The pilot runs on the owner's Claude
subscription through headless Claude Code (`headless`), so it bills nothing to the API.
The Message Batches path (`submit`/`collect`, capped at `--max-usd 5`) is the fallback.

## Boundaries

- Only the four Project Gutenberg novels `fetch --pilot` downloads: Hard Times (786),
  Silas Marner (550), The Mayor of Casterbridge (143) and The Moonstone (155). No private
  corpus text, and nothing from any private-map directory.
- Do not use `submit` unless the owner says to switch to the API. If you do, pass
  `--max-usd 5` to every `submit` and never raise it.
- If the runner refuses or crashes, record the exact error and stop. Code fixes go
  through the pull request, never through local patches.

## Before you start

1. Work in a separate git worktree on this branch. Leave existing checkouts alone.
2. Confirm `claude --version` runs and `claude auth status` shows a subscription login.
   If either fails, stop and report it. Don't install anything or log in on your own.
3. Pick a run directory outside the repo, for example `storyscope-atlas-pilot-2026-09-23`.
   Download `https://raw.githubusercontent.com/jenna-russell/storyscope/main/data/taxonomy.json`
   into it.

## Run

Run these from `plugins/setec-voiceprint/scripts`. Here `M` stands for
`python -m setec.calibration.storyscope_atlas` and `RUN` is the run directory.

```
M fetch --run RUN --pilot
M plan  --run RUN --taxonomy RUN/taxonomy.json
```

`plan` prints a list-price estimate for each step. On the subscription it is a size
check, not a bill.

Before running any model, check `RUN/plan.json`. The chapter count for each work should
be close to the real book (Hard Times 37, Silas Marner 21, Mayor of Casterbridge 45, and The
Moonstone split into several narratives, each with its own chapter run, so check that
one by eye). A count far off means the chapter split is wrong. Report it and stop.

Smoke test first, then the full steps:

```
M build    --run RUN --step cards
M headless --run RUN --step cards --limit 3
```

Open `RUN/results/cards.jsonl` and confirm the three rows are `succeeded`, name
`claude-sonnet-5`, and hold a JSON card. Then:

```
M headless --run RUN --step cards
M build    --run RUN --step features
M headless --run RUN --step features
M build    --run RUN --step gold
M headless --run RUN --step gold
M build    --run RUN --step works
M headless --run RUN --step works
M emit     --run RUN
```

Each `headless` call pins the step's model, effort and system prompt, turns every tool and
local customization off, and runs four calls at a time (`--parallel`). It is resumable:
rerunning the same command only redoes rows that did not succeed. If it stops on a usage
or rate limit, wait for the limit to reset and rerun the same command. Don't lower the
model or skip a step to get around a limit. Exit code 3 means some rows failed without a
limit; rerun once, and if they fail again, report the errors from the results file.

## Check and hand back

Plans bind the work bytes. Once requests exist, use a new run directory to
replan; once a step starts or has results, it cannot be rebuilt in place.
Older runs without the plan/request bindings must also start in a new directory.
This prevents resumed answers from acquiring a different prompt or passage identity.

For the optional batch route, `--max-usd` checks an estimated allowance using
the selected model, maximum output tokens and cold-cache writes. Input token
counts and configured prices are estimates, so this is not an absolute billing
cap; verify prices and allow headroom before submitting. A constant-label gold
sample has undefined kappa and cannot recommend a cheaper judge.

- Read `out/cost.json` (billed USD should be 0; `subscription_list_usd` is what the run
  would have cost at API list price) and `out/agreement.json`. Sample `out/features.jsonl`
  and `out/cards.jsonl`, looking for parse failures, `warnings`, and any `stop_reason`
  other than `end_turn`.
- Copy `out/`, `plan.json` and `estimates.json` to Dropbox under
  `/Cowork/repo-fleet/storyscope-atlas-pilot-20260923/`. Do not copy `works/` or
  `requests/`.
- Report: the Claude Code version; list-price cost per step; how many units parsed
  cleanly; the agreement table (feature, n_pairs, kappa or Jaccard, routing_hint);
  anything that went wrong; and the Dropbox path.
