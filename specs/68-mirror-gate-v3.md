# Spec 68 draft — External-mirror production gate v3

Status: **implementation-ready draft; no calibration claim**
Board item: **B1 / gate-v3**
Intended repository: `setec-voiceprint`
Canonical implementation location: `plugins/setec-voiceprint/scripts/_mirror_gate.py`
Canonical spec landing location: `specs/68-mirror-gate-v3.md`

## 1. Purpose and evidence boundary

The transient packet gate has caught useful production failures, but its v2
heuristics leave three evidence-backed holes:

1. its entity denominator can omit paragraph-initial entities and can treat
   sentence connectives as entities;
2. it cannot mechanically establish exact preservation of annotated,
   block, or inline quoted material, especially unmarked blocks; and
3. it has no quote-exempt measure of long exact-copy runs.

Gate v3 closes those holes without turning the thresholds into a calibration
claim. The values in this contract are operational production policy backed by
the accrued packet evidence, including eleven entity false-positive
confirmations: they are not population estimates, authorship
thresholds, or guarantees of semantic fidelity. All examples and tests are
synthetic. No corpus content enters the repository or test suite.

This is a maintained **internal packet helper**, not a public `setec run`
capability. It therefore lands at the exact path above, with no capability
fragment or public task-surface registration.

## 2. Command-line contract and compatibility

The only supported command shape is:

```text
python _mirror_gate.py SOURCE MIRROR \
  [--register unknown|published|informal] \
  [--quote-spans PATH]
```

`--register` defaults to `unknown`. Options may appear before or after the two
positionals if `argparse` accepts them. Unknown options, missing/extra
positionals, and invalid register values are usage errors.

### 2.1 Stable top-level fields

Every completed evaluation emits one JSON object. These thirteen legacy keys
remain top-level, retain their existing JSON types, and are emitted in this
order so line-oriented packet tooling remains compatible:

1. `source_words` — integer
2. `mirror_words` — integer
3. `ratio` — number, rounded to four decimals
4. `paragraphs_source` — integer
5. `paragraphs_mirror` — integer
6. `similarity` — number, rounded to four decimals
7. `entity_retention` — number, rounded to four decimals
8. `entities_source` — integer
9. `ok_len` — boolean
10. `ok_par` — boolean
11. `ok_sim` — boolean
12. `ok_ent` — boolean
13. `all_pass` — boolean

The object must append exactly one namespaced extension, `gate_v3`, containing
the aggregate-only fields specified below. Consumers must continue to read the
legacy keys by name, not reject the documented extension.

Legacy metric calculations are preserved except for the intentional v3 changes
stated here:

- `ok_sim` is now the upper-copy hard gate only: `similarity <= 0.75`.
  Similarity below `0.15` is advisory, never a hard failure.
- Entity extraction, `entities_source`, and `entity_retention` now use the
  frozen phrase grammar in section 6 rather than v2's mid-sentence titlecase
  token set. `ok_ent` means that **zero hard entity phrases are missing**, while
  register policy determines whether the scalar retention's `0.90` floor
  creates an advisory. This intentionally includes the section 6 ASCII-led
  boundary semantics rather than v2's Unicode `\b` behavior.

Output compatibility is at the parsed JSON field/type/order level, not the
byte-for-byte serialization level: v3 deliberately replaces v2's platform text
printing and spaced separators with compact UTF-8 plus one literal `LF`.

`all_pass` is the conjunction of `ok_len`, `ok_par`, `ok_sim`, `ok_ent`, the
v3 quote-fidelity hard gate, and the v3 exact-copy hard gate. Advisories never
change it.

### 2.2 Exit and stream behavior

| Exit | Meaning | stdout | stderr |
|---:|---|---|---|
| `0` | Evaluation completed, whether `all_pass` is true or false | Exactly one compact JSON object plus one byte `LF` | Empty in normal operation |
| `2` | CLI usage error | Empty | One closed static diagnostic |
| `3` | Input/read/decode error, malformed or stale supplied sidecar, an input ceiling exceeded, or internal failure | Empty | One closed static diagnostic |

A required-but-absent complete sidecar is deliberately **not** an execution
error: it produces a valid aggregate JSON result with quote failure and exits
`0`. An explicitly supplied path that cannot be read is an input error and exits
`3`. Expected errors do not emit tracebacks. No other documented exit exists;
an unexpected exception is caught at the CLI boundary, emits the closed
`internal_error` diagnostic, leaves stdout empty, and exits `3`.

Every exit-`2`/`3` diagnostic is exactly the ASCII bytes
`mirror_gate_error:<code>\n`, written to `sys.stderr.buffer` with a literal
`LF`; stdout is empty. The closed code vocabulary and mapping are:

