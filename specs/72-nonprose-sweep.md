# 72 - Deterministic non-prose sweep

> Productionize the repeated transcript/non-prose staging screen as a bounded,
> default-preserving validation capability with fixed operational thresholds and
> an exact authored-residual/transcript word partition.

- **Status:** Implementation review GO; draft PR #341; native-Windows CI pending;
  merge held for Code-PC Claude (`codex/nonprose-sweep`)
- **Tier:** core / stdlib / CPU
- **GPU required:** no
- **Source contract:** fleet refill packet B2 and update-14 Windows portability guidance
- **Surface:** existing `validation`
- **Calibration posture:** operational, uncalibrated screen; no disposition or authorship claim

## 1. Motivation and compatibility boundary

Four private workers have independently rebuilt the same staging checks for VTT
cues, speaker labels, disfluencies, and unusually short-line layouts. The exact
private implementations and corpus records are not a public contract. This spec
defines the first code-safe public method, `setec-nonprose-method/1`; it does not
claim byte or decision compatibility with an unpublished predecessor.

The capability reports structural transcript/non-prose indicators for operator
review. It does not remove, rewrite, register, accept, reject, or disposition a
document. `authored_residual_words` is the residual side of a deterministic
structural partition, not an inference about who wrote any words. A screen hit is
an operational queue condition, not an authorship, provenance, quality, genre,
fiction/nonfiction, AI/human, or training-eligibility verdict.

V1's applicability boundary is the upstream-selected B2 nonfiction inventory,
recorded as `all_nonfiction` in the capability registry. The tool does not decide
that a document is nonfiction and must not be used to classify fiction versus
nonfiction; fiction and mixed-register discovery are outside this command's input
scope. Registration states an operator-selected domain, not a conclusion licensed
by the structural measurements.

The existing `check_corpus.py` remains unchanged. That tool measures broad
markup/code stripping impact and has existing defaults and consumers. B2 is a
separate sibling capability named `nonprose_sweep` on the already registered
`validation` surface. There is no implicit validation-harness, registration, or
shard-runner integration in v1.

## 2. CLI and input contract

```text
nonprose_sweep.py --manifest MANIFEST --report-out REPORT
```

- Both paths are required. There is no directory discovery, inline-text mode,
  threshold override, overwrite flag, network input, or default output path.
- `MANIFEST` itself must remain a direct, single-linked regular file beneath a
  pinned direct parent; symlink/reparse, directory/device, replacement during read,
  or multiply-linked control identity refuses.
- `MANIFEST` is a B2 descriptor JSONL that accepts a standard SETEC manifest's
  superset of fields but intentionally narrows path semantics. B2 performs its own
  bounded parse of the exact bytes and consumes only `id` and `path`. It requires
  every data row to carry a unique nonempty string value for both. This projection
  check is not a substitute for the full `manifest_validator`; B2 must not reuse
  that validator's unbounded, path-bearing CLI result shape.
- IDs are opaque controls, not filenames. An ID is at most 256 strict UTF-8 bytes,
  contains no C0/C1 control, surrogate, U+2028, or U+2029 character, and is emitted
  only inside the explicit private report. Paths and prose are never emitted.
- Blank lines and lines whose first non-space/tab character is `#` remain
  permitted by the standard manifest. Data rows must be JSON objects. Parsing
  rejects a UTF-8 BOM, invalid UTF-8, duplicate JSON keys at any nesting depth,
  `NaN`, `Infinity`, booleans where integers are required, and malformed rows.
- Physical JSONL record separators are exactly LF, CRLF, or lone CR. A missing
  final newline is accepted. U+0085, U+2028, and U+2029 inside JSON strings are
  data, never record separators.
- Document paths use `/` as their only separator. They must be relative and contain
  no literal backslash, NUL, empty/`.`/`..` component, drive, or alternate-data-stream
  colon, and resolve only by component-relative lookup beneath the pinned manifest
  parent. Absolute paths, parent traversal, and the
  broader `manifest_validator.resolve_path` parent-parent/CWD fallbacks are not B2
  inputs. A source must remain a direct regular file, not a symlink,
  junction/reparse point, directory, device, or source/output alias. All input
  handles close before report publication. No source may alias the manifest, and no
  two descriptor rows may resolve to the same stable file identity, including two
  names/hardlinks for one inode/file ID. A single selected read-only source may have
  multiple links.
