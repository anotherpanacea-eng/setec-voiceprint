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

**`period_ruler`: excess vetoing reads beyond noise; uncalibratable ratio veto switches off;
widened null draws exclude every source they drew from (VP-RS1, Codex review).** A veto rate above
the pseudo-model null now reads beyond reference noise, so dropping degraded windows can never
improve an arm's reading. When no distinct-token-ratio cut meets its budget the ratio veto is
switched off with a warning, and the block reports `target_real_veto_rate` beside
`achieved_real_veto_rate`. A pseudo-model whose thin cell widened to other sources re-ranks
without those sources too. Reference rows passed in memory block by `author` when they name one.

**`period_ruler`: noise is judged on the in-band share with vetoes counted as out of band, and a
null with no source-disjoint reference fails closed (VP-RS1, Codex re-review).** The block adds
`in_band_share_with_vetoes`; it, not the clean share, is compared with the pseudo-model floor, so
vetoing an outlying window can never improve the reading even when a looping reference source
lifts the null veto ceiling. When excluding a pseudo-model's sources leaves a window nothing to
rank against, that rank is unavailable, and more than 5% of such draws stops the read.

**`period_ruler`: W1 is judged veto-inclusively too (VP-RS1, Codex re-review round 3).** The block
adds `w1_from_uniform_with_vetoes`, which places each vetoed window's ranks at whichever end of
[0, 1] is worse; by convexity this bounds any ranks those windows could have had. Both noise
statistics are now veto-inclusive, so a veto can never make a reading better.

**`period_ruler`: the veto-inclusive W1 is a true worst-case bound.** Endpoint fills did not bound
interior rank placements (Codex review at 44c7071). `w1_from_uniform_with_vetoes` is now
(1 - a) * W1(unvetoed) + a * 0.5 for a vetoed share `a` of ranks, which by convexity of W1 in the
measure is at least the W1 the arm would have had with any ranks for those windows.