- exit `2`: `usage_error` for every missing/extra argument, unknown option, or
  invalid option value;
- exit `3`: `input_unreadable`, `input_too_large`, `input_invalid_utf8`, or
  `input_token_limit` for either source or mirror;
- exit `3`: `sidecar_unreadable`, `sidecar_too_large`,
  `sidecar_invalid_utf8`, `sidecar_invalid_json`, `sidecar_invalid_schema`,
  `sidecar_stale`, or `sidecar_span_limit`; and
- exit `3`: `internal_error` for every unexpected exception caught at the CLI
  boundary.

No usage banner, operand, path, exception string, platform error, count, or
other free text is emitted. A supplied sidecar that violates closed keys,
types, ordering, byte boundaries, region reconciliation, or other schema
constraints maps to `sidecar_invalid_schema`.

On every platform, JSON is encoded as UTF-8 and written to
`sys.stdout.buffer` with a literal `b"\n"`. Windows text-mode newline rewriting
must never affect stdout or the binary static diagnostic.

## 3. Raw input, canonical text, and ceilings

Source and mirror are opened in binary mode. Their raw bytes are retained for
SHA binding and quote fidelity. Each is then decoded once with strict UTF-8.
Decode failure is exit `3`; replacement decoding is forbidden.

For the thirteen legacy metrics only, decoded `CRLF` and lone `CR` are
canonicalized to `LF` before word count, paragraph count, entity analysis, and
similarity. Similarity remains
`difflib.SequenceMatcher(None, source_text, mirror_text).ratio()` after that
newline canonicalization. Its existing default `autojunk` behavior is not
changed under the already-established thresholds.

The remaining v2 calculations are frozen here so the repository implementation
does not depend on a transient packet copy. `source_words` and `mirror_words`
are `len(text.split())` using Python's no-argument Unicode-whitespace split.
Paragraphs are the nonempty, `.strip()`-truthy pieces from
`re.split(r"\n\s*\n", text)` using Python's default Unicode regex semantics.
`ratio` is `mirror_words / source_words`, or `0.0` when `source_words == 0`;
`ok_len` is `ratio >= 0.85`. `ok_par` is
`abs(paragraphs_mirror - paragraphs_source) <= max(1, round(0.10 *
paragraphs_source))`, including Python's built-in `round` behavior. Display
rounding for the three legacy scalar fields uses Python `round(value, 4)`; hard
decisions always use the unrounded values or integer comparisons named by this
spec.

Hard input ceilings, checked before expensive analysis, are:

- at most **1 MiB (1,048,576 raw bytes)** per source or mirror;
- at most **20,000 legacy whitespace tokens** per source or mirror; and
- at most **1 MiB** for a supplied quote sidecar; and
- at most **`MAX_QUOTE_SPANS = 4096`** total sidecar region slices.

Each exact boundary is accepted; one byte, token, or slice beyond it is exit `3`
with empty stdout. The v3 quote and exact-run passes must be streaming or bounded:
they may retain the two inputs, token/span metadata, and byte masks, but may not
materialize a source-by-mirror matrix or enumerate all substrings. Exact-run
coverage is linear expected time in token count (rolling hash or equivalent,
with byte/token equality confirmation). Overall runtime is still bounded in
part by the preserved `SequenceMatcher` behavior; the spec makes no stronger
asymptotic claim for that legacy metric.

Ceiling checks must precede similarity. The module must route legacy similarity
through one internal callable seam so direct tests can replace it with a
raise-on-call sentinel and prove every one-over ceiling returns before invoking
similarity. The independent implementation review verifies the exact-run data
structure against the no-matrix/no-substring-enumeration rule; the 20,000-token
synthetic stress test supplies behavioral regression evidence rather than a
machine-dependent wall-clock claim.

## 4. Register policy matrix

All three registers use the same hard gates. Register changes only annotation
requirements and advisories.

| Register | Complete SHA-bound sidecar | Automatic quote discovery | `0.90` entity-retention floor | Lower similarity `<0.15` |
|---|---|---|---|---|
| `published` | Mandatory, including when the complete list is empty | Supplemental only | Advisory | Advisory |
| `informal` | Mandatory, including when the complete list is empty | Supplemental only | No scalar advisory | Advisory |
| `unknown` | Optional | Allowed, but `annotation_complete` must be false without a complete sidecar | No scalar advisory | Advisory |

For `published` or `informal`, no `--quote-spans` option, or a valid sidecar
with `complete:false`, yields a completed JSON evaluation with
`gate_v3.quotes.ok=false`, static reason `complete_annotation_required`, and
therefore `all_pass=false`. A complete sidecar with zero regions is positive
evidence that the annotator found none; it is not equivalent to no sidecar.

