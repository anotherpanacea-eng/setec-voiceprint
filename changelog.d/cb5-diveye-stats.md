### Changed

**`diveye_signals` no longer imports `surprisal_audit` (CB-5).**
It lazily imported the surface's mean, SD, skew, excess-kurtosis and ACF
helpers and kept same-formula stdlib copies as a fallback. It now always uses
its own copies, so the import, the dispatch and one layering exemption are
gone. Signal values are unchanged.
