# Mirror gate v3 (internal helper)

`scripts/_mirror_gate.py` is an internal packet-production guard, not a public
SETEC capability or authorship signal. Run it from the repository root:

```bash
python3 plugins/setec-voiceprint/scripts/_mirror_gate.py SOURCE MIRROR \
  --register unknown --quote-spans regions.json
```

On Windows, use the same `python` command form; the helper writes UTF-8 JSON
and diagnostics directly to binary streams, so its output always uses LF.

`--register` is `unknown` by default. Register changes policy as follows:

| Register | Complete SHA-bound sidecar | Automatic discovery | Entity floor | Low similarity |
|---|---|---|---|---|
| `published` | Required, including an empty list | Supplemental | Advisory below `0.90` | Advisory below `0.15` |
| `informal` | Required, including an empty list | Supplemental | None | Advisory below `0.15` |
| `unknown` | Optional | Structural-only | None | Advisory below `0.15` |

Sidecars use schema `setec-mirror-quote-regions/1` and this closed shape:

```json
{
  "schema_version": "setec-mirror-quote-regions/1",
  "source_sha256": "64 lowercase hexadecimal characters",
  "complete": true,
  "regions": [
    {"spans": [{"start_byte": 10, "end_byte": 20}]}
  ]
}
```

The SHA-256 binds the exact raw source bytes. Region and span order is source
order; ranges are nonempty, nonoverlapping, half-open raw UTF-8 byte ranges on
code-point boundaries. Multiple slices represent one logical automatic block;
an additional bare annotation has exactly one slice.

Completed evaluations exit 0 and emit exactly one aggregate-only JSON object,
even when a hard gate fails. Usage errors exit 2 with `usage_error`. Input and
internal errors exit 3. Their closed codes are:

- input: `input_unreadable`, `input_too_large`, `input_invalid_utf8`,
  `input_token_limit`;
- sidecar: `sidecar_unreadable`, `sidecar_too_large`,
  `sidecar_invalid_utf8`, `sidecar_invalid_json`, `sidecar_invalid_schema`,
  `sidecar_stale`, `sidecar_span_limit`; and
- unexpected failure: `internal_error`.

Expected failures write only `mirror_gate_error:<code>` plus LF to stderr and
leave stdout empty. The result intentionally never reports text, paths, hashes,
offsets, entity values, or token positions. Structural discovery cannot
establish that arbitrary unmarked bare quotes have been enumerated.

All registers use the same hard gates. Similarity below `0.15` is advisory in
all registers. The scalar entity-retention floor below `0.90` is advisory only
for `published`; it is `null` for `informal` and `unknown` and never changes the
hard entity gate.