- Documents decode as strict UTF-8 without BOM or NUL. Their physical line separators are
  exactly LF, CRLF, or lone CR; a missing terminal newline is equivalent. Unicode
  line/paragraph separators remain content within one physical line.
- The report parent must already exist and be a direct directory. `REPORT` must be
  absent and must not alias the manifest or any source file. Publication is
  create-new only; an intervening winner is preserved.

## 3. Frozen text and line grammar

All method constants below are versioned together as
`setec-nonprose-method/1`. Implementations use exact integer arithmetic for every
threshold; displayed ratios never control a screen.

The refill packet supplies only these validated operating points: VTT any-hit,
speaker labels strictly above 15% of nonempty lines, disfluencies strictly above 6
per 1,000 words, and short lines strictly above 55% when there are more than 15
nonempty lines. The tokenizer, cue/speaker grammar, closed disfluency lexicon,
1-5-word short-line definition, transcript partition, and resource ceilings are
new code-safe operational definitions introduced by Spec 72; they are not described
as corpus-validated owner parameters.

### 3.1 Physical and analyzable lines

- A physical line is one record produced by the separator grammar in section 2.
- A nonempty line contains a character other than ASCII space or tab after its
  separator is removed. `nonempty_lines` is the denominator for the speaker and
  short-line screens and includes structural VTT lines.
- Structural prefixes and VTT markup identified below contribute zero analyzable
  words. The remaining text of each physical line is its analyzable content.
- An analyzable word matches
  `re.compile(r"[^\W_]+(?:['’\-‐‑][^\W_]+)*", re.UNICODE)` exactly. Thus a
  word is one or more Unicode alphanumeric characters, optionally joined by an
  internal apostrophe or listed hyphen; underscore and a bare joiner are not words.
  The explicit executable form, rather than the explanatory notation, is canonical.
- Matching is deterministic and locale-independent. No Unicode normalization,
  stemming, language detection, or model is applied.

### 3.2 VTT structures

A line has a VTT structural hit when its ASCII-space/tab-trimmed content is either:

1. exactly case-sensitive `WEBVTT` when it is the first nonempty physical line; or
2. a full cue timing line matching the following ASCII grammar:

```text
TIMESTAMP [SP/HTAB]+ --> [SP/HTAB]+ TIMESTAMP
  ([SP/HTAB]+ SETTING)*

TIMESTAMP := ([0-9]{2,}:)?[0-5][0-9]:[0-5][0-9].[0-9]{3}
SETTING   := (vertical|line|position|size|align|region):[!-~]+
```

The dot before milliseconds is a literal dot. `[!-~]+` means one or more printable
ASCII characters with no space/tab; Unicode whitespace and non-ASCII setting bytes
do not match. No comma-millisecond SRT syntax,
bare arrow, partial-line search, lowercase header, invalid minute/second, unknown
setting, or prose containing `-->` matches. A header or timing line makes
`vtt_any_hit` true; the packet's VTT threshold is therefore exact any-hit.

A timing line, exact header, and a cue identifier immediately before a timing line
after a blank/header boundary are structural and contribute zero analyzable words.
After a timing line, subsequent nonblank non-timing lines form VTT cue payload until
the next blank, timing line, or exact header. Within cue payload only, substrings
matching `<[^>\r\n]{1,128}>` are structural markup and removed before tokenization.
Malformed/unclosed angle text remains analyzable content. Cue payload words are
transcript words.

### 3.3 Speaker labels and blocks

A speaker label occupies the start of a physical line and ends at the first ASCII
colon. The colon must be followed by end-of-line, ASCII space, or tab. The
ASCII-space/tab-trimmed label is at most 48 strict UTF-8 bytes and is one of:

- a case-insensitive explicit role: `SPEAKER` optionally followed by one ASCII
  space and 1-3 digits; `PARTICIPANT` under the same rule; `INTERVIEWER`,
  `INTERVIEWEE`, `HOST`, `GUEST`, `MODERATOR`, `AUDIENCE`, `AUDIENCE MEMBER`,
  `UNKNOWN`, `Q`, or `A`; or
- two through four name tokens separated by exactly one ASCII space. A name token is
  split on the joiner set `{ASCII apostrophe, U+2019, ASCII hyphen, U+2010, U+2011}`;
  it must have no empty segment, every remaining character must satisfy
  `str.isalpha()`, and it must contain at least two letters. At least one letter must
  be cased (`lower() != upper()`), and every cased letter must equal its `upper()`.
  Uncased-script labels therefore do not match, and leading, trailing, or consecutive
  joiners refuse the token.

The multi-token requirement deliberately excludes one-token acronym/headline forms
such as `NASA:` and title-case prose such as `Note:`. The label and colon are
structural and contribute zero analyzable words. Words after the colon are
transcript words. Subsequent nonblank, non-VTT, non-speaker lines remain in the same
speaker block and are transcript words until a blank or another structural boundary.
A line already classified as VTT payload is transcript exactly once.

`speaker_label_lines` counts physical lines matching this grammar, including a label
inside VTT payload. The speaker-label screen hits exactly when:

```text
speaker_label_lines * 100 > nonempty_lines * 15
```

Zero nonempty lines cannot hit. Equality at 15% is clear.

### 3.4 Disfluencies

Each analyzable word is casefolded without normalization. A disfluency is an exact
whole-token member of:

```text
um  umm  uh  uhh  erm  er  hmm  mm-hmm  uh-huh
```

Substrings (`um` in `quantum`) and contextual phrases such as `like`, `you know`,
or `I mean` do not count. `disfluency_count` includes authored-residual and
transcript words. The screen hits exactly when:

```text
disfluency_count * 1000 > total_analyzable_words * 6
```

Zero analyzable words cannot hit. Equality at 6 per 1,000 is clear.

### 3.5 Short lines

A short line is a nonempty physical line with 1-5 analyzable words after structural
prefix/markup removal. A structural line with zero words is not short. The screen is
eligible only when `nonempty_lines > 15` and hits exactly when:

```text
short_lines * 100 > nonempty_lines * 55
```

Exactly 15 lines is ineligible; equality at 55% is clear. The 1-5 word cutoff is a
new public v1 definition selected conservatively because the packet freezes the
percentage/line-count thresholds but no code-safe private short-line token cutoff.

## 4. Authored-residual/transcript partition

Every analyzable word belongs to exactly one bucket:

- `transcript_words`: words in a VTT cue payload, after a recognized speaker-label
  colon, or on an unlabeled continuation line inside a speaker block;
- `authored_residual_words`: every other analyzable word.

Structural headers, timing lines, cue identifiers, VTT tags, and speaker labels are
not analyzable words and belong to neither bucket. The per-document and aggregate
conservation equation is mandatory:

```text
total_analyzable_words = authored_residual_words + transcript_words
```

Disfluency or short-line evidence alone does not invent transcript attribution.
When `total_analyzable_words` is zero, both fraction denominators are zero and both
numerators are zero. Otherwise the exact authored and transcript fractions are
reported as integer `{numerator, denominator}` objects sharing the total denominator.
No floating fraction is stored in the canonical report.

## 5. Canonical private report

`--report-out` publishes canonical ASCII-escaped UTF-8 JSON plus exactly one LF. It
uses `json.dumps(..., sort_keys=True, ensure_ascii=True, allow_nan=False,
separators=(",", ":"))`. The top-level closed schema is:

