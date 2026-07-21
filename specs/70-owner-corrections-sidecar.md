# 70 - Owner-corrections sidecar applier

> Canonicalize the repeated owner-correction staging pattern as a deterministic,
> fail-closed JSONL pre-registration pass without mutating source manifests.

- **Status:** In build (`codex/owner-corrections-sidecar`)
- **Tier:** near-term
- **GPU required:** no
- **Source contract:** fleet refill packet B4 and update-14 Windows portability guidance
- **License decision:** N/A - stdlib-only local metadata transformation

## Motivation and compatibility boundary

Three private staging trees now use the same conceptual operation: an owner
selects one manifest row, rewrites reviewed classification metadata, and records
why. The private artifacts are not public fixtures and their exact field names
are not a code-safe contract. This build therefore defines the first canonical
public schema, `setec-owner-correction/1`; it does **not** claim byte- or
field-name compatibility with private predecessors. Private folds require a
schema-only adapter into this format.

This is an operator-controlled metadata correction, not an inference. It does
not inspect prose, infer a label, map date buckets, alter source text, or claim
that a correction is substantively right.

## Artifacts and pipeline boundary

- Add `plugins/setec-voiceprint/scripts/apply_owner_corrections.py`, usable as a
  CLI and as an importable stdlib module.
- Add the canonical schema and examples to
  `plugins/setec-voiceprint/references/manifest-schema.md` and document the
  pre-registration pipeline in the scripts README.
- The applier writes an explicit corrected manifest beside its source manifest.
  Existing compatible registration passes consume that output through their
  existing `--source-manifest` arguments. They gain no implicit sidecar
  discovery and no default behavior change. Synthetic integration tests must
  feed corrected output to both `normalize_author_registry.py` and a
  non-`document_local` `author_corpus_export.py` registration path, proving that
  the reviewed register/era and corrected-manifest provenance are consumed.
- `author_corpus_export.py` remains unchanged. In particular, a
  `document_local` attestation binds the manifest bytes; operators must not
  substitute corrected bytes under an existing attestation. A newly attested
  corrected manifest is a separate workflow outside B4. A test must prove an
  old attestation fails closed against corrected manifest bytes.
- This supporting transformation is not a new task surface and gets no new
  `capabilities.d` entry or claim license.

## Canonical correction schema

Each nonblank correction line is exactly one JSON object:

```json
{
  "schema": "setec-owner-correction/1",
  "match": {"id": "doc-1", "content_hash": "sha256:..."},
  "expect": {"register": "blog_essay"},
  "rewrite": {"register": "personal"},
  "note": "owner-reviewed classification"
}
```

The closed top-level key set is `schema`, `match`, optional `expect`,
`rewrite`, and `note`. `expect` defaults to `{}`. Unknown keys, duplicate JSON
keys at any nesting level, non-object lines, a UTF-8 BOM, invalid UTF-8,
`NaN`/`Infinity`, or a schema other than `setec-owner-correction/1` refuse the
complete operation.

### `match`

- A nonempty object of exact top-level string equalities. Keys are limited to
  `id`, `path`, `source_id`, and `content_hash`; at least one is required, and
  every value must be a nonempty, non-whitespace string. Before matching, every
  candidate manifest row must itself carry nonempty string `id` and `path`
  values; malformed identity rows refuse the complete operation.
- All supplied predicates are ANDed and evaluated against the immutable
  original manifest. Equality is type-sensitive and case-sensitive. No glob,
  regex, coercion, Unicode normalization, filesystem resolution, host
  case-folding, or path-separator normalization occurs. A stored `path` is a
  JSON string, not a filesystem lookup.
- Each rule must match exactly one manifest row. Zero or multiple matches
  refuse before output.
- Duplicate manifest `id` values refuse even if another selector would make a
  rule unique. This applier is not a repair path for broken row identity.

### `expect`

- An optional nonempty-or-empty object of exact top-level string preconditions.
  Keys are limited to `register` and `era` and must already exist in the
  selected original row. Values must be nonempty, non-whitespace strings but
  may be legacy/open-set labels: `expect` guards old state and does not license
  an output value.
- If the requested rewrite is not already present, every expectation must
  match exactly or the complete operation refuses as stale.
- Validate the complete correction schema, including every expectation type,
  before matching or testing idempotence. If every requested field already has
  the requested value, the rule is
  `already_applied`; stale expectations do not make an otherwise identical
  second run fail. This is the idempotence rule.

### `rewrite`

- A nonempty object of exact top-level replacements. There is no delete,
  append, merge, nested-path, or null-as-delete operation.
- The v1 allowlist is exactly `register` and `era`, the two packet-evidenced
  reviewed classifications. Values are nonempty strings from the existing
  `manifest_validator.ALLOWED_REGISTER` and `ALLOWED_ERA` constants.