For `unknown`, automatic discovery may support the quote-fidelity check, but it
must report `annotation_complete:false` and must never be described as complete
quote enumeration.

## 5. Quote-region sidecar

### 5.1 Schema and validation

The sidecar is strict JSON with this closed shape:

```json
{
  "schema_version": "setec-mirror-quote-regions/1",
  "source_sha256": "64 lowercase hexadecimal characters",
  "complete": true,
  "regions": [
    {
      "spans": [
        {"start_byte": 120, "end_byte": 148},
        {"start_byte": 150, "end_byte": 184}
      ]
    }
  ]
}
```

No additional top-level, region, or span keys are accepted in v1. `complete`
is a JSON boolean. `regions` is a JSON list; each region is a closed object with
one nonempty `spans` list. Each span is a non-empty, half-open
`[start_byte, end_byte)` range in the **raw source UTF-8 byte stream**. Both
endpoints must be integers (booleans are rejected), satisfy
`0 <= start_byte < end_byte <= len(source_bytes)`, and fall on UTF-8 code-point
boundaries.

Within a region, spans must be strictly increasing and non-overlapping and are
the ordered payload slices of that one logical quote. Regions must be ordered
by first span start, and the last span end of one region must be no greater than
the first span start of the next; interleaved or nested region extents are
invalid. Duplicates are malformed. The total span count across all regions is
bounded by `MAX_QUOTE_SPANS`. The SHA-256 is over the source's exact raw bytes;
case, newline, and BOM bytes therefore matter. A bad schema,
unsorted/overlapping/interleaved range, non-boundary endpoint, or SHA mismatch
is a malformed/stale supplied sidecar and exits `3` with empty stdout.

The whole-source SHA binding is sufficient; v1 has and needs no per-region
hashes.

The sidecar itself is input only. The JSON result must not reproduce its SHA,
offsets, or source path.

### 5.2 Region construction and precedence

Three automatic region sources exist, with byte ownership precedence:

```text
Markdown block > colon-indented block > inline
```

- **Markdown block regions** are `>`-prefixed structural blockquotes.
- **Colon-indented block regions** are indented quote blocks after a colon
  introducer.
- **Inline regions** are payloads enclosed by a matched pair of straight or
  curly quotation marks within a paragraph.
- **Annotation regions** are the ordered groups of payload slices in the
  source-only sidecar. They are reconciled with the automatic source regions
  after automatic precedence, as specified below; they do not fragment an
  automatic region slice-by-slice.

Recognition is performed independently on source and mirror raw UTF-8 after
strict decoding while preserving byte coordinates. Annotation spans name their
payload bytes directly; their half-open endpoints are coordinates, not bytes.
Only automatically recognized structural delimiters—`>` plus its one optional
following separator, indent, and matched quotation marks—are excluded from
automatic payloads. Every remaining payload byte is included, including
internal spaces, tabs, and newlines. For a multi-line structural block, the
region is an ordered sequence of contiguous payload slices around excluded
per-line markers; the retained newlines remain payload.

For sections 5.2–5.3, a physical line is a maximal raw-byte sequence ending in
one `CRLF`, `LF`, or lone-`CR` terminator, or the final unterminated sequence;
`CRLF` is one terminator. Marker tests operate on the strictly decoded code
points of the line. When a physical line belongs to a quote region, all of its
terminator bytes belong to that line's payload slice; a final unterminated line
contributes none. A physical line is blank when its decoded content excluding
the terminator is empty after Unicode `.strip()`. A blank or other terminating
nonmember line contributes neither content nor terminator bytes to the block;
the preceding matching line's own terminator remains payload.

The automatic grammar is closed for v3. A Markdown block line begins with zero
to three ASCII spaces followed by `>`; those leading spaces and `>` are
structural and excluded. If the next byte is one ASCII space, that single space
is also excluded; any further spaces or a tab are payload. Consecutive physical
lines that match this rule form one block. A physical blank or nonmatching line
ends it.

A colon-introduced indented block begins only when a nonblank physical line,
after removing trailing ASCII spaces and tabs only, ends in `:`, and the
immediately next physical line (no blank-line skipping) begins either with a
tab or with at least two ASCII spaces. The first payload line establishes one
of two indent styles: exactly one leading tab, or its complete leading run of
`N >= 2` ASCII spaces. Subsequent consecutive physical lines join the block
only when they begin with that same style—at least one tab for tab style, or at
least `N` spaces for space style. One tab or exactly `N` spaces is excluded on
each joined line; additional leading whitespace is payload. Tabs never satisfy
a space indent and spaces never satisfy a tab indent. A blank, under-indented,
or mixed-style physical line ends the block. The colon introducer line creates
no payload slice: its content, colon, trailing horizontal bytes, and terminator
are all outside the block region. Only the joined indented lines produce
payload slices after their established indent prefix is excluded.

