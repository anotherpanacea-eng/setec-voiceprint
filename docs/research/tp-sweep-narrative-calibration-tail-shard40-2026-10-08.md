# TP-SWEEP shard 40: narrative segmenter and calibration tail (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for the long-form narrative segmenter, its agreement harness, the narrative
decision audit, and seven calibration benchmarks, monitors and helpers. This is
a report only, with no source, registry or checker change.

Fleet custody: fleet-coordination #494 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site is left Open. Two cohorts are proposed: CA and CB.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `calibration/narrative_longform_agreement.py` | 11 | 0 | 0 | 0 | 11 | 0 |
| `setec/core/narrative_longform_segment.py` | 9 | 3 | 0 | 0 | 6 | 0 |
| `calibration/aitdna_benchmark.py` | 8 | 0 | 0 | 0 | 8 | 0 |
| `setec/calibration/paraphrase_ladder.py` | 8 | 0 | 0 | 0 | 8 | 0 |
| `calibration/calibration_survey.py` | 6 | 2 | 0 | 1 | 3 | 0 |
| `calibration/pan_voight_kampff_benchmark.py` | 6 | 0 | 0 | 0 | 6 | 0 |
| `calibration/polarity_audit.py` | 6 | 0 | 0 | 0 | 6 | 0 |
| `calibration_drift_monitor.py` | 6 | 0 | 0 | 0 | 6 | 0 |
| `setec/surfaces/narrative_decision_audit.py` | 6 | 2 | 0 | 0 | 4 | 0 |
| `calibration/launchd/setup_launchd.py` | 5 | 0 | 0 | 0 | 5 | 0 |
| **Total** | **71** | **7** | **0** | **1** | **63** | **0** |

All 71 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## Register (7)

### `narrative_longform_segment` word count joins Cohort T (2)

`_WORD = re.compile(r"\S+")` (`setec/core/narrative_longform_segment.py:57`)
and `count_words` (`:147-148`). Probe: on 12 synthetic strings (empty, NBSP,
U+2003, U+200B, `\x1c`, `\x85`, `\f\v`, U+3000, CRLF, a combining accent),
`nls.count_words` equaled `preprocessing.count_tokens` and `len(text.split())`
on every one. Pattern and flags are identical (`\S+`, flags 32). This is the
count form of Cohort T, not a new word unit. It is a separate named copy, so
joining T is a binding change for T's builder (re-export T's object as `_WORD`).
That change is digest-neutral: `params_digest` binds `[_WORD.pattern,
_WORD.flags]` (`:181`), not object identity. Consumers outside this shard
(`storyscope_atlas`, `narrative_longform_agreement:1141`,
`narrative_decision_long_form`) call `nls.count_words` and are not discoveries.

### Cohort CA: segmenter paragraph tier (one row, paragraph_splitter) (1)

| Proposed row | Family | Evidence |
|---|---|---|
| paragraph tier pattern, `_TIER_PATTERNS[3]` | paragraph_splitter | `\r?\n(?:[ \t]*\r?\n)+` (`:108`). Unit starts are separator ends (`m.end()`, `:190-192`); no strip, no filter. |

It is a distinct paragraph unit, different from every roster splitter. Probe
of `m.end()` offsets against R/AA (`\n\s*\n+`) and AB/D (`\n\s*\n`):

| Input | Tier | R / AA | AB / D |
|---|---|---|---|
| `a\n\nb`, `a\n \t \nb`, `a\n\n\nb` | same | same | same |
| `a\r\n\r\nb` | end 5 | end 5 | end 5 (tier match starts at the `\r`, R at the `\n`) |
| `a\n\f\nb` | no boundary | boundary | boundary |
| `a\n \nb` | no boundary | boundary | boundary |

A "blank" line may hold only spaces and tabs. AA also returns offsets, but
trims each span to its stripped text, so the two are not interchangeable.

Minting needs a named binding: the pattern sits unnamed in a tuple. Naming it
(for example `_PARAGRAPH_SEP`) is behavior- and digest-neutral, because
`params_digest` hashes pattern text and flags (`:180`). No R1 move is needed;
the module is already in `setec/core`. It is single-copy with one caller
(`segment_text` through `_unit_starts`), and `params_digest` already pins it in
every receipt, so a registry row adds identity only. If the pending Q8 is ruled
in, CA becomes Local.

### `narrative_decision_audit` word count joins Cohort E (2)

`_WORD_RE = re.compile(r"[A-Za-z']+")` (`:344`) and `count_words` (`:347-348`).
Docstring-stripped body hash `188a3b89152a` matches all four Cohort E copies
(`warrant_probe`, `agd_move_scan`, `fallacy_scan`, `argument_decision_audit`)
and `argquality_dimension_profile`; the `_WORD_RE` literal is the same in all
six. On six synthetic probes (`café naïve`, `İSTANBUL Kelvin-sign`, `ǅ ß ﬁ`,
digits, underscores, apostrophes) the counts matched `warrant_probe`'s. A fifth
re-export for E. The count reaches the envelope (`target.words`) and gates the
short-register warning (`:373`, `:584`, `:766`).

### Cohort CB: `calibration_survey._percentile_bounds` (one row, quantile) (2)

| Proposed row | Family | Evidence |
|---|---|---|
| `_percentile_bounds` | quantile | Returns the `n_buckets - 1` interior cut-points at `k/n_buckets`, each `s[lo]*(1-w) + s[hi]*w` (`calibration/calibration_survey.py:164-193`). Called at `:283` for length buckets. |

Docstring-stripped hash `974ce08c73a1` differs from every roster quantile body.
The API (a vector of cuts) differs from all of them. Pointwise probe on 29,990
cut-points (5,000 random integer lists, 2 to 12 buckets):

