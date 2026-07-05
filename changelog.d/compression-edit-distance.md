### Added

**`compression_edit_distance_audit.py` — paired-input mechanical edit-magnitude
(new `compression_edit_distance` surface, stdlib, `literature_anchored`).** Given
BOTH a pre-edit draft (`--reference`) and the post-edit version (`TARGET`), the
`compression_edit_distance_audit` capability measures the informational
edit-distance between them via LZ-family (raw LZMA2) compression: `distance_raw =
max(0, C(reference + target) - C(reference))` (clamped: a marginal negative
value is an encoder-parse artifact, disclosed via `clamped_negative_artifact`) is the incremental compressed cost of
encoding the edited text GIVEN the original (`C(s) = len(raw-LZMA2(s))` at
preset `9|EXTREME`, `FORMAT_RAW` — deterministic for the pinned filter chain,
container/header-free, stdlib `lzma`; the 64 MiB dictionary spans whole
manuscripts — raw DEFLATE's 32 KiB window was rejected on review because beyond
it a verbatim excerpt of a manuscript-scale reference scored as a heavy edit), and
`distance_normalized = distance_raw / C(target)` expresses it as a fraction of the
target's standalone compressed size. A small distance means the target is largely a
near-copy of the reference (little editing); a large distance means they share
little (heavy editing / unrelated content). Reimplements the method of
*Assessing Human Editing Effort on LLM-Generated Texts via Compression-Based Edit
Distance* (Devatine & Abraham, **arXiv:2412.17321**; code + data CC-BY-4.0 at
github.com/NDV-tiime/CompressionDistance — reimplemented, not vendored); the
paper's edit-time correlation is **[UNVERIFIED on SETEC's corpus]**.

This is the **mechanical, PAIRED-input** case (the operator has both texts, so no
model is needed) — distinct from spec 13's single-input, model-based
`edit_magnitude` (a RoBERTa regressor over ONE text). The directional form matches
the paper's edit-EFFORT semantics (pre→post), NOT symmetric NCD. Descriptive only:
the `ClaimLicense` refuses any absolute `% AI-edited`, any dosage claim, any
provenance/authorship inference, per-sentence localization, and cross-corpus
generalization, licensing only "the informational edit-distance between the two
supplied texts" (no `is_ai` / `label` / `verdict` / `percent_ai` key). Paired-input
is **load-bearing**: with no `--reference` the CLI **fails loud** (nonzero exit,
`error: --reference is required (paired-input only; …)` before any JSON) rather than
degrade to a single-document mode. Uncalibrated (`literature_anchored`, no shipped
model/calibration → no `corpus_provenance`, consistent with spec 13). Ships two
hand-checked golden pairs (a minimal-edit high-similarity pair; a major-edit
low-similarity pair) with fixed-precision raw + normalized values.