- Identity, filesystem, and content-integrity fields are immutable, including
  `id`, `path`, `content_hash`, `source_id`, `word_count`, `author`, and
  `persona`; so are provenance, consent, privacy, authorship/training posture,
  text notes, and all other fields. Any other rewrite key refuses.
- After its own strict register/era checks, the applier validates the complete
  candidate manifest with `manifest_validator.validate_manifest` from a
  same-directory temporary file. Any validator error refuses. Existing
  validator warnings remain advisory and are neither promoted nor echoed.
  Relative paths are therefore checked with the same base directory used by
  downstream registrations.
- More than one correction targeting the same manifest row refuses, even when
  the requested fields are disjoint or identical. Thus sidecar order cannot
  create an implicit precedence rule.

### `note`

- A nonempty, non-whitespace string containing the owner's audit rationale.
- The note remains in the correction sidecar and contributes to the exact
  raw-byte `corrections_sha256` reported on success. It is not implicitly
  appended to manifest `notes`:
  that field has both legacy string and structured uses. An owner who intends
  to rewrite manifest `notes` must use a later, separately specified schema.
- Successful and refused aggregate output never echoes notes or rewritten
  values.

## Manifest parsing and canonical output

- The input manifest is strict UTF-8 without BOM. JSON objects use duplicate-key
  rejection and `allow_nan=False` semantics. Blank lines and lines whose first
  non-whitespace character is `#` are accepted but are not emitted: output is a
  canonical JSONL manifest containing only data rows.
- Treat LF, CRLF, lone CR, mixed CR/LF endings, and a missing terminal newline
  as record separators. Do not use `str.splitlines()`, which also recognizes
  Unicode separators. Unicode line/paragraph separators inside JSON strings are
  data, not record boundaries.
- Preserve manifest data-row order. Serialize every row with
  `json.dumps(row, sort_keys=True, ensure_ascii=True, allow_nan=False,
  separators=(",", ":"))`, encode strict UTF-8, and append exactly one literal
  LF byte. ASCII escaping preserves non-ASCII values semantically while keeping
  U+0085, U+2028, and U+2029 from becoming record boundaries in existing
  `splitlines()` consumers. A nonempty successful output therefore has exactly
  one terminal LF and no platform CRLF translation.
- Sidecar row order and original dictionary insertion order must not affect
  output bytes. Reapplying the same sidecar to its own corrected output must
  reproduce identical bytes.
- Source manifest and sidecar bytes are read fully and handles are closed before
  any destination publication. The source files are never mutated unless the
  operator explicitly selects `--in-place`.

## CLI, publication, and privacy contract

```text
apply_owner_corrections.py MANIFEST CORRECTIONS --out OUT [--replace] [--dry-run]
apply_owner_corrections.py MANIFEST CORRECTIONS --in-place [--dry-run]
```

- Exactly one of `--out OUT` or `--in-place` is required. The manifest parent
  must exist, and `--out` must have the same resolved parent-directory identity
  as `MANIFEST`; moving a manifest would change the meaning of relative corpus
  paths. `--out` refuses an existing path unless `--replace` is also supplied.
  `--replace` is invalid with `--in-place`.
- Resolve path identity before publication. Existing paths use
  `os.path.samefile`; all paths also use resolved absolute paths normalized by
  the host's `os.path.normcase`. `--out` may equal neither input, including a
  Windows case-only or short-name alias. Only `--in-place` may target the
  manifest itself, and no mode may target the corrections file.
- `--dry-run` performs the full parse, match, conflict, rewrite, serialization,
  and post-validation path but publishes nothing.
- Success exits 0. argparse, controlled parse/schema/match/conflict/validation,
  and publication refusals exit 2 without traceback. An unexpected internal
  error retains exit 1.
- Success writes one aggregate-only canonical JSON object plus LF to stdout.
  Real CLI execution must use `sys.stdout.buffer`; a fallback is allowed only
  for an injected/embedded stream without `.buffer` and is outside the
  subprocess byte-exact guarantee. Stable keys are `schema`, `manifest_rows`,
  `corrections`, `applied`, `already_applied`, `input_manifest_sha256`,
  `corrections_sha256`, `output_sha256`, and `dry_run`.
  The schema is `setec-owner-corrections-result/1`; SHA-256 uses the conventional
  `sha256:` prefix. Key order is sorted and JSON uses ASCII escaping so the
  summary is byte-stable and console-safe.
- The three SHA-256 fields hash the exact raw manifest input, exact raw
  corrections input, and canonical corrected output bytes respectively. Notes
  are thereby audit-bound without entering stdout as content. Sidecar order may
  change `corrections_sha256`, but must not change corrected output bytes or
  `output_sha256` when the rule set is otherwise equivalent.