For inline recognition, a paragraph is a maximal sequence of physical lines
not separated by two line-break sequences with only Unicode whitespace between
them, recognizing `CRLF`, `LF`, and lone `CR` as line breaks. Inline pairs may
cross a single line break but not that paragraph boundary. Straight `"` pairs
only with straight `"`, `“` only with `”`, and `‘` only with `’`; apostrophes
between word characters are not delimiters. Within each paragraph, scan decoded
code points left-to-right; an opener pairs with the first eligible corresponding
closer, and scanning resumes after that closer. Delimiter bytes are excluded
and every byte strictly between them, including a single physical-line
terminator, is payload. Scanning is non-greedy before precedence resolution. No
other bare-block grammar is implied.

Resolve automatic candidates as whole logical regions, not byte fragments.
Process them by class priority, then earliest structural-extent start, then
longest extent. Accept a candidate only if its structural extent does not
overlap an already accepted higher- or equal-priority extent; otherwise discard
the lower-ranked candidate wholesale. Effective automatic regions therefore
have nonoverlapping structural extents. A nested inline quotation inside a block
and a colon candidate overlapping a Markdown candidate are not counted or
required separately.

Discard any automatic candidate whose concatenated payload byte sequence is
empty before precedence. It creates no effective region, needs no complete-sidecar
confirmation, and cannot create an added mirror region. Thus `""` and a final
unterminated marker-only `>` are mechanically recognized syntax but not quote
regions; a marker-only line with a retained line terminator has that nonempty
terminator payload and is not discarded.

This precedence does not license automatic discovery to override annotation
completeness. In particular, automatic detection cannot establish that all
unmarked bare restatements were found.

Reconcile source annotations only after automatic precedence. If an annotation
region's containing interval—from its first span start through its last span
end—intersects an effective automatic region's structural extent (defined in
section 5.3), its ordered
span list must equal that automatic region's ordered payload-slice list exactly.
It then confirms and represents that one automatic region without changing its
payload or logical-region count. One annotation may confirm only one automatic
region and vice versa. A partial slice, a structural-marker byte, surrounding
byte, intersection with multiple automatic extents, or duplicate confirmation
makes a supplied sidecar malformed and exits `3`. This exact slice grouping is
how a complete annotation represents a multi-line block without including its
per-line markers.

An annotation region whose containing interval intersects no automatic
structural extent is an additional bare annotation region. A bare annotation
region must contain exactly one contiguous span; multiple slices are permitted
only for exact confirmation of an automatic region. The effective source region list is the
source-ordered union of automatic regions (confirmed or not) and additional bare
annotation regions, with every logical region appearing once.

When `complete:true`, every effective automatic source block or inline region
must have exactly one confirming annotation region. An entirely omitted
automatic region produces a completed quote failure with static reason
`complete_annotation_omits_detected_source_quote` and exits `0`; it is distinct
from the malformed partial/ambiguous mappings above. `source_regions` is the
count of logical effective source regions. `preserved_regions` is the count of
those source regions that receive a successful ordered one-to-one equal-payload
mirror match. It is never greater than `source_regions`.

### 5.3 Fidelity algorithm

For each effective source quote region, construct one payload byte sequence by
concatenating its ordered raw byte slices. Construct the corresponding payload
sequence for every **effective automatic mirror region**, meaning the mirror's
automatic candidates after the section 5.2 empty-payload discard and
whole-candidate precedence. A detected region's
structural extent is: for Markdown, from its first permitted leading space (or
`>` when there is none) through its last joined line terminator/end; for a colon
block, from the first indent byte of its first joined line through its last
joined line terminator/end; and for inline, from opener start through closer
end. The colon introducer is outside that extent. A raw occurrence overlaps a
detected region when its byte interval intersects that structural extent,
including excluded marker bytes; such an occurrence is not a raw candidate.

At or after the end of the preceding selected candidate's structural extent
(or raw interval), select the earliest unused candidate with byte-identical
payload: a detected mirror region, or a contiguous raw mirror occurrence that
does not overlap any effective automatic mirror region. A detected-region
match consumes all and only that region's ordered payload slices. Ties resolve
by extent start and then shortest extent. The next candidate's extent start must
be at or after the preceding selected extent end. Excluded marker bytes are not
payload and are never consumed or quote-masked.
Consume the selected region's payload slices, or the selected raw occurrence's
contiguous range, then continue. Matches are therefore ordered, one-to-one,
non-overlapping, and multiplicity-aware even when structural markers make a
block payload non-contiguous in the raw mirror. Every effective region must
match; one missing byte, changed newline, missing duplicate, reordering, or
overlap reuse is a hard quote failure.

