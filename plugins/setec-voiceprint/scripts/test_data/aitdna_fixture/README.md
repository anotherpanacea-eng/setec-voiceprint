# Synthetic AITDNA fixture (NOT real AITDNA data)

These files are a tiny, fully synthetic stand-in for the **AITDNA**
dataset (HF `datasets/UKPLab/AITDNA`, CC-BY-SA-4.0, arXiv:2606.04906).
They exist only to exercise the AITDNA benchmark-harness pipeline
(adapter -> runner -> scorer -> report) in CI with **zero model loads**
and **no real AITDNA text**.

- `instances.jsonl` — 14 synthetic rows in the **real AITDNA schema**:
  each row is `{"data": [{text, author, queries}, ...], "metadata":
  {author, human_only, model, temperature, setting, task}}`. `data` is
  the per-token genesis stream; `author` is `"User"` (human) or `"Bot"`
  (AI) — the same vocabulary the real `token`/`membership` configs use.

The 14 rows are constructed to cover every label path the adapter
computes (verified against `aitdna_to_manifest.compute_notion_label`):

  - **6 human-only** docs (all `User` tokens; `metadata.human_only ==
    true`) — label `0`, and the membership/authorship-ID **reference
    corpus** (`human_only == true`).
  - **4 all-AI** docs (all `Bot` tokens) — label `1`, not co-written.
  - **2 co-written AI-majority** docs (Bot ratio > τ=0.5) — label `1`,
    co-written.
  - **2 co-written human-majority** docs (Bot ratio <= τ=0.5) — label
    `0`, co-written. This is the **AITDNA hard case**: co-written yet
    human-labeled, the docs generic detectors flag worst (co-written FPR).

No file here is derived from the real AITDNA dataset. The real release
is fetched locally by the operator at run time (parquet/JSONL) via
`--aitdna-dir`; it is never vendored (CC-BY-SA-4.0 share-alike — a
fetch-only, report-only harness re-publishes no text).
