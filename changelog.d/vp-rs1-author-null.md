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

**`period_ruler`: the degradation veto is calibrated on unseen authors and reported beside the
in-band share (VP-RS1).** The second qualification read found the veto dropping 16-23% of real
held-out windows and counting them as out of band. The 4-gram repeat count and a now one-sided
distinct-token-ratio cut (a rich vocabulary is not degradation) are set so reference windows,
each with its author left out, are vetoed about 5% of the time. Vetoed windows leave the
in-band share and displacement and are reported as `veto_rate`; an arm with every window vetoed
has no in-band share and reads beyond noise. The block adds `null_veto_rate_interval_95` and the
calibrated thresholds under `veto`.