The source quote mask used by the exact-copy calculation is the union of every
effective source region's payload slices. The mirror quote mask is the union of:

1. the consumed mirror byte ranges corresponding to source quote payloads; and
2. all effective automatic mirror-region payloads.

After source-region matching, every effective automatic mirror region must
itself have been selected and fully consumed as an
equal-payload detected-region match. Mere overlap with a consumed raw occurrence
is insufficient. Any unconsumed detected mirror region is an added-region hard
failure with static reason
`added_mirror_quote_region`. This catches added marked/structural quote regions
and preserved annotated-style regions. It **does not claim complete detection
of arbitrary added bare quotes**;
the output must carry static scope text or a reason enum that makes that limit
explicit.

Quote failure reasons use this deterministic precedence, first applicable wins:
`complete_annotation_required`,
`complete_annotation_omits_detected_source_quote`, `quote_fidelity_failed`,
`added_mirror_quote_region`, then `ok`. All checks still run so aggregate counts
and the exact-copy mask are deterministic when multiple failures coexist.

## 6. Entity gate and advisory

Entity analysis is ASCII-led and deliberately frozen for v3; the only
non-ASCII grammar character admitted is curly apostrophe U+2019 (`’`). It is a
conservative packet guard, not named-entity recognition.

### 6.1 Token and phrase candidates

Scan the newline-canonicalized source left-to-right with the following closed
ASCII token grammar. Boundaries are exact: neither neighboring character may be
an ASCII letter, digit, underscore, apostrophe (`'` or `’`), or hyphen.
Classification uses the first matching class in this order:

1. **digit-bearing:** starts with uppercase ASCII, otherwise contains ASCII
   letters/digits, and contains at least one digit;
2. **acronym:** two or more uppercase ASCII letters;
3. **internal-cap:** starts with uppercase ASCII, contains ASCII letters, and
   contains another uppercase ASCII letter after the first;
4. **apostrophe/hyphen compound:** starts with uppercase ASCII and contains one
   or more apostrophe- or hyphen-joined nonempty ASCII-letter segments;
5. **titlecase:** `[A-Z][a-z]{2,}`.

In regex terms the candidate union is bounded around:

```text
[A-Z][A-Za-z0-9]*[0-9][A-Za-z0-9]*
| [A-Z]{2,}
| [A-Z][A-Za-z]*[A-Z][A-Za-z]*
| [A-Z][A-Za-z]*(?:['’\-][A-Za-z]+)+
| [A-Z][a-z]{2,}
```

A candidate **phrase** is a maximal run of one or more candidate tokens
separated only by ASCII spaces or tabs. Phrase identity is its exact source
string after newline canonicalization, including internal spaces/tabs and
punctuation inside compound tokens. Entity denominators count unique phrases,
not individual tokens and not occurrences.

At a document-, paragraph-, line-, or sentence-initial position, exclude the
following exact case-sensitive connective tokens from both hard and advisory
sets:

```text
Another  But  However  Now  So  Take  Then  Therefore  Thus  Yet
```

The exclusion is positional, not global: it does not exclude a longer token,
is not substring-based, performs no punctuation stripping, and does not
silently expand from a general stop-word list. Matching is exact,
case-sensitive, and token-bounded. Suppression removes only the leading
connective token from phrase construction; it must not erase or weaken a
following strong token or phrase (`Now GPT4` still yields hard anchor `GPT4`).
A position is initial exactly when the prefix has no non-whitespace
character, contains only whitespace after its most recent `LF`, or, after
right-stripping, ends in `.`, `!`, or `?`. These tokens and this initial-position
test are the frozen connective behavior for v3.

### 6.2 Hard and advisory sets

A **hard entity anchor** is an entire candidate phrase that meets at least one
high-precision rule:

1. the phrase contains an independently strong token classified as acronym,
   internal-cap, or digit-bearing; or
2. the exact whole phrase recurs at a noninitial position; or
3. one of its exact constituent tokens recurs as a bounded candidate token at a
   noninitial position.

A multi-token anchor is atomic for retention: preserving only one token from a
hard two-token phrase is a miss. Ordinary initial-only titlecase phrases,
including titlecase headings, remain advisory rather than hard. This admits
strong paragraph-initial names instead of dropping the position from
consideration while avoiding a hard failure for every ordinary initial
capital. `ok_ent` is true only when every unique hard source phrase appears at
least once as the same exact, case-sensitive, bounded phrase in the mirror. No
case folding, substring match, or punctuation stripping is permitted. With no
hard phrases it is true.