```json
{
  "schema": "setec-nonprose-sweep-report/1",
  "method": "setec-nonprose-method/1",
  "calibration_status": "operational_uncalibrated",
  "manifest_sha256": "sha256:...",
  "source_set_sha256": "sha256:...",
  "thresholds": {
    "disfluencies_per_1000_strictly_greater_than": 6,
    "short_line_max_words": 5,
    "short_line_min_nonempty_lines_exclusive": 15,
    "short_line_percent_strictly_greater_than": 55,
    "speaker_label_percent_strictly_greater_than": 15,
    "vtt_any_hit": true
  },
  "totals": {},
  "documents": []
}
```

`documents` is sorted by the UTF-8 bytes of opaque `id`, independent of manifest
row order. Each document object has exactly:

```json
{
  "id": "opaque-id",
  "nonempty_lines": 0,
  "total_analyzable_words": 0,
  "authored_residual_words": 0,
  "transcript_words": 0,
  "authored_residual_fraction": {"numerator": 0, "denominator": 0},
  "transcript_fraction": {"numerator": 0, "denominator": 0},
  "vtt_structural_hits": 0,
  "speaker_label_lines": 0,
  "disfluency_count": 0,
  "short_lines": 0,
  "screen_hits": {
    "vtt_any": false,
    "speaker_labels": false,
    "disfluencies": false,
    "short_lines": false
  }
}
```

`source_set_sha256` seals the analyzed source snapshot without publishing individual
content hashes. Its preimage is canonical JSONL sorted by UTF-8 ID bytes. Each row
has exactly `{"content_sha256":"LOWERCASE_64_HEX","id":"OPAQUE_ID"}` serialized
with `sort_keys=True`, `ensure_ascii=True`, `allow_nan=False`, compact separators,
strict UTF-8, and one literal LF; the final row also has one LF. The report field is
`"sha256:"` plus the lowercase SHA-256 hex of that complete byte sequence. Component
hashes are retained only in memory. Thus a report is bound to both the manifest bytes
and every analyzed document byte sequence.

`manifest_sha256` hashes the exact raw manifest bytes. It intentionally changes when
only manifest framing, row order, or JSON key order changes; semantic analysis and
`source_set_sha256` remain invariant for an equivalent projected inventory. Like all
three public seals, it is spelled `sha256:` plus 64 lowercase hexadecimal digits.

`totals` contains exactly `documents`, `documents_with_any_screen`,
`nonempty_lines`, `total_analyzable_words`, `authored_residual_words`,
`transcript_words`, `vtt_structural_hits`, `speaker_label_lines`,
`disfluency_count`, `short_lines`, and `screen_counts` with the four exact screen
names. It must satisfy the aggregate word-conservation equation. The report
contains no path, prose, raw line, raw token, speaker name/role, VTT payload,
per-document content digest, inferred register, disposition, verdict, label, quality, authorship,
provenance, or AI/human key. The report is private because opaque IDs may still be
operator-sensitive; no corpus fixture enters the repository.

## 6. Aggregate stdout and errors

Success exits 0 and writes one canonical schema-version 1.0 SETEC envelope through
`sys.stdout.buffer`, followed by exactly one LF and an explicit flush. It uses:

- `task_surface="validation"`, `tool="nonprose_sweep"`, and `version="1.0"`;
- `target.path=null`, `target.words=total_analyzable_words`, and `baseline=null`;
- a `ClaimLicense` that licenses only a structural screen and explicitly refuses
  disposition/authorship/provenance/quality/AI-human conclusions;
- aggregate-only `results` containing the method, fixed thresholds, exact totals,
  `calibration_status="operational_uncalibrated"`, manifest SHA-256, source-set
  SHA-256, and report SHA-256.

The envelope is serialized once with `json.dumps(..., sort_keys=True,
ensure_ascii=True, allow_nan=False, separators=(",", ":"))`, encoded as strict
UTF-8, and followed by exactly one literal LF. The report SHA-256 hashes the exact
canonical report bytes including their terminal LF and uses the same `sha256:` plus
lowercase-hex spelling.

Stdout contains no document IDs or paths. The only fallback from `.buffer` is an
explicitly injected in-memory text stream in unit tests; real subprocess behavior is
binary and byte-exact on Windows and POSIX.

