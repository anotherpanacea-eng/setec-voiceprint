# TP-SWEEP shard 42: core contracts, preflight and infrastructure (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for fifteen infrastructure files: judge plumbing, schemas and contracts,
Windows file identity, preflight span and holdout cores, the concreteness
loader, the conformal gate, and two reporting scripts. This is a report only,
with no source, registry or checker change. Admission is by the owner, one
cohort per PR (spec v6). Owner rulings Q1 and Q4 to Q7 are applied as given.

Fleet custody: fleet-coordination #496 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Local | Open | Hold |
|---|---:|---:|---:|---:|---:|---:|
| `controls_audit.py` | 7 | 0 | 3 | 4 | 0 | 0 |
| `setec/core/argument_annotation_contract.py` | 7 | 0 | 0 | 7 | 0 | 0 |
| `setec/core/judge_backends.py` | 7 | 0 | 0 | 7 | 0 | 0 |
| `setec/core/register_typical_baselines.py` | 7 | 0 | 0 | 7 | 0 | 0 |
| `evidence_pack.py` | 6 | 0 | 0 | 6 | 0 | 0 |
| `setec/contract/output_schema.py` | 6 | 0 | 0 | 6 | 0 | 0 |
| `setec/core/windows_descriptor_io.py` | 6 | 0 | 0 | 6 | 0 | 0 |
| `windows_portable_tree.py` | 6 | 0 | 0 | 6 | 0 | 0 |
| `setec/core/cross_doc_consistency_schema.py` | 5 | 0 | 0 | 5 | 0 | 0 |
| `setec/preflight/multiplicity_core.py` | 5 | 0 | 0 | 5 | 0 | 0 |
| `setec/surfaces/conformal_gate.py` | 5 | 0 | 0 | 5 | 0 | 0 |
| `setec/surfaces/dependency_check.py` | 5 | 0 | 0 | 5 | 0 | 0 |
| `setec/core/concreteness.py` | 4 | 0 | 0 | 4 | 0 | 0 |
| `setec/preflight/holdout_core.py` | 4 | 0 | 0 | 4 | 0 | 0 |
| `setec/preflight/span_core.py` | 4 | 0 | 0 | 4 | 0 | 0 |
| **Total** | **84** | **0** | **3** | **81** | **0** | **0** |

## Proposed cohorts

None. The cohort letters CE and CF are unused. No file in this shard defines a
word, sentence or paragraph unit, a quantile over text, or a text-normalizing
fingerprint. The word-count families (T, Hold, E, S, K, N) do not appear:
`register_typical_baselines` reads a YAML table and counts no words.

## Consumer (3)

`controls_audit.py`:

- `:89` `_strip_and_tokenize`. A thin wrapper that returns
  `strip_non_prose(...)` unchanged (Cohort T). Despite its name it does not
  tokenize. It follows the precedent shard 18 used for `_default_preprocessor`.
- `:97` the `strip_non_prose` call inside it.
- `:106` `_function_word_distance_to_baseline`. It counts with
  `stylometry_core.word_tokens` (Cohort R, `:111`) and builds the vector with
  `voice_distance._function_word_vector` (`:115`), which is
  `function_word_features(word_tokens(text))` (`voice_distance.py:129-135`).
  So the count and the vector use the same R tokenizer. The L1 distance is
  analysis logic, not a primitive.

## Local (81)