The **advisory set** is every unique candidate phrase left after the positional
connective suppression, including hard phrases and ordinary initial-only
titlecase phrases. `entity_retention` and `entities_source` are computed from
this unique source-phrase denominator. A source advisory phrase is retained if
it appears at least once in the mirror as the same exact, case-sensitive whole
phrase with the section 6.1 boundary rules; the numerator is the count of
unique retained source advisory phrases. An empty denominator yields retention
`1.0`.
Only `published` emits `entity_retention_below_0_90=true` when retention is
strictly below `0.90`. `informal` and `unknown` report that advisory as `null`.
The scalar never changes `ok_ent` or `all_pass`.

## 7. Similarity policy

The rounded display value is not used for decisions. Compare the unrounded
`SequenceMatcher` ratio:

- `similarity > 0.75` is a hard failure (`ok_sim=false`);
- `similarity == 0.75` passes the upper hard gate;
- `similarity < 0.15` sets the lower-floor advisory;
- `similarity == 0.15` does not set it.

The former lower bound becomes advisory because unusually low similarity is a
review-routing signal, not by itself proof of an invalid mirror. This is a
policy correction from field evidence, not a newly calibrated threshold.

## 8. Quote-exempt exact-run coverage

Tokenize decoded source and mirror as maximal runs of characters for which
`str.isspace()` is false, while retaining each token's corresponding raw-byte
interval and raw byte string. A token is quote-masked if any byte overlaps an
effective quote payload. The denominator is the integer count of eligible,
nonquote **mirror** tokens.

An exact run is a source-contiguous and mirror-contiguous sequence of equal raw
token byte strings with length at least **13**. A run may not cross a
quote-masked token or a paragraph boundary in either input. For this gate, a
paragraph boundary exists between tokens when their intervening raw bytes
contain two line-break sequences separated only by Unicode whitespace; a line
break is `LF`, `CRLF`, or lone `CR`. The numerator is the union of mirror token
positions participating in one or more such runs; overlaps are counted once.
Implementations must discover all qualifying positions (for example with a
rolling 13-token index followed by exact extension), not rely on a single
greedy alignment that loses duplicate runs.

The gate compares integer counts, without rounded-float decisions:

```text
covered_mirror_tokens * 100 <= eligible_mirror_tokens * 30
```

Equality at **0.30** passes. If fewer than 13 eligible mirror tokens remain,
coverage is `null`, the hard gate is false, and the static reason is
`insufficient_nonquote_evidence`. Quoted tokens never enlarge either numerator
or denominator, and two shorter exact runs separated by a quote cannot bridge
into a 13-token run.

## 9. Aggregate-only result extension and privacy

`gate_v3` is this exact closed, versioned shape, containing only booleans,
numbers, register vocabulary, and static reason/scope enums:

```json
{
  "schema_version": "setec-mirror-gate/3",
  "register": "unknown",
  "quotes": {
    "annotation_complete": false,
    "source_regions": 0,
    "preserved_regions": 0,
    "ok": true,
    "reason": "ok",
    "automatic_scope": "structural_only_no_arbitrary_bare_completeness"
  },
  "entities": {
    "hard_source": 0,
    "hard_missing": 0,
    "advisory_source": 0,
    "published_retention_below_0_90": null
  },
  "advisories": {
    "similarity_below_0_15": false
  },
  "exact_copy": {
    "eligible_mirror_tokens": 0,
    "covered_mirror_tokens": 0,
    "coverage": null,
    "ok": false,
    "reason": "insufficient_nonquote_evidence"
  },
  "all_hard_pass": false
}
```

`register` takes the CLI vocabulary. Quote `reason` is exactly one of `ok`,
`complete_annotation_required`, `complete_annotation_omits_detected_source_quote`,
`quote_fidelity_failed`, or `added_mirror_quote_region`. Exact-copy `reason`
is exactly one of `ok`, `over_0_30`, or `insufficient_nonquote_evidence`.
`quotes.ok` is true exactly when quote `reason` is `ok`; it is false for every
other quote reason. `quotes.automatic_scope` always equals the sole v3 scope
enum `structural_only_no_arbitrary_bare_completeness`.

`entities.hard_source` is the count of unique hard source phrases,
`entities.hard_missing` is the count of those phrases not retained under the
section 6.2 exact bounded rule, and `entities.advisory_source` equals top-level
`entities_source`, the count of unique advisory source phrases. Top-level
`ok_ent` is identical to `entities.hard_missing == 0`.

