# TP-SWEEP shard 43: core and contract tail (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for eighteen files: the registry module itself, the frozen passage tokenizer,
the passage remediation projection, the acquisition content hash, schemas and
contracts, two preflight tables and the paraphrase-robustness calibration. This
is a report only, with no source, registry or checker change. Admission is by
the owner, one cohort per PR (spec v6). Owner rulings Q1 and Q4 to Q7 are
applied as given.

Fleet custody: fleet-coordination #498 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`. The checker JSON
has 38 unresolved discoveries in these files, not the 36 the prompt expected.
The difference is real: `textprims.py:54` holds two `.strip()` calls
(`s.strip() for s in ... if s.strip()`), and the checker reports each one, as
it does for every `sha256(...).hexdigest()` pair.

| File | Discoveries | Register | Consumer | Local | Open | Hold |
|---|---:|---:|---:|---:|---:|---:|
| `setec/calibration/paraphrase_robustness.py` | 4 | 1 | 0 | 3 | 0 | 0 |
| `setec/core/argument_certainty_calibration_schema.py` | 4 | 0 | 0 | 4 | 0 | 0 |
| `setec/contract/claim_license.py` | 3 | 0 | 0 | 3 | 0 | 0 |
| `setec/contract_validate.py` | 3 | 0 | 0 | 3 | 0 | 0 |
| `setec/core/storyscope_polarity_contract.py` | 3 | 0 | 0 | 3 | 0 | 0 |
| `setec/core/acquisition_primitives.py` | 2 | 0 | 0 | 2 | 0 | 0 |
| `setec/core/argument_feature_schema.py` | 2 | 0 | 0 | 2 | 0 | 0 |
| `setec/core/atomic_publish.py` | 2 | 0 | 0 | 2 | 0 | 0 |
| `setec/core/passage_remediation_projection.py` | 2 | 0 | 0 | 2 | 0 | 0 |
| `setec/core/passage_tokenizer_v1.py` | 2 | 0 | 0 | 2 | 0 | 0 |
| `setec/core/register_taxonomy.py` | 2 | 0 | 0 | 2 | 0 | 0 |
| `setec/core/textprims.py` | 2 | 2 | 0 | 0 | 0 | 0 |
| `setec/preflight/final_core.py` | 2 | 0 | 0 | 2 | 0 | 0 |
| `setec/core/embeddings.py` | 1 | 0 | 0 | 1 | 0 | 0 |
| `setec/core/narrative_feature_schema.py` | 1 | 0 | 0 | 1 | 0 | 0 |
| `setec/core/pool_guard.py` | 1 | 0 | 0 | 1 | 0 | 0 |
| `setec/core/rank_space_signals.py` | 1 | 0 | 0 | 1 | 0 | 0 |
| `setec/preflight/p7_report_core.py` | 1 | 0 | 0 | 1 | 0 | 0 |
| **Total** | **38** | **3** | **0** | **35** | **0** | **0** |

## Proposed cohorts

None. The cohort letters CG and CH are unused. The 3 Register sites join
existing units: two are operations inside the already-registered
`split_sentences_punkt` row, and one is an inline spelling of Cohort H.

## The registry module: `textprims.py` (2, Register)

`split_sentences_punkt` (`:52-54`) is a registered row
(`sentence_splitter-0a069702b2f6-v1`), and the checker recognizes its
definition (`:52`). Its two `.strip()` calls at `:54` are unresolved for one
reason only: the checker's operation-level recognizer at
`tools/gen_textprims_inventory.py:242` names a single owner,
`owner == "split_sentences_regex"` (plus the `_SENT_RE =` compile). The
identical `p.strip()` pair inside `split_sentences_regex` (`:59`) is therefore
recognized, and the pair inside `split_sentences_punkt` is not. They are not a
helper the registry uses, and not registry plumbing: they are the strip-and-filter
step of a registered row, and the row's `behavior_sha256` already covers them.

