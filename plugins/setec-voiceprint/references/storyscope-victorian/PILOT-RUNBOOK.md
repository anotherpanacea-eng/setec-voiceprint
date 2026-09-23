# StoryScope atlas pilot runbook

Owner-approved 2026-09-23: about USD 5, four public-domain novels, Sonnet 5 and Opus 5.5
through the Message Batches API. The runner is `setec.calibration.storyscope_atlas`.

## Boundaries

- Pass `--max-usd 5` to every `submit`. The runner refuses anything that would push
  committed spend past the ceiling. Never raise it.
- Only the four Project Gutenberg novels `fetch --pilot` downloads: Hard Times (786),
  Silas Marner (550), The Mayor of Casterbridge (143) and The Moonstone (155). No private
  corpus text, and nothing from any private-map directory.
- If the runner refuses or crashes, record the exact error and stop. Code fixes go
  through the pull request, never through local patches.

## Before you start

1. Work in a separate git worktree on this branch. Leave existing checkouts alone.
2. Confirm Python can `import anthropic` and that API credentials resolve:
   `ANTHROPIC_API_KEY` is set, or `ant auth status` shows a profile. If either is
   missing, stop and report it. Don't install packages or create keys on your own.
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

`plan` prints an estimate for each step. If the total ceiling is over USD 5, stop and
report it. Don't trim anything.

Before you spend anything, check `RUN/plan.json`. The chapter count for each work should
be close to the real book (Hard Times 37, Silas Marner 21, Mayor of Casterbridge 45, and The
Moonstone split into several narratives, each with its own chapter run, so check that
one by eye). A count far off means the chapter split is wrong. Report it and stop.

```
M build  --run RUN --step cards
M submit --run RUN --step cards    --max-usd 5
M build  --run RUN --step features
M submit --run RUN --step features --max-usd 5
M build  --run RUN --step gold
M submit --run RUN --step gold     --max-usd 5
M collect --run RUN --step cards    --wait
M collect --run RUN --step features --wait
M collect --run RUN --step gold     --wait
M build  --run RUN --step works
M submit --run RUN --step works    --max-usd 5
M collect --run RUN --step works    --wait
M emit   --run RUN
```

## Check and hand back

- Read `out/cost.json` and `out/agreement.json`. Sample `out/features.jsonl` and
  `out/cards.jsonl`, looking for parse failures, `warnings`, and any `stop_reason` other
  than `end_turn`.
- Copy `out/`, `plan.json` and `estimates.json` to Dropbox under
  `/Cowork/repo-fleet/storyscope-atlas-pilot-20260923/`. Do not copy `works/` or
  `requests/`.
- Report: actual cost per step; how many units parsed cleanly; the agreement table
  (feature, n_pairs, kappa or Jaccard, routing_hint); anything that went wrong; and the
  Dropbox path.
