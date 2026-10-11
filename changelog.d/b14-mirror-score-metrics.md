### Changed

**`external_mirror_workflow` score takes `--metrics`.** `workflow.py score` now
passes `--metrics` through to `compute_distances.py`. Before, the distance step
always tried every metric, so an operator without sentence-transformers could
not score a run. `--metrics word_jaccard` now scores without it. Leaving the
flag out keeps the old behavior.
