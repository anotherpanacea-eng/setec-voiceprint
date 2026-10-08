# TP-SWEEP shard 4: self-exclusion fingerprint siblings (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for five of the 16 other modules that define their own `_content_fingerprint`.
These are siblings of Cohort B's `verbatim_cover._content_fingerprint`. This is
a report only, with no source, registry or checker change. Admission is by the
owner, one cohort per PR (spec v6). Earlier shards: drafts #584, #585 and #586.

Fleet custody: fleet-coordination #442 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local |
|---|---:|---:|---:|---:|---:|
| `setec/surfaces/general_imposters.py` | 15 | 9 | 0 | 0 | 6 |
| `setec/surfaces/rank_turbulence_audit.py` | 15 | 12 | 0 | 0 | 3 |
| `setec/surfaces/cross_doc_novelty_profile.py` | 12 | 9 | 0 | 1 | 2 |
| `setec/surfaces/crosslingual_voice_distance.py` | 17 | 14 | 0 | 2 | 1 |
| `voice_distance.py` | 17 | 0 | 6 | 0 | 11 |
| **Total** | **76** | **44** | **6** | **3** | **23** |

The labels are as in earlier shards, plus **Hold**: a whitespace word count
(`len(text.split())`) that should be minted once as a family, after a shard
reviews its many siblings (see shard 3's Cohort I).

After shards 1 to 4, 3,125 of the checker's 3,442 unresolved discoveries remain
unreviewed. The other 11 fingerprint modules hold about 425 more.

## Finding: the fingerprints differ on purpose

Each `_content_fingerprint` is designed to share its own surface's equivalence
class, the "matcher-aligned" rule in their docstrings. So none of them may be
consolidated with Cohort B or with each other:

| Owner | Fingerprint input | Joined by |
|---|---|---|
| `verbatim_cover` (Cohort B) | lowercase, then `[a-z0-9]+` | `\x1f` |
| `general_imposters` | `\w+` (Unicode), each token lowercased | `\x1f` |
| `rank_turbulence_audit` | lowercase, then `[a-z]+` | `\n` |
| `cross_doc_novelty_profile` | `stylometry_core.normalize_for_char_ngrams` (lowercase, whitespace collapsed, stripped) | n/a |
| `crosslingual_voice_distance` | `_normalize`: NFC, `\s+` collapsed to one space, stripped; case and punctuation kept | n/a |
| `voice_distance` | the `strip_non_prose`-cleaned string itself (a bare digest; out of scope per Q1) | n/a |

Each fingerprint row therefore binds its own surface's tokenizer or normalizer.
A fingerprint that hashes its input unchanged has no text policy of its own and,
under the owner's Q1 ruling, is not registered.
Where that unit is itself a primitive, it is the same cohort's second row.

## Proposed cohorts

### Cohort J: `rank_turbulence_audit` (one fingerprint row)

- `_content_fingerprint` (`:156`, `:181`): sha256 of the
  `"\n".join(_TOKEN.findall(text.lower()))` stream, with
  `_TOKEN = re.compile(r"[a-z]+")` (`:38`).
- `_counts` (`:42`) uses the same `_TOKEN.findall(text.lower())` inline
  expression. Under the Cohort B contract's treatment of inline uses of a bound
  pattern, `:42` is a consumer of the row's `_TOKEN`. If the reviewer of that
  contract rejects that treatment, this cohort needs the same answer.
- The four calls in `_load_baseline` (`:191`, `:200`, `:217`, `:227`) are uses
  of the row inside its owner.

Register: 12.

### Cohort K: `crosslingual_voice_distance` text units (four rows)

| Proposed row | Family | Evidence |
|---|---|---|
| `count_words` | tokenizer | `\b\w[\w'-]*\b`, Unicode (`:47`, `:53-54`). A third distinct `count_words`, after shard 2's two. |
| `_normalize` | preprocessor | NFC, then `\s+` collapsed to one space, then strip (`:48`, `:57-58`). It feeds `char_ngram_counts` and the fingerprint. |
| `_content_fingerprint` | fingerprint | sha256 of `_normalize(text)` (`:68`, `:81`). Called at `:217` and `:458`. |
| `_SENT_SPLIT_RE` | sentence_splitter | Multilingual terminal punctuation `[.!?。！？…।]+` (`:49`), applied inline in `aux_profile` (`:96`). Not a function, so the row names the compiled pattern object, as spec §1 permits. |

Register: 14. Hold: `aux_profile`'s whitespace tokens and per-sentence word
counts (`:94`, `:97`).

### Cohort L: `general_imposters` tokenizer and fingerprint (needs one source change first)

- `_tokens` (`:264-274`): `\w+` (Unicode) with per-token `.lower()`.
- `_content_fingerprint` (`:281`, `:292`): `\x1f` join plus sha256.
- The two `_exclude_target_path` calls (`:231`, `:243`) use them.

**Blocked:** `_tokens` compiles its pattern lazily, assigning a module global
`_TOKEN_RE` (initially `None`) from inside the function through
`global _TOKEN_RE`. Spec §2 keeps rebinding and dynamic construction
unresolved, and a row's dependencies must be bound exactly once. Registering
these rows therefore first needs a separate, reviewed change that compiles
`_TOKEN_RE` once at module level, which changes no output. That is
behavior-change work under the firewall rule, not part of an ownership-only
cohort. Register: 9.

### `voice_distance` fingerprint: out of scope under the owner's Q1 ruling

`_content_fingerprint(cleaned_text)` (`:138`, `:155`) is
`hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest()`. It reads no module
global and applies no case, normalization or segmentation rule. All of its text
policy lives in the caller's `strip_non_prose` call (`:786-797`).

The owner ruled on 2026-10-08 (shard 1, Q1) that a bare digest with no text
policy is out of scope. Its text policy belongs to the preprocessing row, here
`setec/core/preprocessing.py:strip_non_prose`. So this is local, as are the
calls at `:797` and `:814`. Six other modules have the same body, byte for byte
(`stance_modality_audit`, `function_word_grammar_audit`,
`agency_abstraction_audit`, `discourse_move_signature`,
`punctuation_cadence_audit` and `paragraph_audit`). The same ruling applies to
them. Local: 5.

### Quantile (blocked by a nested definition)

`cross_doc_novelty_profile._abs_z_distribution` defines `_percentile` as a
nested function (`:173`), a linear-interpolation percentile used for p10, p50
and p90 (`:191-193`). It is the first quantile-family candidate any shard has
found; it is the only `_percentile` definition in production source. A
nested function has no module-level `module:symbol`, so registering it first
needs an R1 move to module level, unchanged. Spec §4 requires that each
existing site's empty-input and interpolation behavior be kept: the outer
function returns early for `n == 0` (`:170-172`) and `_percentile` returns the
single value for `n == 1`. Register (blocked): 4.

`cross_doc_novelty_profile._content_fingerprint` (`:134`, `:145`; calls `:527`,
`:532`) is sha256 of `stylometry_core.normalize_for_char_ngrams(text)`. It
belongs with that normalizer's eventual row, so it is a register-bound row for
the `stylometry_core` shard. Register: 5. Hold: `:557`, the pool word count.

## Consumer (6)

These are calls in `voice_distance.py` to primitives defined elsewhere:
- `_function_word_vector` (`:129`) and `_baseline_mean_function_word_vector`
  (`:158`) call `function_word_features(word_tokens(...))` from
  `stylometry_core`.
- `strip_non_prose` calls at `:268`, `:287`, `:753` and `:790`.

## Local (23)

- `voice_distance.py` fingerprint (5), per the Q1 ruling above.

- **Manifest or file handling:**
  - `general_imposters.py:173`×2 (comment and blank-line skip)
  - `rank_turbulence_audit.py:197` (suffix check) and `:207` (manifest line)
  - `cross_doc_novelty_profile.py:90` (suffix) and `:107` (manifest line)
  - `crosslingual_voice_distance.py:204` (suffix)
- **Emptiness predicates on prose:** these return or refuse, and transform
  nothing. Sites: `rank_turbulence_audit.py:253`, and `voice_distance.py:292`,
  `:306` and `:624`.
- **Output rendering and tables:**
  - `general_imposters.py:798` and `:827` (Markdown `rstrip`), `:423`
    `metadata_keys` and `:744` `lic.references`
  - `voice_distance.py:53-54` (Markdown cell escaping) and `:1026` (envelope
    keys excluded from `results`)

## Method

1. Ran the checker at `93675ba` and filtered to the five files.
2. Read each `_content_fingerprint` with its docstring, the tokenizer or
   normalizer it binds, and its call sites.
3. Read each remaining site in context. No model call.
