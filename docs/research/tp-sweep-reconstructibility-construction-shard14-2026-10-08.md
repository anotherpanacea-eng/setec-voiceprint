# TP-SWEEP shard 14: reconstructibility probe set and construction signature (2026-10-08)

Independent review of the 69 unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `reconstructibility_probe_set.py` and `construction_signature_audit.py`.
This is a report only, with no source, registry or checker change. Admission is
by the owner, one cohort per PR (spec v6). Earlier shards are drafts #584 to
#587 and #589 to #597. The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #456 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local |
|---|---:|---:|---:|---:|---:|
| `reconstructibility_probe_set.py` | 35 | 4 | 0 | 0 | 31 |
| `construction_signature_audit.py` | 34 | 5 | 0 | 1 | 28 |
| **Total** | **69** | **9** | **0** | **1** | **59** |

No site is Open. The owner's later 2026-10-08 rulings settle the only question
these files raise: their 20 construction-frame patterns are Local under "Q4
ruled local". No site falls under Q5, Q6 or Q7. The module's spaCy detectors
(`_detect_passive` at `:249`, `_detect_stacked_pps` at `:299`) produced no
discoveries.

All 69 discoveries for these files are unresolved; the checker reports no other
outcome for them. Shards 14 to 16 run in parallel, so this report gives no
combined remainder.

Both files are root-level scripts that packaging's P4 has not relocated yet
(`packaging_migration_exemptions.yaml:1058` and `:1663`).

Of the 9 Register sites, only 1 is a new row (Cohort AC). The other 8 are
inline spellings that join existing cohorts (B, U, N) or the fallback splitter
that shard 11 recommends deleting (Z). Cohort letter AD is not used.

## Proposed cohorts

### Cohort AC: `population_token_projection` (one row)

