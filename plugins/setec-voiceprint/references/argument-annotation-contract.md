# Argument annotation candidate contract (fixture-only)

`setec.core.argument_annotation_contract.validate_candidate_bundle(source_bytes, block_map_bytes, candidate_bytes)` is a pure, importable Python helper. All three inputs must be exact UTF-8 `bytes`. It returns `ValidationResult(candidate_projection, validation_summary)` or raises `ValidationError` with a stable `reason` and optional zero-based `ordinal`. It reads no files, calls no model or provider, and writes no output.

Validation success establishes only that a structured **candidate** matches this mechanical contract. It does not establish source admission, correct extraction or paragraph boundaries, independent labelers, valid role judgments, adjudication, accepted labels, B1/B2 statistics, or a baseline. The candidate is the exact structured record at this boundary. A later provider adapter must retain and bind original provider responses and parse diagnostics separately.

## Byte artifacts

The cleaned source is strict UTF-8. Map spans are half-open Unicode codepoint offsets into the decoded string; slice hashes use the slice re-encoded as UTF-8 without normalization. All `sha256` fields are lowercase 64-character hex. The block map binds the exact source bytes, while the candidate binds the exact block-map bytes. Neither binding is a raw PDF/HTML locator or an extraction recipe.

Block-map JSON root, schema `setec.argument_block_map.v1`:

```json
{"schema":"setec.argument_block_map.v1","source_sha256":"<sha256>","nodes":[{"id":"p1","kind":"prose","start":0,"end":12,"text_sha256":"<sha256>","disposition":"measure"}]}
```

The example is structural; its placeholder hashes and span are not runnable data. Root and node key sets are exact. IDs are unique nonempty strings. Nodes appear in source order, have nonoverlapping positive spans, and carry the exact hash of each slice. Uncovered characters may be whitespace only. The only kind/disposition pairs are `prose/measure`, `boundary/substantive_nonprose`, and `excluded/layout_only`. Prose and boundary spans must contain a non-whitespace codepoint. The map needs at least one prose node. A substantive nonprose boundary covers its own source span and ends the current adjacency run. A layout-only exclusion does not end the run. The helper cannot judge whether a declared block classification is substantively right.

Candidate JSON root, schema `setec.argument_annotation_candidate.v1`:

```json
{"schema":"setec.argument_annotation_candidate.v1","block_map_sha256":"<sha256>","annotator_run_id":"run-1","entries":[{"unit_id":"p1","role":{"value":"support","state":"assigned","reason":null},"mode":{"value":"exposition","state":"assigned","reason":null}}]}
```

The root, entry and field key sets are exact. `annotator_run_id` is an opaque nonempty string; it does not prove independence. Entries contain every prose ID exactly once and in map order. Boundary and excluded IDs do not receive entries. Missing, extra, duplicate, reordered and wrong-type IDs refuse. An absent entry differs from a present entry with an explicit missing state. JSON duplicate keys, non-finite numbers, wrong roots/types, unknown keys and unsupported schemas refuse.

Role and mode states are independent:

| State | Value | Reason | Allowed field |
| --- | --- | --- | --- |
| `assigned` | Member of the unchanged `ROLE_OPTIONS` or `MODE_OPTIONS` taxonomy | `null` | Both |
| `substantive_abstention` | `null` | `no_supported_argumentative_function` | Role only |
| `adjudication_pending` | `null` | `insufficient_context` or `unresolved_interpretation` | Both |
| `missing_or_malformed` | `null` | `missing_response_field` or `unsupported_response_value` | Both |
| `provider_failure` | `null` | `provider_failure` | Both |

The helper does not coerce labels, fill gaps, infer states, sort entries, or accept a first duplicate. In particular, an abstained role can coexist with an assigned exposition mode.

## Result custody

`candidate_projection.first_prose_unit_id` identifies the first prose unit even when excluded or boundary nodes lead the map. Its ordered `units` retain each prose `unit_id`, integer `adjacency_run_id`, role/mode value and role/mode state. Unassigned prose remains in place; it cannot make its neighbors adjacent. A boundary advances the run for later prose, while layout-only exclusions leave it intact. The projection has no copied source-body field, but caller-supplied IDs may contain sensitive prose. Keep it as private candidate data.

`validation_summary` contains schema identities, exact source/map/candidate byte hashes, total and per-kind node counts, and separate role/mode state counts. It contains no body text, unit IDs, label values, path, time or model-validity claim. Do not publish a projection as if it were the aggregate receipt. On any validation error there is no result or partial projection; exception messages contain only a reason code and optional ordinal.

This increment has no CLI, capability registration, source extraction, adjudication, accepted-label artifact, statistics, production YAML write or consumer integration. Source admission and the later execution/report contracts remain separate decisions.