The bounded v1 emits no progress records, so stderr is terminal-only. A closed
argument parser emits exactly `nonprose_sweep: invalid arguments\n` and exits 2 for
every usage error, without echoing operands or default argparse help. Controlled
failure exits 3 and writes exactly
`nonprose_sweep: input, resource, or publication validation failed\n` as UTF-8 bytes
to stderr, and writes nothing to stdout. No controlled failure emits a traceback,
private path, ID, source value, raw bytes, or platform exception text. An unexpected
invariant/internal failure exits 1 with exactly
`nonprose_sweep: internal failure\n`, no stdout, and no traceback or operand detail.

## 7. Resource and complexity ceilings

The v1 algorithm is single-pass and linear in input bytes, physical lines, and
tokens. It processes one document at a time and retains only its metrics plus the
bounded document-summary list. These hard ceilings apply before allocation/use:

- manifest bytes: 8 MiB;
- manifest data rows/documents: 10,000;
- one document: 8 MiB;
- cumulative document bytes: 256 MiB;
- physical lines in one document: 200,000;
- one physical line: 1 MiB UTF-8 bytes;
- analyzable words in one document: 2,000,000;
- canonical report bytes: 16 MiB.

Crossing a ceiling refuses the whole operation without partial final output; there
is no truncation, sampling, silent skip, or partial success. The 256 MiB/10,000-file
cap plus linear streaming is the explicit v1 short-run boundary. Larger corpora must
be split into operator-controlled manifests and reconciled outside this capability;
B2 does not claim shard-runner/checkpoint parity.

## 8. Byte-exact and platform-safe I/O

- All input and output files use binary mode. Strict UTF-8 decoding occurs only
  after bounded reads. No text-mode newline translation is used for artifacts or
  console streams.
- Raw descriptor flags, if used, add `getattr(os, "O_BINARY", 0)` and obtain every
  optional flag such as `O_CLOEXEC` or `O_NOFOLLOW` with `getattr`. Absence of a
  POSIX-only flag must not make Windows unusable.
- On POSIX, nonzero `O_NOFOLLOW` and `O_DIRECTORY`, descriptor-relative open/stat,
  and a native atomic no-replace sibling-rename symbol are required for this
  capability; symbol absence refuses before any source read or output write. The rename is
  `renameatx_np(RENAME_EXCL)` on macOS and `renameat2(RENAME_NOREPLACE)` on Linux.
  A filesystem that rejects the real native operation returns a controlled refusal
  at publication and preserves the possibly mutation-ambiguous names.
  The guarded-zero fallback applies only to the separate Windows handle backend,
  never as a silent POSIX safety downgrade.
- `chmod`, `fchmod`, UID, and POSIX-mode assertions are unnecessary. If introduced,
  guard APIs with `hasattr` and assertions with `os.name == "posix"`; do not emulate
  a Windows DACL.
- Extend `windows_descriptor_io` only through backward-compatible keyword controls:
  `allow_multiple_links=False` on `require_direct`, `_nt_open`, and `open_file`, and
  `share_write=True` / `share_delete=True` on `create_file`. Existing callers retain
  the current restrictive hardlink policy and sharing defaults. B2's manifest/control
  open retains `allow_multiple_links=False`; only B2 document-source opens opt into
  `allow_multiple_links=True` with write/delete sharing denied. B2 creates its payload
  temporary with both sharing flags false. Helper-focused regressions pin old defaults
  and both B2 open policies.
- Preflight records source identity and size from the open handle, reads exactly the
  bounded declared bytes plus EOF, and rechecks stable identity/size before close.
  POSIX pins the source parent and uses descriptor-relative lookup; Windows pins the
  parent and opens with write/delete sharing denied. Direct-file/reparse checks use
  existing platform helpers. Stable multiply-linked read-only sources are accepted;
  link count is not contamination. A source mutation, indirection, alias, sharing
  conflict, or short/extra read refuses.