| File | Sites | Evidence |
|---|---|---|
| `controls_audit.py` | `:247`, `:348`, `:351`, `:461` | an emptiness predicate on cleaned text (`cleaned.strip()`); claim-license and report `rstrip`; the `_RESULTS_KEYS` key table |
| `argument_annotation_contract.py` | `:22`, `:81`×2, `:154`, `:187`, `:190`, `:204` | `_STATES` enum; `_digest` is a bare sha256 of bytes (Q1 ruled out of scope; the policy belongs to whoever produced the cleaned source and block map); four emptiness predicates over the source and node slices |
| `judge_backends.py` | `:29`, `:42`, `:77`, `:115`, `:123`, `:124`, `:186` | `__all__`, `PROVIDERS`; `.strip()` of model-identity strings for the judge and generator disjointness check; a stderr excerpt in an error. This module has no prompt fingerprint, so Q5 is not needed. |
| `register_typical_baselines.py` | `:141`×3, `:142`, `:208`×3 | normalizing register and signal names for YAML lookup and a source label (key table) |
| `evidence_pack.py` | `:154`, `:157`, `:158`, `:163`, `:164`, `:180` | Markdown-to-HTML rendering of its own output (`**bold**` and `` `code` `` patterns, line `rstrip`) |
| `output_schema.py` | `:114`, `:124`, `:133`, `:146`, `:164`, `:331` | regexes that classify result field names for R4 bounds, `key.lower()`, rendering |
| `windows_descriptor_io.py` | `:512`, `:752`, `:754`×2, `:756`, `:802` | `scoped_fingerprint` is a file-identity tuple (volume, file id, size, times, mode, links, attributes), not text; path-anchor `rstrip`/`lstrip` |
| `windows_portable_tree.py` | `:56`, `:67`, `:245`×2, `:1014`, `:1036` | bare sha256 of file bytes and journal bytes (Q1); no text policy |
| `cross_doc_consistency_schema.py` | `:30`, `:177`, `:228`, `:238`, `:269` | `__all__`; non-empty-string predicates on locus quote, rationale, resolution class and `does_not_license`. Locus offsets are checked for type and order only, and no span is computed. |
| `multiplicity_core.py` | `:20`, `:23`, `:25`, `:27`, `:30` | enum and count-key tables. The module reads records and clusters and does no text or offset work. |
| `conformal_gate.py` | `:44`, `:54`, `:82`, `:85`, `:155` | direction enums; parsing a numeric score file (`strip`, `split(",")`) |
| `dependency_check.py` | `:102`, `:113`, `:815`, `:824`, `:832` | platform name, Python version string, report rendering |
| `concreteness.py` | `:79`, `:127`, `:177`, `:325` | `REQUIRED_COLUMNS` CSV header table; `rating_key` (`:127`) and the lookup in `get_concreteness` (`:325`) are both `word.lower()` on a lexicon key: the Brysbaert norms table is a lexicon matched against prose, so this is Local under the Q4 ruling; `:177` trims an exception message |
| `holdout_core.py` | `:24`, `:26`, `:27`, `:29` | `CLASSES`, `STAGES`, `REASONS`; `LABEL` is a slug validator for operator labels (`:379`, `:487`) |
| `span_core.py` | `:20`, `:23`, `:25`, `:27` | `BOUNDARY_CLASSES`, `PROOF_RESULTS`, `DISPOSITIONS`, `STAGES` enums |

## Units the checker did not flag

These are outside the fold. They are recorded because the prompt asked about
span, offset and tokenizer work in these files.

**`concreteness` has no tokenizer.** It only lowercases a word or phrase that
a caller has already tokenized. Its callers tokenize first:
`argmove_profile.mean_concreteness` uses BK on `text.lower()` (shard 31), and
`image_conjunction.evaluate_pair` (`:208-209`) passes spaCy tokens (Q7
territory). A probe with a synthetic five-row CSV through the `data_path`
seam shows the following. The key rule is `str.lower()`, with no casefold and
no NFC:

- `"STRASSE"` misses `straße`.
- `"İstanbul"` misses `istanbul`, because U+0130 lowercases to `i` plus
  U+0307.
- NFD `café` misses NFC `café`.
- The Kelvin sign in `"\u212Aing"` hits `king`.

`rating_key(c) == c.lower()` for every non-surrogate code point (0
differences). So the inline `word.lower()` at `:325` matches `rating_key`
today. But the docstring says the loader and the fetcher measure "the same
quantity" through this helper. Calling `rating_key(word)` at `:325` would
make that true by construction. It is a one-token change.

**`span_core.BoundaryReader` (`:83-202`) is a byte-level span classifier, not
a splitter.** It decides whether a given `[start_byte, end_byte)` span sits on
whole-document, blank-line-paragraph, physical-line or sentence-terminal
boundaries of the raw source bytes. It does not segment text, so it fits no
family. Under Q6 (parts only), its parts are private byte sets (`W`, `H`, `T`,
closers, `:34-38`), not shared patterns, so it gets no row. Probes on synthetic
strings place it against the roster and Cohort C:

| Input | `classify_span` on the first unit | Registered `split_sentences_regex` | R `paragraphs` | C view |
|---|---|---|---|---|
| `One. two three.` | sentence_terminal | no split (needs a capital after) | one | unchanged |
| `He said “no.” Then left.` | sentence_terminal (skips U+201D) | no split (closer before the space) | one | unchanged |
| `A\r\rB` | blank_line_paragraph | no split | one (no `\n`) | `A\n\nB` |
| `A\n\u00a0\nB` | physical_line only (NBSP is not blank) | no split | two (`\s` matches NBSP) | unchanged |
| `\ufeffA\n\nB` | blank_line_paragraph (BOM skipped, `:88`) | keeps the BOM | keeps the BOM | refused (`bom`) |

It recognizes CR, LF and CRLF as line breaks, the same set Cohort C folds
(`common.py:279`). Its offsets are raw bytes, not C's NFC view. That fits a
byte-exact proof: `_proof` compares `source[start:end]` to the candidate bytes
(`:226`). It has a single copy (no other module references it) and no caller
outside the span stage. If proposed Q8 is ruled, it is Local.

**`holdout_core`** consumes Cohort C's view (`record.analysis_text`) through
`overlap_core.preflight_word_ngrams_v1` (`:178`, `:185`). The tokenizer that
function uses, `overlap_core._tokens` (`overlap_core.py:109`), is an
unresolved discovery in another shard. Holdout adds no unit of its own.

**`conformal_gate.threshold_at_fpr_bound` (`:208-214`)** is an order
statistic, `ceil((n+1)(1-q))`, with a tie-raise. It is a statistic over
scores, not text, and it differs from the interpolating quantile cohorts by
design. Probe: on calibration scores 1 to 10 with q = 0.1, the conformal
threshold is 10.0. BA's `a+(b-a)*f` at 0.9 gives 9.1. No row.

## Questions for the owner

None new. Q1 (bare digests) and Q4 (lexicon keys) are applied as ruled. Q5
does not arise.

## Fix by deletion and small fixes (outside the registry's scope)

- `controls_audit.py:69-70` imports `FUNCTION_WORDS` and
  `function_word_features`, and nothing in the module uses them. Delete the
  two names (2 lines).
- `concreteness.py:325`: use `rating_key(word)` instead of an inline
  `word.lower()`. There is no behavior change today (probe above).
- `register_typical_baselines.py:141` and `:208` spell the same
  register-name normalization twice. If one changes, the lookup key and the
  reported `source` label drift apart. A shared one-line helper fixes it.
- `windows_descriptor_io.pin_directory_chain` (`:802-804`) builds its root as
  `"\\?\" + anchor.rstrip("\\/") + "\\"`. `pin_directory` (`:751-756`)
  handles UNC and `\\?\` anchors separately. A replicated-expression probe
  with `PureWindowsPath` shows the following. The module is Windows-only and
  cannot be imported here.
  - `\\server\share\dir` gives `\\?\\\server\share\` (the correct form is
    `\\?\UNC\server\share\`).
  - `\\?\C:\dir` gives `\\?\\\?\C:\`.

  Its callers are `shingle_dedup_checkpoint.py:493` and `:527`. This is not a
  text-primitive finding. Check it on Windows before changing it.

## Method

1. Filtered the checker JSON at `93675ba` to the fifteen files: 84 unresolved
   discoveries, with per-file counts as in the fold table.
2. Read each site in context, along with the callers that feed text into
   `concreteness`, `controls_audit` and the preflight cores.
3. Ran probes on synthetic strings only, with `socket.connect` blocked. The
   live modules imported were `setec.core.concreteness`,
   `setec.preflight.span_core`, `setec.preflight.common._analysis`,
   `setec.core.textprims` and `setec.surfaces.conformal_gate`.
   `stylometry_core.paragraphs` was AST-extracted, because that module imports
   `variance_audit`. `variance_audit` was confirmed absent from
   `sys.modules` after the probes. No corpus, no model.
