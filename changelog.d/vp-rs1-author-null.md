### Changed

**`period_ruler`: the architecture noise floor now includes between-author variation (VP-RS1).**
The first qualification read showed real windows from held-out authors reading beyond reference
noise, because every pseudo-model was an average over all reference sources and the
leave-one-out percentiles excluded only the window's own work. When reference rows name an
`author`, leave-one-out, the human-vs-human energy band and the pseudo-model nulls now block by
author. Each pseudo-model is drawn from as many real sources as the arm has: its named authors
when every candidate names one, else `--null-blocks` (default 1, so a generated arm is read as
one voice). The block reports `null_blocked_by`, `null_sources_per_pseudo_model` and
`null_sources_from`. Every pseudo-model window is re-ranked against the reference without any
of the pseudo-model's chosen sources, the way the arm's windows are ranked. On synthetic authors
whose offsets are five times the within-author spread, unseen 3- and 6-author arms read beyond
noise in 0 of 20 seeds each (3 of 20 for 6 authors without the re-ranking).