`coverage`, when non-null, is rounded to four decimals for display only.
It is null if and only if `eligible_mirror_tokens < 13`; otherwise it equals
`covered_mirror_tokens / eligible_mirror_tokens` before display rounding.
`published_retention_below_0_90` is boolean for `published` and null otherwise.
`annotation_complete` reports verified completeness, not merely the sidecar's
declaration: it is true only when a valid supplied sidecar has `complete:true`
and confirms every effective automatic source region. It is false for an absent
or `complete:false` sidecar and for a `complete:true` sidecar that omits an
automatic region. The latter remains the completed exit-`0` failure described
in section 5.2.
`all_hard_pass` is identical to top-level `all_pass`.

Neither stdout nor expected stderr may emit source/mirror text, quote payloads,
entity strings, identifiers, hashes, filesystem paths, byte offsets, token
positions, sidecar contents, or user-controlled exception text. Diagnostics use
only the closed static reason-code lines in section 2.2. The JSON
schema and a recursive privacy test enforce this prohibition.

## 10. Synthetic acceptance matrix

The implementation is not reviewable as complete until synthetic tests cover
every behavioral row below on both direct-function and subprocess paths where
applicable. The complexity/data-structure prohibition is additionally an
explicit independent code-review check, as named in its row.

| Area | Required synthetic acceptance |
|---|---|
| Legacy surface | Two-position invocation works; all thirteen keys remain in order with their pinned types; valid pass and valid fail both exit `0`; `all_pass` includes every v3 hard gate. |
| Aggregate schema | The sole `gate_v3` extension has the exact closed shape; quote reason and `ok`, verified completeness, sole automatic-scope enum, hard/advisory entity counts, `ok_ent`, exact-copy reason and `ok`, and both hard-pass fields obey their normative mappings. |
| CLI errors | Missing/extra args and bad register exit `2`, stdout empty, and stderr is exactly `mirror_gate_error:usage_error` plus literal `LF`. |
| Input errors | Missing explicit files, invalid UTF-8, stale/malformed sidecar, and ceiling violations map to their exact closed exit-`3` diagnostic bytes with empty stdout; no traceback, operand, path, or user bytes appear. Permission-denied behavior uses a mocked binary opener rather than platform-dependent file modes. |
| Portable byte I/O | CRLF and lone-CR fixtures hash against raw bytes but yield the same legacy metrics as LF; subprocess stdout is UTF-8 JSON ending in exactly one raw `LF`, never `CRLF`; exit diagnostics also end in literal `LF`. Direct inspection/review confirms both streams use `.buffer` and no text newline path. This is synthetic portability evidence, not a Windows production-run claim. |
| Sidecar coordinates | Multibyte UTF-8 endpoints accept only code-point boundaries; exact end-of-file half-open spans pass; empty regions, booleans, zero-length, unsorted, duplicate, overlapping, interleaved, and out-of-range spans fail validation; grouped multi-slice regions preserve one automatic region's logical identity, while a nonintersecting bare multi-slice region is malformed; 4,096 total slices pass and 4,097 fail before evaluation. |
| Register matrix | Published/informal with absent or incomplete annotations return JSON quote failure; complete empty annotations are distinct and can pass; unknown auto-only reports `annotation_complete=false`; a declared-complete sidecar that omits an automatic region also reports verified `annotation_complete=false`. |
| Precedence | Markdown block beats an overlapping colon block, colon block beats inline, and every lower-ranked overlapping candidate is discarded wholesale; `Intro:\n  > X\n` resolves only to Markdown payload `X\n`; an annotation that intersects an automatic structural extent must exactly equal that region's ordered payload slices, while a nonintersecting annotation is one contiguous bare region; partial/marker/multi-region intersections are malformed; an identical multi-line source/mirror block with one confirming multi-slice annotation passes as one logical region; a complete sidecar omitting an automatic source quote returns a completed static-reason quote failure. |
| Block grammar | Markdown fixtures pin 0–3 excluded leading spaces, the one optional separator, extra-space/tab payload, physical `LF`/`CRLF`/lone-`CR` terminator ownership, and blank termination; colon fixtures pin physical-next-line triggering, introducer exclusion, ASCII-only right-strip, tab versus `N`-space indent establishment, extra-indent payload, and blank/under-indent/mixed-style termination. |
| Empty automatic payload | Empty inline quotes and final unterminated marker-only blocks in either source or mirror produce no effective region, need no confirmation, and cannot trigger an added-region failure; a marker-only block line with a retained terminator has a nonempty terminator payload and remains a region. |
| Ordered fidelity | Repeated identical quotes require repeated ordered mirror occurrences; deletion, byte change, newline change, reordered distinct quotes, or reuse of one occurrence fails; inline fixtures pin same-style delimiters, single-line-break pairing, and paragraph-boundary refusal. |
| Auto scope | An added effective marked/structural mirror quote not consumed by source matching hard-fails; nested mirror inline syntax discarded under a Markdown region is not separately required; source payload `alpha` versus marked mirror payload `alpha beta` proves raw substring overlap cannot consume or exempt the larger region; marker-byte and structural-extent fixtures prove raw matches cannot reuse excluded syntax; a fixture with an arbitrary added bare restatement proves the result still does not claim complete detection. |
| Combined quote failures | One fixture triggers completeness, fidelity, and added-region failures together and proves the frozen reason precedence while preserving deterministic region counts and masks. |
| Entity hard set | A phrase containing acronym/internal-cap/digit and a phrase/token recurring noninitially become hard; partial preservation of a hard two-token phrase fails; exact phrase retention passes; unique phrase—not token/occurrence—denominators are asserted. |
| Entity connectives | Each frozen initial connective alone is excluded, `Now GPT4` retains hard anchor `GPT4`, the same connective spelling mid-sentence is not positionally excluded, and case variants, punctuation, longer tokens, and substrings are untouched. |
| Entity advisory | Ordinary initial-only titlecase runs and titlecase headings remain advisory; published retention `0.90` does not advise and the next exact rational below does; informal/unknown scalar advisory is `null`; none changes hard pass. |
| Similarity | Exact unrounded cases below/equal/above `0.15` and `0.75` prove lower advisory and upper-only hard behavior; default `SequenceMatcher` autojunk remains pinned. |
| Exact-run minimum | A 12-token run does not count; a 13-token run does; duplicate and overlapping runs union mirror positions once; a quote mask and a paragraph boundary each prevent bridging. |
| Exact-run denominator | Denominator contains only nonquote mirror tokens; quote-only overlap is exempt; exact integer fixtures at `0.30` pass and the next count above fails. |
| Insufficient evidence | 0 and 12 eligible mirror tokens produce `coverage:null`, hard false, and `insufficient_nonquote_evidence`. |
| Ceilings/performance | Exact 1 MiB/20,000-token/4,096-slice limits are accepted; each one-over case uses a raise-on-call similarity seam to prove rejection precedes similarity; a 20,000-token exact-run stress fixture verifies results, while independent code review confirms bounded metadata and no quadratic matrix/substrings. |
| Privacy | Generated high-entropy sentinels, chosen not to overlap any schema key/enum/diagnostic substring, stand in separately for source text, mirror text, quote payload, entity value, ID, SHA, path, offset, and position; recursive inspection proves each full sentinel and its encoded JSON form are absent on pass, gate fail, and expected error, while implementation review confirms no user-derived values enter output formatting. |