- equals BG (`voice_fingerprint._quantile`) and AQ (`validation_harness._quantile`) on all of them, since both use the same `a*(1-f)+b*f` form;
- differs from BA (`calibrate_thresholds._quantile`, `a+(b-a)*f`) on 4,077, in the last ulp (e.g. `20646.666666666668` against `...664`).

Edges differ from BG: empty input gives `[]` (BG `0.0`, AQ `None`); one value
gives floats (`[7.0, ...]`, where BG returns the element unchanged).

It could be rewritten as `[_quantile(sorted, k/n) for k ...]` over BG or AQ
with equal cut values, but that is a binding change. As a root-level
`calibration/` helper, it would need an R1 move into `setec/core/textprims.py`
before minting. It is single-copy and single-caller, so it is a Q8 candidate
too.

## Hold (1)

`calibration_survey.py:161`: `_entry_text_length` falls back to
`len(text.split())` when an entry has no non-negative `word_count`. Confirmed by
probe: with no `word_count` (or `-1`) it returned the `split()` count of the
file (5), and with `word_count=9` it returned 9. This matches shard 26's
finding (#612). Shard 26 also records that EditLens manifests fill this field
in `\w+` units while the others fall back to whitespace.

## Local (63)

- **`narrative_longform_segment` (6).**
  - `_sha` (`:50` ×2) is a bare digest of exact bytes or canonical JSON (Q1).
  - The chapter-heading (`:100`), scene-break (`:105`) and blank-line-run
    (`:107`) tier patterns fit no family. Under "Q6 parts only" they are Local.
    `blank_line_run` needs two or more blank lines; the probe shows it does not
    fire on one. So it marks whitespace section breaks, not paragraphs.
  - `_unit_starts`'s `finditer` (`:190`) is the composite's tier walk (Q6). It
    applies all four tiers, so it is also the paragraph pattern's only use site
    if CA is minted.
  - Shard 16's divergence is confirmed. The chapter tier rejects
    `CHAPTER headings are conventions of the trade` and `BOOK I read
    yesterday`, and accepts `CHAPTER I.`, `STAVE ONE` and `XIV`.
- **`narrative_decision_audit` (4).**
  - `fingerprint_prompt` (`:371`) is out of scope under Q5.
  - `:457` and `:575` are render strips.
  - `:595` is a quote-mark presence predicate for the no-dialogue register
    warning; it is not a text unit.
- **`narrative_longform_agreement` (11).**
  - `__all__` (`:315`).
  - Bare digests (Q1): `canonical_json_sha256` (`:580` ×2) and `file_sha256`
    (`:585`, `:592`).
  - Emptiness predicates on identity strings and segment content (`:869`,
    `:897`, `:1131`).
  - A manifest line strip (`:997`) and key tables (`:1022`, `:1056`).
  - It consumes `nls.content_digest` and `nls.count_words` at `:1140-1141`,
    which are not discoveries.
- **`aitdna_benchmark` (8).**
  - Label-status tables (`:100`, `:101`).
  - Emptiness predicates (`:228`, `:371`) and a JSONL line strip (`:361`).
  - CLI detector-list parsing (`:698` ×3).
- **`pan_voight_kampff_benchmark` (6).**
  - Status and baseline tables (`:91`, `:92`, `:97`).
  - CLI detector-list parsing (`:799` ×3).
- **`paraphrase_ladder` (8).**
  - A manifest line strip (`:163`) and a key table (`:509`).
  - Render strips (`:598`, `:600`).
  - `_split_csv` (`:606`, `:609` ×3) splits a comma-separated CLI argument.
- **`polarity_audit` (6).** `sig=direction` CLI parsing in
  `parse_registry_overrides` (`:836-838`) and `main` (`:878-880`).
- **`calibration_survey` (3).**
  - A bare digest of the scoring metadata JSON as a cache key (`:1128` ×2; Q1).
  - `os.replace`, an atomic file rename (`:1155`).
- **`calibration_drift_monitor` (6).**
  - A path-suffix lowercase (`:123`) and a path-separator fold (`:295`).
  - An emptiness predicate (`:302`).
  - A stack-metadata key table (`:457`).
  - Render strips (`:670`, `:815`).
  - Its metrics come from `variance_audit.audit_text`; it defines no quantile.
- **`setup_launchd` (5).** Plist template substitution (`:236`) and HH:MM
  time-window parsing (`:386`, `:391` ×2, `:394`). This is OS scheduling
  plumbing.

The benchmarks and the drift monitor define no quantile of their own.
`narrative_longform_agreement` computes an inline median of segment word counts
(`:1495-1502`), which is not a discovery.

## Cross-shard notes

- **Word units:** none new. Cohort T is `nls.count_words`. Cohort E is
  `narrative_decision_audit.count_words`. Hold is the survey's fallback.
- **Sentence splitters:** none in this shard.
- **Paragraph splitters:** one new distinct unit, CA.
- **Quantiles:** one new API, CB. Its cut values are pointwise equal to BG and
  AQ.
- **Optional-dependency branches:** none in these sites.
- **Dead code:** none. Every flagged function has a production caller.

## Method

1. Filtered the checker's JSON at `93675ba` to the ten files. That gave 71
   unresolved discoveries, matching the expected total, and confirmed the
   per-file counts above.
2. Read each site in context.
3. Imported `setec.core.narrative_longform_segment` and
   `setec.core.preprocessing` live; both import only the standard library.
4. AST-extracted `count_words`, `_WORD_RE`, `_percentile_bounds`,
   `_entry_text_length` and the roster `_quantile` bodies instead of importing
   modules that import `variance_audit`. Hashed docstring-stripped bodies.
5. All probes used synthetic strings and integer lists. `socket.connect` was
   blocked. No corpus, model or network was used.
