### Fixed

**Calibration-readiness matrix input labels.** `tools/gen_calibration_readiness.py`
no longer strips the leading flag from an input description, so rows such
as `pan_replay` now read `--signals to restrict reported signals` instead of
losing the flag the reader has to pass. An input is labelled a
register-matched personal baseline corpus only when it names a baseline
directory or corpus (`--baseline-dir`, or "baseline directory",
"baseline directories" or "baseline corpus"); a value flag such as `kicker_density`'s `--baseline` rate keeps its
own wording. `references/calibration-readiness.md` is regenerated.