The test suite must use only generated literals and temporary files. No packet,
manifest, private path, or corpus-derived fixture may be copied into the repo.

## 11. Repository deliverables

One PR implements this contract with:

1. `plugins/setec-voiceprint/scripts/_mirror_gate.py` as the maintained helper;
2. focused synthetic tests under
   `plugins/setec-voiceprint/scripts/tests/test_mirror_gate.py`;
3. this contract at `specs/68-mirror-gate-v3.md`;
4. a concise internal-operator reference documenting the CLI, sidecar schema,
   exit codes, register matrix, Windows invocation, and privacy limits; and
5. a `changelog.d/` fragment describing the internal gate hardening without a
   calibration or public-capability claim.

The implementation must run the focused tests, the repository's relevant docs
and leak gates, and the full ordinary suite before the PR is marked finished.
The PR body records exact pass/skip counts and explicitly says that tests are
synthetic and that no corpus, GPU, or calibration run occurred.

## 12. Out of scope

- Editing, replacing, or executing the live batch-0145/0146 packet copies.
- Adopting v3 in any packet driver or changing production/retry/fold policy;
  packet adoption is a later, separately reviewed handoff step.
- Reading, copying, committing, or exposing corpus material.
- Semantic-fidelity judgment, authorship inference, model scoring, NER, or a
  claim that arbitrary unmarked quotes can be discovered automatically.
- Recalibrating any threshold or interpreting packet confirmations as a
  population sample.
- Adding a public SETEC capability, task surface, golden capability fixture, or
  consumer-contract change.
- GPU/model runs or Windows production claims beyond synthetic portability
  tests.

## 13. Review and release gate

The spec receives independent Sol review before implementation. The completed
diff receives a separate independent Sol implementation review. Findings are
resolved and all exact tests rerun before the draft PR is ledgered FINISHED.
The PR remains unmerged for Code-PC's opposite-vendor review; merge commit only,
never squash.
