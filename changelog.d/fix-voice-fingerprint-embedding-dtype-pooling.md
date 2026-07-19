### Fixed

**`voice_fingerprint` no longer crashes on bf16 encoder weights, and the StyleDistance encoder now mean-pools instead of reading an untrained CLS head.** Two defects that only surface when real weights load — the stub suite never instantiates a model, so both passed CI and were caught by the M2 GPU smoke run:

- **bf16 → numpy.** `_LUAREncoder` called `.detach().cpu().numpy()` directly on the model output. A bf16 checkpoint (StyleDistance ships bf16 weights) has no numpy dtype, so this raised `TypeError: Got unsupported ScalarType BFloat16` before any distance was computed. The tensor is now cast with `.float()` first — a no-op on the fp32 LUAR/Wegmann path and the fix on the bf16 path.
- **Wrong pooling manifold.** `_StyleDistanceEncoder` preferred `pooler_output` when the model exposed it. StyleDistance ships as a sentence-transformers model whose `1_Pooling` config sets `pooling_mode_mean_tokens=true`; the RoBERTa `pooler_output` is the *untrained* CLS dense+tanh head — a different manifold that collapses the embedding and silently produced meaningless voice distances rather than an error. The encoder now always mask-weighted mean-pools the token states, matching the published pooling config.

Also adds `scripts/calibration/fetch_pan24_voightkampff.py`, a local-only fetcher for the PAN@CLEF 2024 Voight-Kampff bootstrap corpus (Zenodo `10.5281/zenodo.10718757`). It follows `fetch_pangram_editlens.py`: requires a destination beneath an `ai-prose-baselines-private` marker directory, writes a `NOTICE.md` recording the research-use-only / no-redistribution terms, verifies Zenodo's pinned archive digest before an atomic cache publish, and bounds archive members and uncompressed size before refusing any member that would extract outside the destination. No corpus material is committed.

No capability surface, schema, envelope, or contract-fixture change.
