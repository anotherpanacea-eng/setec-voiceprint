### Fixed

- `sliding_window_heatmap` emits metadata-only structured refusals when JSON
  output is requested while preserving direct CLI failure exits. Both output
  paths are checked before rendering or partial writes. File-mode `setec_run`
  honors strict recognized refusals from nonzero children; legacy wrapping,
  success delivery and discovery registration remain unchanged.