- Disposition: Register, under the existing row. No new row.
- Fix: one token in the checker. Recognize operations whose owner is any
  registered defining function in `OWNER` (or add `split_sentences_punkt` to the
  test at `:242`). That clears both sites.
- Probe: `split_sentences_punkt` raises `LookupError` here because the NLTK
  Punkt data is not installed, and no download was attempted. The regex row
  splits `"  One.  Two here.  "` into `['One.', 'Two here.']`.

The module's other plumbing (`__getattr__` lazily re-exporting the frozen
`tokenize`, `:83-87`, and the `_MappingProxyType` tables) raised no discovery.

## The frozen passage tokenizer: `passage_tokenizer_v1.py` (2, Local)

`_digest` (`:35-36`) returns `"sha256:" + sha256(payload).hexdigest()`. Its only
use is `load_data`'s data-commitment check (`:82`): it hashes a domain prefix
plus the `_frame` encoding of the committed JSON table, and compares the result
with `data_commitment_sha256`. It never sees prose. It is plumbing around the
frozen `tokenize` (`:106-126`), which is the registered row. Local: a bare
digest (Q1) of the tokenizer's own data table. It should not become a row.
Probe: `load_data()` loads offline, and `_digest` recomputes the stored
commitment exactly.

## `passage_remediation_projection.py` (2, Local) and where `n_words` comes from