- Controlled failure writes exactly
  `apply_owner_corrections: input, policy, or publication validation failed\n`
  as UTF-8 bytes through `sys.stderr.buffer` in real CLI execution and nothing
  to stdout. The same constrained embedded-stream fallback rule applies. It
  never exposes absolute/private paths,
  row IDs, match predicates, notes, field values, or prose.
- Publication uses a same-directory temporary file, binary write, flush,
  `fsync`, and close. `--replace` and `--in-place` then use `os.replace`.
  Create-new publication atomically hard-links the closed temp to the absent
  destination and unlinks the temp; the link fails rather than overwriting a
  destination created after preflight. Every owned temporary is removed on
  failure. The existing destination remains byte-identical after any failed
  preflight or injected write/flush/fsync/link/replace error.
- Do not require POSIX ownership/mode APIs on Windows. If mode hardening is
  added, guard `fchmod` with `hasattr`, guard POSIX-only `chmod` and assertions
  with `os.name == "posix"`, and avoid raw `os.open`. If raw flags become
  necessary, every optional flag uses `getattr(os, "O_*", 0)` and Windows
  binary mode includes `getattr(os, "O_BINARY", 0)`.

## Acceptance tests

All fixtures are synthetic and code-safe.

1. One exact match rewrites register and era; untouched values and data-row
   order remain stable; source files remain byte-identical.
2. `normalize_author_registry.py` consumes the corrected output and maps the
   corrected register through its existing explicit register map. A synthetic
   non-document `author_corpus_export.py` run consumes corrected register/era,
   and its source snapshot changes when the corrected-manifest SHA changes. An
   old `document_local` attestation refuses corrected bytes. No existing
   registration CLI changes.
3. LF, CRLF, lone CR, mixed endings, and no-final-newline inputs produce the
   same canonical LF output. Quoted Unicode U+0085/U+2028/U+2029 and other
   non-ASCII values round-trip semantically through both registration consumers
   while remaining escaped in the corrected JSONL bytes.
4. Non-ASCII IDs, paths, and notes round-trip where applicable; invalid UTF-8
   and BOM refuse without publication.
5. Zero-match, multi-match, duplicate manifest ID, malformed manifest
   identity types, unknown/empty match, non-string match/expect values,
   type/case mismatch, and stale expectation all refuse. Malformed expectation
   types also refuse when the rewrite is already applied.
6. Unknown/forbidden rewrite fields, wrong register/era types or values, and
   validator-error final rows refuse. A pre-existing warning stays advisory and
   does not appear in public output.
7. Duplicate JSON keys at every object layer, non-object lines, unknown schema
   keys, and `NaN`/`Infinity` refuse.
8. Two rules targeting one row refuse regardless of whether their fields or
   values agree; sidecar permutations otherwise yield byte-identical corrected
   output and `output_sha256` (their exact raw `corrections_sha256` may differ).
9. A second identical application is `already_applied` and output bytes are
   identical; notes are never duplicated.
10. Default create-new, explicit replace, in-place after closed reads,
    dry-run, same-parent enforcement, input-alias rejection, and
    preexisting-target refusal follow the CLI contract. A destination injected
    after preflight remains byte-identical when create-new publication refuses.
11. Injected write, flush, fsync, link, and replace failures leave the
    destination byte-identical and remove the owned temporary.
12. Success stdout is byte-exact UTF-8/LF with one terminal LF; failure is the
    exact sanitized stderr line and no stdout. Neither sink contains private
    paths, IDs, predicates, notes, or rewrite values.
13. A focused `windows-latest` Python 3.12 CI job runs the module and a real CLI
    subprocess, verifying binary LF stdout/output, absent POSIX permission APIs,
    and replacement only after input/output handles close.

## Paper trail and gates

- Add the script, focused tests, schema/reference docs, scripts README usage,
  changelog fragment, roadmap reconciliation, and this spec.
- Run the focused B4 tests, registration integration test, full suite with exact
  pass/skip counts, capability drift, calibration-readiness check, docs
  freshness, compile/diff checks, and leak gate.
- Open one draft PR. Do not merge; Code-PC Claude owns the opposite-vendor and
  native-Windows merge gate.

## Out of scope

- Translating private/ad-hoc sidecar field names without a code-safe schema
  fixture; auto-discovery; implicit precedence; fuzzy/path-resolved matching.
- Mapping coarse staging-era buckets such as `2017-today` onto the canonical
  manifest era enum; that bucket straddles canonical categories.
- Editing source text, content hashes, filesystem paths, identity fields, or
  document-local attestations.
- Corpus prose, model inference, calibration, fiction, or GPU work.