- Report publication writes an owned same-directory binary temporary through a
  retained parent handle, flushes, `fsync`s/flushes, seeks, and exact-byte verifies
  it while its identity remains pinned. POSIX then atomically consumes the temporary
  with a native descriptor-relative no-replace rename and fsyncs the directory.
  Windows performs the existing handle-relative create-new rename with replacement
  disabled while the verified handle remains live. There is no
  overwrite/copy/path-based fallback. A POSIX failure before the atomic rename
  deliberately preserves the unambiguous UUID temporary: POSIX has no portable
  primitive that can condition an unlink atomically on inode identity, so a
  stat-then-unlink cleanup is forbidden. A race winner stays byte-identical and is
  never deleted. Final-name identity and exact report SHA-256 are verified before
  success is emitted.
- The UUID temporary namespace is process-owned. Same-principal replacement of that
  unpredictable name immediately before the native rename is outside the guarantee
  available from path-based POSIX APIs: the replacement can become the final name,
  but retained-writer versus fresh-final identity verification detects it and returns
  a controlled refusal without deleting, truncating, or renaming either name. Once a
  native rename has been attempted, even an error is treated as mutation-ambiguous;
  neither name is cleaned. Unsupported-symbol/filesystem errors never downgrade to
  `os.rename`, `os.replace`, link/unlink, overwrite, or copy publication.
- Pre-rename POSIX failure residues may contain the private canonical report and can
  accumulate. They remain UUID-named in the already-private report directory for
  operator inspection/cleanup; this capability never deletes them by pathname.
- Every opened file/descriptor/Windows handle is closed on success, controlled
  failure, and `OSError` or `MemoryError` injection. Cleanup failure cannot authorize
  deletion of an unverified path and must not mask the primary controlled refusal.

## 9. Registration and documentation

Ship in the same draft PR:

- `plugins/setec-voiceprint/scripts/nonprose_sweep.py`;
- the default-preserving `windows_descriptor_io` keyword extension and focused
  helper regressions required by section 8;
- focused synthetic POSIX and native-Windows tests;
- `capabilities.d/nonprose_sweep.yaml` and the matching per-ID golden fragment;
- a concise method/report reference document;
- scripts README usage and validation-surface table entry;
- specs README entry, roadmap reconciliation, implementation survey/glossary note
  where required, changelog fragment, and regenerated calibration-readiness matrix;
- a focused `windows-nonprose-sweep` GitHub Actions job.

The capability manifest uses `status: heuristic`, `surface: validation`,
`handoff: none`, `consumers: []`, stdlib/core compute, and describes the four
operational screens without implying calibration or a disposition. No new task
surface or claim-license fragment is added. The script carries the exact unannotated
module-level assignment `TASK_SURFACE = "validation"` required by the capability
drift AST gate.

## 10. Executable acceptance criteria

All fixtures are synthetic and code-safe.

1. Exact VTT header and valid cue timing each hit; lowercase/partial header, comma
   timestamp, invalid seconds, bare/embedded arrow, unknown setting, and arrow prose
   do not. Cue identifiers/timing/tags contribute zero words; payload partitions to
   transcript.
2. Explicit roles, numbered SPEAKER/PARTICIPANT, Q/A, and 2-4 uppercase name tokens
   match. One-token acronym, title-case heading, overlong label, missing colon
   boundary, uncased-script label, leading/trailing/consecutive joiner, and invalid
   token do not. Speaker continuation stops at every frozen boundary and no word is
   double-counted inside VTT.
3. Every disfluency lexeme matches case-insensitively as one whole token; substrings
   and excluded phrases do not. Unicode/apostrophe/hyphen word fixtures pin the
   executable tokenizer.
4. Speaker equality at 15% is clear and one exact cross-multiplication unit above
   hits. Disfluency equality at 6/1,000 is clear and one unit above hits. Short-line
   equality at 55% is clear and one unit above hits; 15 nonempty lines is ineligible
   and 16 is eligible. Rounded displays cannot change outcomes.
5. Authored-only, VTT-only, speaker-only, mixed, zero-word, and blank-boundary
   fixtures prove both per-document and aggregate conservation and exact fraction
   numerators/denominators. Disfluency/short-line-only evidence leaves words in the
   authored-residual bucket.
