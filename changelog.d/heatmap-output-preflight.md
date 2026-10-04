### Fixed

- `sliding_window_heatmap` checks both output paths against the private-output
  rule before writing anything, so a refused JSON path no longer leaves a
  Markdown report behind.
