### Changed

- Relocate the whole `agd_move_scan`, `enthymeme_gapflag`, `fallacy_scan`, and
  `warrant_probe` modules into `setec.surfaces` behind permanent schema-1.x
  legacy aliases. Preserve module/monkeypatch identity, scripts-root paths,
  APIs, judge provenance, claim licenses, reports, and refusals. Only AGD
  retains normalized consumer delivery; the other capabilities are not promoted.
  Add scoped offline copied-plugin launch checks and exact existing-channel
  metadata; the counted bootstrap ceiling remains unchanged. Packaging-only
  PATCH change, not full P3/P4 or live-judge qualification.
