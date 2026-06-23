# Synthetic Voight-Kampff fixture (NOT real PAN data)

These files are a tiny, fully synthetic stand-in for the PAN@CLEF
Voight-Kampff Subtask-1 release. They exist only to exercise the
benchmark-harness pipeline (adapter -> runner -> scorer -> report) in CI
with **zero model loads** and **no real PAN text**.

- `instances.jsonl` — synthetic instance rows (`id` + `text`).
- `truth.jsonl` — the matching gold labels (`id` + `label`; `0` = human,
  `1` = machine), in a **separate truth file** (the join-on-id path).
- `instances_inline.csv` — a CSV variant carrying the label **inline** on
  the row (the single-file path), BOM-prefixed to test `utf-8-sig`.

No file here is derived from the real PAN dataset (Zenodo 14962653). The
real release is redistribution-gated and is staged locally by the
operator at run time via `--pan-dir`; it is never vendored.