| Proposed row | Family | Evidence |
|---|---|---|
| `population_token_projection` | fingerprint | `semantic_sha256` over `[{"unit_id", "tokens": _tokens(text)}]`, rows sorted by UTF-8 `unit_id`, under the domain `setec-reconstructibility-population-token-projection-v1\n` (`:458-467`). `case_policy=lower` (inherited from Cohort B's `_tokens`), `unicode_normalization=none`, `pattern_sha256` null, no backends. |

It tokenizes before hashing, so it is in scope under the Q1 ruling. Probes at
`93675ba` (one unit unless noted):

- "Hello, World!" and "hello world" give equal projections, as do "Café" and
  "caf é". "ab" and "a b" give different ones.
- With two units, swapping their texts changes the projection, and reordering
  the rows does not.
- Its digest differs from `verbatim_cover._content_fingerprint` on the same
  text. The token stream is the same, but the framing (`_frame`, `:219-247`),
  the domain prefix and the `sha256:` prefix are not. This is a new
  fingerprint input, distinct from shard 4's table.

The row must bind the globals it reads: `semantic_sha256` (`:250-251`), `_frame`
(`:219-247`) with `_walk_scalar_tree` (`:119`), and Cohort B's `_tokens` and
`_TOKEN` (`setec/core/verbatim_cover.py:29-34`). It takes a row sequence and a
text mapping, not one string. Characterization can still pass both as JSON
`args`.

**Ownership first (§1).** The owner is a root-level script waiting for P4, so
minting now would bind the ID to a non-final owner. The function has one
caller (`:2247`), and no copy exists elsewhere. The owner chooses whether to
wait for P4 or move it in R1. An R1 move also changes this script's bytes, and
`builder_source_sha256` (from `_git_identity`, `:2156`) is bound into every
checkpoint binding. Any edit to this file has that cost, not just this move.

**Deletion test.** If this row is never minted, a drift in its behavior is
**not silent**. Each owner plan commits `population_token_projection_sha256`,
and `run` refuses a mismatch with `population_token_projection_refused`
(`:2247-2249`). So the row's value is limited to the R2 completion rule, which
requires every discovery to be reconciled. It adds little protection. The
owner could reasonably defer it to the P4 relocation and mint it then, at the
final path.

Register: 1 (`:458`).

### Inline units that join existing cohorts

Following the Cohort B contract (#588), these inline uses are not legacy sites
or bypasses. They stay unresolved candidates until a later cohort reconciles
them. They are counted as register-bound under the cohort they would join, not
as Consumer, because none of them calls a registered callable.

- **Cohort B (3): `lower_to_source_matches`** (`:479-522`). The `_TOKEN.finditer`
  at `:481` is one of the two offset sites #588 names. The function lowercases
  the whole text (`:480`), finds `_TOKEN` matches in it, and maps each match
  back to source offsets with a per-character `ch.lower()` walk (`:488`). It
  checks its own token values against `_tokens(text)` (`:482`, `:515`) and
  refuses on any difference. `_TOKEN` and `_tokens` here are the owner's
  objects (`is` holds against `setec.core.verbatim_cover`). A seeded fuzz (seed
  14, 200,000 strings weighted toward U+0130, U+03A3, U+212A, `ß`, U+0301 and
  ligatures) found zero refusals. Its token values and lowered string equalled
  `_tokens(text)` and `text.lower()` on every input. So it adds offsets, not a
  new token unit. All three sites (`:480`, `:481`, `:488`) stay with B.
  Registering the offset map as its own tokenizer row would add nothing that
  the self-check and the existing binding (see Observations) do not already
  cover.
- **Cohort U (1): inline blockquote stripper** in
  `construction_signature_audit.detect_constructions` (`:467-471`; the
  discovery is the `lstrip` at `:470`). Its expression has the same AST as
  `phraseological_signature_audit._strip_blockquotes`' return expression
  (`phraseological_signature_audit.py:122-126`), and it reads no globals.
  **It is behavior-equal.** Probes:
  - The expression, compiled from the live source, compared against both
    `phraseological_signature_audit._strip_blockquotes` and
    `semantic_preservation_check._strip_blockquotes`: a seeded fuzz (seed 14,
    200,000 strings over `>`, spaces, tabs, `\r`, `\r\n`, NBSP, U+001C to
    U+001E, U+0085, U+2028, U+2029 and U+3000) found zero differences.
  - Through the live function, `detect_constructions(t)` equals
    `detect_constructions(_strip_blockquotes(t), keep_quotes=True)` in hits
    and `n_words` on 3,006 inputs.
  - It is not Cohort Y's quote stripper. On `"x\n> q1\n> q2\ny"`, this gives
    `'x\ny'` and `aic_pattern_audit`'s `re.sub` gives `'x\n\n\ny'`.

  Under #588 it stays an inline candidate. Folding it into U means calling the
  shared function, which changes call shape, so that is outside an
  ownership-only cohort.
- **Cohort N (1): density denominator** `n_words = len(re.findall(r"\b\w+\b", text))`
  (`:474`). It is the same pattern string as `stance_modality_audit._WORD_RE`
  (`stance_modality_audit.py:151`, Unicode flag). A seeded fuzz (200,000
  strings with digits, `_`, apostrophes, hyphens, combining marks, CJK,
  Arabic-Indic digits and superscripts) against `stance_modality_audit._word_count`
  found zero differences. The live `n_words` also equals `_word_count` of the
  stripped text on all 3,006 inputs. This adds no new word-count unit.

Register: 5.

### Cohort Z: fallback splitter (cited, not re-derived)

`construction_signature_audit.py:83-90` is the `ImportError` fallback that shard
11 (#597) found to be the third AST-identical copy, alongside
`aic_pattern_audit.py:79-86` and `semantic_preservation_check.py:97-98`. Shard
11 recommends deleting the `except` branch rather than registering it. Its
reasons: the fallback runs only when `variance_audit` is broken but
`output_schema` and `claim_license` import fine (`:76-80` here have no
fallback), and then the surface switches splitters silently. This file adds one
point to that case. Unlike in `aic_pattern_audit`, `HAS_SPACY` and `_NLP` are
read here (`:261`, `:308`, `:449`, `:587`, `:714`, `:808`). So the cut removes
the `try`/`except` wrapper and its fallback body (`:83`, `:85-90`) and keeps the
plain `variance_audit` import, about six lines. With
`variance_audit` blocked, the live module binds the fallback and returns `['']`
on blank input. The `sent.strip()` and empty skip at `:478-480` absorb that, so
the blank-input difference from Cohort F does not reach hit counts in this
surface.

Register (blocked, recommend deletion): 3 (`:89`, `:90`×2).

## Hold (1)

`construction_signature_audit.py:567`: `len(prefix.split())`, a whitespace word
count. It keeps a fronted-adverbial hit only when the prefix has 2 to 8 words.

## Local (59)

### Construction-frame patterns, Q4 ruled local (20)

These are `construction_signature_audit`'s construction-frame patterns, matched
against prose. Under the owner's ruling they are this surface's own analytic
rule, with no lexicon family:

- 14 compiled patterns: cleft (`:133`), pseudo-cleft (`:141`), existential
  `there` (`:146`), the two extraposition patterns (`:171`, and `:175`, built
  from the `_EXTRAPOSITION_PREDICATES` adjective lexicon at `:154`), the five
  correlatives (`:186`, `:193`, `:199`, `:205`, `:211`), and the concessive,
  participial, fronted-adverbial and parenthetical patterns (`:218`, `:226`,
  `:234`, `:241`).
- 6 uses in `detect_constructions`: `:490`, `:504`, `:517`, `:523`, `:530` and
  `:577`.

No other module imports them.

### `reconstructibility_probe_set.py` (31)

- **Bare digests (4):** `semantic_sha256` (`:251`, `sha256` and `hexdigest`)
  hashes a domain prefix plus `_frame(value)` for any JSON-like value.
  `_frame` encodes strings as their UTF-8 bytes with no normalization, so the
  function applies no text policy. `plain_sha256` (`:255`×2) hashes raw bytes.
  Both are out of scope under Q1. Their policy belongs to the caller, which for
  the one text-derived caller is Cohort AC.
- **Identifier and path validation (9):** the `_DIGEST`, `_UTC`, `_COMPONENT`,
  `_DEVICE` and `_HEX40` patterns (`:88-92`); `portable_private_relative_path_v1`'s
  splits (`:275`, `:281`); `portable_collision_key` (`:288`), also imported by
  `passage_consumer_authority.py:34`; and the private-root split in
  `_DarwinPrivateTree.__init__` (`:987`).
- **Directory-entry collision checks (6):** `_exact_entry` lowercases,
  casefolds and NFC-normalizes file names to detect case and normalization
  collisions (`:1140`, `:1141`×2, `:1167`, `:1168`×2).
- **Schema key and enum tables (7):** `PARTITIONS` (`:61`), `POPULATION_KEYS`
  (`:304`), `ATTESTATION_KEYS` (`:309`), `PLAN_KEYS` (`:317`), `digest_fields`
  (`:378`), `SHARD_KEYS` (`:676`) and `expected_root` (`:1677`).
- **Checkpoint file names (2):** `score-NNNNNNNN.json` (`:1404`) and its
  `.stage` form (`:1653`).
- **Scorer output parsing (1):** the histogram-key check `0|[1-9][0-9]*`
  (`:595`).
- **Git output (1):** `strip` of `git` stdout in `_git_identity` (`:877`).
- **Resource preflight (1):** `len(text.lower())` in `preflight_resources`
  (`:909`) sizes the lowering work. Only the length is read.

### `construction_signature_audit.py`, other sites (8)

- **Analysis over already-split sentences (2):** the per-sentence `strip`
  (`:478`) and the concessive opener's display span, `sent.split(",", 1)[0]`
  (`:538`). The span is display text; no count reads it.
- **Baseline file handling (2):** the suffix filter (`:647`) and the blank-file
  skip (`:665`).
- **Rendering (4):** the claim-license block `rstrip` (`:842`), the table
  `headers` list (`:960`), the excerpt `strip` (`:997`) and the report `rstrip`
  (`:1011`).

## Word counts, splitters, preprocessors and fingerprints: what these files add

| Unit | Where | Disposition |
|---|---|---|
| `[a-z0-9]+` after lowering, with source offsets | `lower_to_source_matches` | joins Cohort B; no new token unit |
| `\b\w+\b` count | `construction_signature_audit.py:474` | joins Cohort N; no new unit |
| whitespace `split()` count | `construction_signature_audit.py:567` | Hold |
| fallback sentence splitter | `construction_signature_audit.py:89-90` | Cohort Z (shard 11); recommend deletion |
| line-drop blockquote stripper | `construction_signature_audit.py:467-471` | joins Cohort U; behavior-equal |
| framed per-unit token stream, domain-separated | `population_token_projection` | Cohort AC; a new distinct fingerprint input |

## Observations outside this sweep

- **The binding already pins the tokenizer.** `run` hashes the whole defining
  module of `audit_originality`, which is `setec/core/verbatim_cover.py`, into
  `originality_source_sha256` (`:2259-2260`). It also hashes
  `lower_to_source_matches` output on seven fixed strings (U+0130, U+03A3 in
  final and medial position, U+0301) into `token_semantics_sha256`
  (`:2261-2280`). Both go into every checkpoint binding. The second is a
  text-derived digest that the checker does not discover, because it sees only
  the `hashlib` calls inside `semantic_sha256` and not calls to that helper.
  Any edit to `verbatim_cover.py`, including the loaders that Cohort B does not
  freeze, changes every new binding. That is fine for B, which moves nothing,
  but a later cohort that edits that module should expect it.
- **Quadratic retokenization in `valid_anchors`.** `tokens = _tokens(text)`
  (`:554`) does not depend on the loop variable but runs once per candidate
  anchor. Measured at `93675ba` with 32-word prompt and suffix and no masks:
  2,000 tokens took 0.28 s, 4,000 took 1.07 s and 8,000 took 4.19 s, about 4×
  per doubling. `MAX_DOCUMENT_TOKENS` is 250,000 (`:75`), and
  `preflight_resources` does not count this work. Hoisting the line above the
  loop changes no output. This is recorded, not dispositioned.

## Method

1. Filtered the checker output at `93675ba` to the two files: 35 and 34
   discoveries, all unresolved.
2. Read each site in context, along with the importers
   (`passage_consumer_authority.py`, `passage_authority_package_transaction.py`,
   `passage_lineage_crosswalk.py`), the `originality_audit` launcher and its
   re-export, and the packaging exemptions.
3. Ran the probes and seeded fuzzes above against the live modules, with
   `socket.connect` disabled. Importing `variance_audit` calls `nltk.download`
   on this host (shard 7's observation), so the probes blocked it
   (`sys.modules["variance_audit"] = None`), and `construction_signature_audit`
   bound its fallback splitter. No probe relied on `variance_audit`'s splitter,
   and nothing touched the network. No model call.