6. Manifest row order, JSON key order, and manifest LF/CRLF/lone-CR/missing-final-LF
   variants over unchanged source bytes yield identical document metrics, totals,
   screen decisions, and source-set seal; their exact raw manifest/report seals
   differ. Document newline variants yield identical metrics, totals, and decisions,
   but their exact source-set/report/stdout seals differ. U+0085/U+2028/U+2029 remain
   data. Document rows sort by UTF-8 ID bytes.
7. BOM, invalid UTF-8, duplicate keys/IDs, nonfinite JSON values, malformed manifest
   projection, NUL, invalid ID, unsafe/absolute/escaping path, symlink/reparse, nonregular
   source, hard resource limit, changing manifest/source, source-manifest alias,
   duplicate descriptor source identity, and every source/output alias refuse with
   exit 3 and no report. A multiply-linked manifest refuses while one stable
   multiply-linked document source remains accepted.
8. Every resource ceiling has an exact-at-limit success and one-over refusal test,
   using injected small limits where necessary. Common long-line/token amplification
   remains bounded and linear.
9. Repeated runs over the same exact manifest and source bytes produce byte-identical
   canonical report/stdout bytes. A changed exact manifest necessarily changes its
   manifest pin and report/stdout seal. Report and stdout have one terminal LF and
   no CR on native Windows and POSIX.
   A one-byte source mutation changes `source_set_sha256` and the report seal even
   when the manifest is byte-identical; individual content hashes are never emitted.
10. Report publication tests inject write, flush, fsync, atomic create-new rename,
    verification, close, and `MemoryError` failures. POSIX pre-rename failures leave
    the exact owned UUID temporary rather than risk a check/unlink race; Windows
    exercises handle-bound cleanup. An intervening destination remains byte-identical;
    no unverified winner is deleted; all handles are attempted closed.
11. Recursive leak tests prove report/stdout/error contain none of the synthetic
    prose, paths, raw lines/tokens, speaker labels, or VTT payload. Stdout contains
    no IDs. A recursive posture walk over the private report and envelope `results`
    rejects the bare inference keys `disposition`, `verdict`, `label`, `selection`,
    `authorship`, `provenance`, `quality`, `is_ai`, and `is_human`. Measured-property
    names beginning `speaker_label_` are explicitly allowed. The standard envelope's
    mandatory `ai_status` key remains present and exactly null; it is not duplicated
    or populated inside `results`.
12. The standard output envelope, `ClaimLicense`, surface, version, fixed thresholds,
    report seal, capability fragment/golden, docs, calibration matrix, and changelog
    are pinned, including the literal module-level `TASK_SURFACE = "validation"`.
    Existing `check_corpus`, validation-harness, manifest-validator, and registration
    defaults remain byte/behavior unchanged.
13. Native Windows CI runs the real CLI on Unicode/space/`#` paths and verifies strict
    UTF-8, LF/CRLF/lone-CR equivalence, binary one-LF stdout/report, create-new race
    preservation, closed handles before rename/delete, and absence of unguarded
    POSIX permissions/required `O_*` flags.
14. Focused and adjacent tests, capability drift/golden, docs freshness, generated
    readiness, compilation, `git diff --check`, leak gates, and the full repository
    suite pass with exact counts recorded in the draft PR and fleet ledger.

## 11. Out of scope and merge gate

Out of scope: private corpus execution or fixtures; fiction classification; source
rewriting; acceptance/rejection/registration action; automatic corpus disposition;
speaker identity; language/model inference; calibrated rates; authorship,
provenance, quality, AI/human, or training-use claims; network/API/model/GPU work;
SRT/TTML/ASS subtitle grammars; fuzzy speaker recognition; shard-runner/checkpoint
integration; changing existing validation defaults.

One draft PR contains the spec, implementation, tests, and docs. Independent Sol
specification and implementation reviews must be GO after all findings are folded.
Every push passes the fleet leak gate. The PR remains unmerged for Code-PC Claude;
eventual integration uses a merge commit, never squash or rebase-merge.
