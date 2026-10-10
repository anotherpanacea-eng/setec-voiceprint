# TP-SWEEP shard 13: `setec/surfaces/register_sweep.py` (2026-10-08)

Independent review of the 67 unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `plugins/setec-voiceprint/scripts/setec/surfaces/register_sweep.py`. This is
a report only, with no source, registry or checker change.

Fleet custody: fleet-coordination #455 (CAM-12, TP-SWEEP).

## Result: all 67 local

The spec's own constraints already put this module outside the registry. It
says: "S5/G1, author-corpus, and register-sweep envelopes and hashes are
untouched because this spec never edits their shape." The module seals and
verifies register-sweep receipts. It never segments, tokenizes or fingerprints
analyzed prose itself. Classification runs inside `register_classifier.py`,
loaded through a namespace (`:1226`, `:1464`).

| Kind | Sites | Count |
|---|---|---:|
| Frozen schema, key and slot tables | `:120`, `:124`, `:211`, `:218-219`, `:257`, `:268`, `:275`, `:1062`, `:1168`, `:1624`, `:1642`, `:1678`, `:1691`, `:1753`, `:1850`, `:2470`, `:2901`, `:2917`, `:2925`, `:2938`, `:3528`, `:4144`, `:4342-4353` (3) | 26 |
| Receipt and file-identity hashes: `raw_sha256` and `framed_sha256` over exact bytes, file-stat fingerprint bindings, checkpoint and shard row digests | `:427`×2, `:444`×2, `:590`, `:604`, `:735`×2, `:3403`×2, `:3790`, `:4580`, `:4583-4584`, `:4659` | 15 |
| Claim-license output policy: key normalization and the forbidden-value patterns applied to emitted keys and values, not to analyzed text | `:2428`, `:2436`×2, `:2439`, `:2442`, `:2450`, `:2457`, `:2483`×2, `:2484`×2, `:2487`, `:2507`×2 | 14 |
| String validators: NFC required, no stripped edges | `:363`, `:937`, `:4377`, `:4379` | 4 |
| Artifact filename patterns and portable path keys | `:2874`, `:2878`, `:3837`×3 | 5 |
| Rendering `rstrip` | `:2537`, `:2672` | 2 |
| CLI integer pattern | `:4362` | 1 |
| **Total** | | **67** |

The hashes are bare digests over exact bytes or canonical JSON. Under the
owner's 2026-10-08 Q1 ruling they are local in any case.

## Constraint for shard 12's Cohort AB

`register_sweep.py:271` records that "the committed receipt byte-pins
`register_classifier.py`". Shard 12 proposes three rows inside
`register_classifier.py`: `_SENTENCE_TERMINATORS`, `_PARAGRAPH_BREAK` and
`_word_count`. Those rows must be minted strictly in place. An R1 move, or any
re-export edit to `register_classifier.py`, would change pinned bytes, and the
spec says it never changes register-sweep hashes. Shard 12's report carries the
same note.

## Method

1. Ran the checker at `93675ba` and filtered to the module.
2. Read each site and the module's classifier-loading path. No model call.
