### Fixed

- `sliding_window_heatmap` now accepts current `variance_audit` JSON envelopes
  containing `results.windows`, alongside raw windows and legacy top-level
  windows. Invented offline loader and file/stdin CLI regressions cover the
  producer envelope and malformed or non-windowed input. Discovery and consumer
  registration remain unchanged.