**This module computes neither `n_words` nor passage boundaries.** Like the two
authority modules shard 25 (#610) reviewed, it takes them as data.
`_complete_passage_partition` (`:83-184`) checks each Stage-A row's closed keys
(`_PASSAGE_KEYS`, `:76`), its digest form (`_DIGEST`, `:80`, used at `:103`),
its types, `0 <= char_start < char_end` and `n_words > 0` (`:108-116`). It then
copies the values into the partition (`:170-179`). The only cross-check is that
each cluster member equals its complete-list row (`:144-146`), and both come
from the same producer. `_project_interval` (`:187-215`) maps given source
spans onto lineage units by integer offset arithmetic. It reads no text.
Probe: a row with `char_start=0`, `char_end=5` and `n_words=999` is accepted
unchanged.

The two discoveries are both Local: `_PASSAGE_KEYS` is a closed key table, and
`_DIGEST` is a digest-form validator.

**The producer is `setec/surfaces/near_dup_dedup.py`:**

- **Boundaries.** `split_passages` (`:655-681`) splits on `\n\s*\n+` and gives
  the offsets of the stripped paragraph. This is Cohort AA's paragraph
  splitter. `chunk_document` (`:693-717`) assigns the ordinals and ids.
- **`n_words`.** `_passage_provenance` (`:720-736`) sets
  `n_words = len(tokenizer(p.text))` (`:734`). At the inventory call
  (`:1731-1734`), the tokenizer is `_strict_token_words` (`:1376-1377`, which
  is the registered frozen `tokenize`) when `strict_spec80` is set, and
  `_norm_tokens` (`:684-686`, Cohort AA, `\w+` then lowercase) otherwise.
- **Cohort T.** It is not in the chain. `near_dup_dedup` never calls
  `strip_non_prose`, and strict Spec-80 mode reads the raw strict-UTF-8
  payloads (`:1362-1373`).

**Probes (Python 3.13.7, Unicode 15.1):**

- The frozen table's word set equals the host's `\w` on every non-surrogate
  code point (0 differences). Its lowercase map equals `str.lower` on every word
  character (0 differences). So on this host, `n_words` is the same under
  either tokenizer, and passage boundaries agree.
- The normalized tokens differ on context-sensitive final sigma. The frozen
  tokenizer maps each character independently, so `"ΟΔΟΣ"` becomes `οδοσ`.
  AA's `t.lower()` gives `οδος`. That changes shingle keys, not counts.
- A host with a different Unicode database could disagree on counts as well.
  Preventing that is what the frozen table is for.

**Callers.** `project_remediation` (`:265`) has no non-test caller at
`93675ba`. The module docstring assigns admission to "the future transaction
wrapper". `passage_authority_package_transaction` imports only the
`RemediationProjection` type and `_semantic_sha`.

## `acquisition_primitives.py` (2, Local)

`compute_content_hash` (`:37-45`) is `"sha256:" + sha256(text.encode("utf-8"))`,
and its only globals are `hashlib` and the methods it calls. It is a bare digest
and Local under Q1, as shard 19 found. Its text policy belongs to the caller's
cleaning (`acquisition_core.py:774`, which hashes `cleaned_text`). Probe:
CRLF and LF, case, and NFC and NFD spellings all give different hashes.

Two related points:

- The legacy CRLF retry in `acquisition_core.py:1021-1031` is a caller-side
  representation rule, not part of the primitive.
- The docstring anticipates "a normalized-text fingerprint" as a future hash
  family. That would be in scope (Register, fingerprint) if it is ever built.

## `paraphrase_robustness.py` (4: Register 1, Local 3)

`StdlibProxyParaphraser._one_pass` (`:212-226`) is the bundled model-free attack.
It splits on a literal space, swaps words from a closed synonym table and
collapses whitespace. The text it produces is a perturbation, not a
measurement unit. Under Q6 (parts only), the composite is Local and its parts
are placed one by one:

| Site | Disposition | Evidence |
|---|---|---|
| `:214` `text.split(" ")` | Local (Q6 parts only) | A reassembly split paired with `" ".join`. It keeps tabs and NBSP inside tokens (`"The big\tdog"` gives `['The', 'big\tdog']`). It is not a word unit, and it has one copy. |
| `:215` `tok.lower()` | Local (Q4 ruled local) | The lookup key into `_PROXY_SYNONYMS`, which is a lexicon matched against prose. |
| `:226` `" ".join(" ".join(out_tokens).split())` | Register (Cohort H, inline) | Probe: with an empty synonym table, `_one_pass(s)` equals H's `_normws(s)` (`setec/core/fallacy_judge.py:191`) on all 16 test strings, including tab, NBSP, U+2028 and U+3000. Under the Cohort B contract (#588) it stays an unresolved candidate counted under H. |
| `:607` `paraphraser_label.strip()` | Local | An emptiness predicate on a payload label. |

The class has no non-test caller. `run_report` requires a caller-supplied
paraphraser (`:382`). The CLI's injected-scores path runs with
`apply_paraphraser=False` (`:696-704`). See "Fix by deletion" below.

The module imports `variance_audit` through `validation_harness`, so the class
was AST-extracted for the probe.

## Local (remaining 27)

| File | Sites | Evidence |
|---|---|---|
| `claim_license.py` | `:62`, `:187`, `:195` | Trims a label fragment file's trailing LF, rstrips the rendered block, and replaces `_` with a space in a rendered key. |
| `contract_validate.py` | `:38`, `:43`, `:44` | Expected-envelope key and reason-category tables. |
| `argument_certainty_calibration_schema.py` | `:32`, `:140`, `:198`, `:231` | `__all__`, plus non-empty predicates on the locus quote, the rationale and `does_not_license`. |
| `argument_feature_schema.py` | `:59`, `:325` | `__all__`, plus the `_no_anchor_tiers` tier-name set in an import-time check. |
| `atomic_publish.py` | `:153`, `:295` | `os.replace` (a file operation, flagged by its name) and `__all__`. |
| `embeddings.py` | `:112` | `word.lower()` as the key into the spaCy vocab: a lexicon lookup (Q4 ruled local). It is a lookup, not a segmentation, so Q7 does not apply. Its caller is `image_conjunction.py:215`, through `cosine_similarity`. |
| `narrative_feature_schema.py` | `:65` | `__all__`. |
| `pool_guard.py` | `:77` | Strips a JSONL manifest line before `json.loads`. |
| `rank_space_signals.py` | `:117` | `_rank_of_token` ranks a model token id within a log-prob vector, which is model output (`:199`). It was flagged by its name. |
| `register_taxonomy.py` | `:219`×2 | `registry_digest` hashes the canonical JSON of the register-to-tier mapping (`:206-214`), not prose. |
| `storyscope_polarity_contract.py` | `:12`, `:49`×2 | `__all__`, plus `framed_digest`: a domain-separated sha256 of bytes with no text policy (Q1). Probe: it equals `sha256(domain + "\n" + len8 + payload)`. |
| `final_core.py` | `:21`, `:25` | `FINDINGS` and `TUPLE_FIELDS` enums. |
| `p7_report_core.py` | `:11` | The `P7_OBLIGATIONS` table. |

## Units the checker did not flag

These sit outside the fold.

`storyscope_polarity_contract.count_source_words` (`:72-74`) returns
`narrative_longform_segment.count_words(text)`, which is `len(_WORD.findall(text))`
with `_WORD = \S+` (`narrative_longform_segment.py:57`, `:147-148`). Probe: it
equals `preprocessing.count_tokens` (Cohort T) on all 16 test strings. So this
is a Consumer of a T-equal count, and the `\S+` copy in
`narrative_longform_segment` belongs to that file's shard.
`source_work_sha256` (`:65-69`) is `framed_digest` over the raw text (Q1,
Local).

## Word counts and splitters: what this shard adds

- **No new word, sentence or paragraph unit, quantile or fingerprint.**
- **The passage-row chain is now placed.** Boundaries come from AA
  (`split_passages`). `n_words` comes from the registered frozen `tokenize` in
  strict Spec-80 mode, and from AA's `_norm_tokens` otherwise. The two give the
  same counts on this host, and their tokens differ on final sigma. T is not
  involved.
- **No optional-dependency branch.** `embeddings` needs a spaCy vectors model,
  and it refuses with `EmbeddingsBackendError` when none is installed (`:94`).
  It does not silently fall back.

## Questions for the owner

None new. Q1 (bare digests), Q4 (lexicon keys) and Q6 (parts only) are applied
as ruled. Q5 and Q7 do not arise. The pending Q8 would not change any
disposition here.

## Fix by deletion and small fixes (outside the registry's scope)

- **Checker:** `tools/gen_textprims_inventory.py:242` should recognize
  operations inside any registered defining function in the registry module,
  not only `split_sentences_regex`. This is a one-line change, and it clears
  the 2 Register sites at `textprims.py:54`.
- **`StdlibProxyParaphraser` (`paraphrase_robustness.py:199-232`)** has only
  test callers. The deletion test: if it were moved into the test suite,
  production would lose nothing, and the 3 discoveries at `:214`, `:215` and
  `:226` would leave the inventory (about 35 lines). The module docstring
  presents it as the bundled M1 paraphraser, so this is the owner's call.
- **`project_remediation`** has no production caller yet. It is staged for a
  future wrapper, so it is not a deletion candidate. Recorded so the next
  passage shard does not look for a live producer here.

## Method

1. Filtered the checker JSON at `93675ba` to the eighteen files: 38 unresolved
   discoveries, with per-file counts as in the fold table.
2. Read each site in context. Traced the passage rows to `near_dup_dedup`, and
   the content hash to `acquisition_core`.
3. Ran probes on synthetic strings only, with `socket.connect` blocked.
   - Live modules imported: `setec.core.passage_tokenizer_v1`,
     `passage_remediation_projection`, `acquisition_primitives`,
     `storyscope_polarity_contract`, `preprocessing` and `textprims`.
   - AST-extracted, because their modules import `variance_audit` or carry
     heavy imports: `near_dup_dedup.split_passages` and `_norm_tokens`,
     `fallacy_judge._normws`, and `paraphrase_robustness.StdlibProxyParaphraser`.
   - `variance_audit` was confirmed absent from `sys.modules` after the probes.
   - The frozen-table scan covered all 1,112,064 non-surrogate code points.
   - No corpus, no model, no NLTK data download.
