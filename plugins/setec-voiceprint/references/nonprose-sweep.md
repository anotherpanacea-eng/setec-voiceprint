# Deterministic non-prose sweep

`nonprose_sweep.py` is a bounded, stdlib-only corpus-hygiene screen for a B2
descriptor JSONL and the strict-UTF-8 documents it names. It reports four fixed
structural conditions for operator review:

- any recognized WebVTT header or cue timing line;
- recognized speaker labels on strictly more than 15% of nonempty lines;
- a closed disfluency lexicon at strictly more than 6 hits per 1,000 analyzable words;
- 1-5-word lines on strictly more than 55% of nonempty lines, when there are more
  than 15 nonempty lines.

These are operational, uncalibrated queue conditions. They do not license corpus
disposition, authorship, provenance, quality, genre, AI/human, or training-use
conclusions. `authored_residual_words` is only the complement of structurally
recognized transcript words.

## Run

```bash
python3 plugins/setec-voiceprint/scripts/nonprose_sweep.py \
  --manifest MANIFEST \
  --report-out REPORT
```

Both paths are required. `REPORT` must not exist. The descriptor consumes only a
unique opaque `id` and a relative `/`-separated `path` per data row. Each source
must remain beneath the pinned manifest parent. No discovery, overwrite, threshold
override, source rewrite, registration, model, network, API, or GPU path exists.

The private report is canonical `setec-nonprose-sweep-report/1` JSON with document
metrics keyed by opaque ID, aggregate totals, fixed thresholds, a raw-manifest seal,
and a source-set seal. It contains no paths, prose, tokens, speaker names, VTT
payloads, per-document content digests, or inference fields. Stdout is a separate
aggregate-only SETEC envelope and contains no IDs. Both artifacts use byte-exact
UTF-8 with one terminal LF.

Success exits 0. Invalid arguments exit 2. Any input, resource, identity, or
create-new publication refusal exits 3. An unexpected invariant failure exits 1.
Diagnostics are fixed and never echo operands or source content.

Resource ceilings are 8 MiB per manifest, 10,000 rows, 8 MiB per document,
256 MiB total document bytes, 200,000 lines and 2,000,000 words per document,
1 MiB per physical line, and 16 MiB for the canonical report. See
[`specs/72-nonprose-sweep.md`](../../../specs/72-nonprose-sweep.md) for the exact
grammar, partition, seals, I/O invariants, and acceptance tests.
