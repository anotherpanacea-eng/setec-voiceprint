# TP-SWEEP shard 28: artifact census, consumer client, external mirror (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/preflight/artifacts_core.py`, `setec/consumer_client.py`,
`external_mirror/ingest_outputs.py` and `external_mirror/build_prompts.py`.
This is a report only, with no source, registry or checker change. Admission is
by the owner, one cohort per PR (spec v6). The Cohort B contract is draft #588.
The owner's 2026-10-08 rulings on Q1 and Q4 to Q7 apply.

Fleet custody: fleet-coordination #480 (CAM-12, TP-SWEEP).

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`. Every discovery
for these four files is unresolved.

| File | Discoveries | Register | Consumer | Local | Open | Hold |
|---|---:|---:|---:|---:|---:|---:|
| `setec/preflight/artifacts_core.py` | 23 | 0 | 0 | 20 | 0 | 3 |
| `setec/consumer_client.py` | 21 | 0 | 0 | 21 | 0 | 0 |
| `external_mirror/ingest_outputs.py` | 23 | 1 | 0 | 22 | 0 | 0 |
| `external_mirror/build_prompts.py` | 16 | 8 | 0 | 8 | 0 | 0 |
| **Total** | **83** | **9** | **0** | **71** | **0** | **3** |

Of the 9 Register sites, 8 are two new rows (Cohorts BE and BF). The other one
joins Cohort T's `count_tokens`.

## The three questions this shard was asked

### Does `artifacts_core` use Cohort C's fingerprint?

**It consumes Cohort C's view. It does not re-spell it or hash text.**

- `detect_artifacts` runs only on `record.analysis_text` (`:349`, `:462`).
  That is the view that `common._analysis` builds (`common.py:275-280`: NFC,
  then `\r\n` and `\r` folded to `\n`) and stores in `Record`
  (`common.py:428-431`).
- The census copies `record.analysis_sha256` into its detail rows (`:353`). It
  does not recompute it.
- The module never imports or calls `unicodedata.normalize`. Its
  `unicodedata` import serves only `unidata_version` stamps (`:368`, `:391`,
  `:504`) and their checks.
- Its own hashes read no prose:
  - `DETECTOR_SHA256` (`:104-105`) is `domain_hash` over the canonical JSON of
    its own detector descriptor: pattern bytes, flags, whitespace code points
    and limits (`:77-100`). This is the module's own configuration digest, the
    same kind of thing as a Q5 prompt fingerprint. The checker raised no
    discovery for it.
  - `plain_hash(detail_bytes)` (`:392`, `:505`) hashes the census's own JSON
    output.

So the census's text policy is Cohort C's, applied upstream. The census only
reads the view.

### Does `consumer_client` normalize text passed to surfaces?

**No.** It never touches the prose that surfaces analyze.

- `run_dispatcher` (`:577-606`) builds `[surface, *args, "--json"]` and passes
  the caller's arguments through unchanged (`:591`). The target text reaches the
  surface as a file path in `args`, and the surface reads it itself.
- The only text this module handles is envelope plumbing:
  - the dispatcher's stdout, tested for emptiness (`:595`) and parsed with
    `json.loads` (`:602`);
  - SemVer strings (`:74-165`);
  - the producer's own warning strings (`:217-241`).
- The subprocess is run with `text=True` (`:574`), so stdout is decoded with
  the locale encoding. That is a decode of the JSON envelope, not a
  normalization of input text.

### `external_mirror`: templating or a reusable unit?

**Both.** Prompt templating and output parsing are Local. But `build_prompts`
puts the author's text through a preprocessor and a word tokenizer of its own
before templating. Those define the word indices for every window and the text
that `target_sha256` binds. They are Cohorts BE and BF below.

`ingest_outputs.normalize_output` cleans the model's output. That is
model-output parsing, so it is Local (details below).

## Proposed cohorts

Both modules are root-level, and both are pending P4 relocation:
- `packaging_migration_exemptions.yaml:1156-1168` lists the `build_prompts`
  path anchors.
- `setec/external_mirror/__init__.py` says the package is "Empty until P4".

So under precedent 5 neither row can be minted at its current path. The final
owner is either `setec/external_mirror/build_prompts.py` after P4, or
`setec/core/textprims.py` through an R1 move. Choosing is the builder's call.
The module is a single-file CLI with no in-tree importers. `workflow.py` runs it
as a subprocess (`workflow.py:113`). That argues for leaving the rows with the
module after P4.

### Cohort BE: `build_prompts.tokenize` (one row, tokenizer)

| Proposed row | Family | Evidence |
|---|---|---|
| `tokenize` | tokenizer | `_WORD_RE = re.compile(r"\S+")` (`:48`). `tokenize` returns a `Token(start, end)` span for each match (`:58`, `:67`). Case preserve, normalization none, backends `()`. |

`build` uses it as follows:
- tokenizes the normalized target (`:387`);
- takes `n_words = len(tokens)` as `target_word_count` (`:388`, `:437`);
- slices every window's context by word index with `slice_words` (`:406-407`,
  `:80-89`).

Window placement and the context text sent to each model both depend on it.

**Not a new boundary.** The pattern bytes and flags equal
`preprocessing.TOKEN_RE` (Cohort T). On 100,000 seeded strings (seed 28;
ASCII, tabs, VT, FF, NEL, NBSP, U+2003, U+3000, U+001C-U+001F, U+200B, U+FEFF,
CR, LF, U+2028, U+2029, CJK, em dash, `é`):
- the `tokenize` spans equalled `[m.span() for m in TOKEN_RE.finditer(s)]` with
  zero differences;
- `len(tokenize(s))` equalled `count_tokens(s)` with zero differences.

What is new is the output: offset spans, not a count. Spec §1 keeps T's
`\S+` as a hygiene unit that is "not … interchangeable with an analysis
tokenizer", so this needs its own row.

The cheaper option is to rebind `_WORD_RE` to `preprocessing.TOKEN_RE`. That
keeps the bytes and drops a duplicate pattern. But it ties prompt windowing to
the hygiene family, which spec §1 separates on purpose. That choice is for T's
and BE's builders.

**Sibling outside this shard.** `length_bootstrap.word_boundary_slice`
(`length_bootstrap.py:62-81`) is the same span idea, with its own `\S+`
pattern. On 20,000 seeded valid ranges it matched `slice_words` with zero
differences. Out-of-range input differs by design: `slice_words` raises and
`word_boundary_slice` clamps. Its shard should decide whether the two share BE.

Register: 3 (`:48`, `:58`, `:67`).

### Cohort BF: `build_prompts.normalize_text` (one row, preprocessor)

| Proposed row | Family | Evidence |
|---|---|---|
| `normalize_text` | preprocessor | `replace("\r\n", "\n").replace("\r", "\n")`, then `re.sub(r"\n{3,}", "\n\n", …)`, then `rstrip` on each line (`:70-77`). No NFC, no strip of the whole text. Case not_applicable, normalization none, backends `()`. |

`build` applies it to the raw target before tokenizing (`:385-387`). Its
output is the text that `target_sha256` hashes (`:436`). That hash is carried
into `ingested.json` (`ingest_outputs.py:527`) and the evidence pack
(`compose_evidence_pack.py:141`, `:362`).

**It is a distinct unit.** 100,000 seeded strings (seed 28) over `a`, `b`,
space, tab, LF, CR, CRLF, U+2028, NEL, NBSP and NFD `é`, compared with
`normalize_text`:

| Compared with | Strings that differ |
|---|---:|
| Cohort C view (`common._analysis`, NFC plus CR fold) | 90,300 |
| Cohort AM tail (`acquisition_core.html_to_text` spelling) | 91,236 |
| `preprocessing._collapse_whitespace` | 90,803 |
| `ingest_outputs.normalize_output`'s tail (`:94-96`) | 88,399 |
| the same tail, against `normalize_text(s).strip()` | 27,983 |
| the same, on strings with no CR | 0 |

So the ingest tail is BF plus a final `strip()`, without CR folding. On
`"a\r\n\r\n\r\nb"`, BF gives `"a\n\nb"` and the ingest tail gives
`"a\n\n\nb"`. BF also keeps NFD: `normalize_text("é")` returns
`'é'`. Its body hash (AST dump, docstring removed) is
`ae22c2b329c9`. No other production file has the line
`"\n".join(line.rstrip() for line in text.split("\n"))` except
`ingest_outputs.py:95`.

Register: 5 (`:74`×2, `:75`, `:76`×2).

**Deletion test for BE and BF.** If either drifted, a rerun on the same
target would place windows differently and change `target_sha256` and every
`context_sha256`. Nothing gates on those values; they are recorded for the
operator. Every run's `MANIFEST.json` already records `tool_sha256`, the
sha256 of this module's own bytes (`:447`). That makes any edit to either
function visible between runs, much as Q5's `--expect-fingerprint` gates do for
prompts. A row would add a characterization pin and little else. This is an
operator-driven research tool with no in-tree importers. **Admit BE and BF
late, after P4, or not at all if the owner treats `tool_sha256` as enough.**

### `ingest_outputs.count_words` (joins Cohort T)

`count_words(text)` returns `len(re.findall(r"\S+", text))` (`:106-107`). On
100,000 seeded strings it equalled `preprocessing.count_tokens` with zero
differences. It is T's `\S+` unit, spelled inline. Its name and its one
parameter name (`text`) allow a re-export of `count_tokens` under the
established name, as shard 19 described for acquisition counts. The value
becomes `normalized_word_count` (`:446`). It is recorded in `ingested.json`
and used in the `empty_output` test (`:448`). No other `external_mirror` module
reads it. Register: 1 (`:107`).

## Hold (3)

### `artifacts_core` verse-line token count

`verse_likely` counts tokens per line as `len(WS_RE.split(_trim(line)))`
(`:311`). It flags a paragraph of 3 to 40 lines when at least 4/5 of them have
1 to 12 tokens, at least 4/5 lack end punctuation, and the total is at least 12
tokens (`:312-316`). This is a whitespace word count. Its spelling is three
sites:

- `WS_RE` (`:55`), built from the explicit set `WS` (`:52-54`);
- `_trim` (`:161-162`), `str.strip(WS)`;
- the split at `:311`.

`WS` has 25 code points. `str.isspace()` has 29. The four missing are
U+001C to U+001F. On 100,000 seeded non-blank lines:
- with those four characters present, the count differed from
  `len(line.split())` on 47,493 lines;
- without them, it differed on none.

**The difference cannot reach the census.** `text_rule_violation` refuses any
C0 control other than tab, LF and CR, and every C1 control
(`common.py:266-269`), before `_analysis` builds the view (`:276-277`). So
on every text the preflight intake admits, this count equals
`len(line.split())` and Cohort T's `\S+` count. It is not a new distinct word
unit for the census path, only a new spelling.

The `WS` set and `WS_RE` bytes are already in `DETECTOR_DESCRIPTOR`
(`:95`, `:97-98`), and so in `DETECTOR_SHA256`. Every receipt carries that
digest, and `tests/test_preflight_artifacts.py:91` and `:449-458` check it.
That pins the whitespace definition, but not the split-and-count code. When the
word-count family shard takes this up, it should weigh that existing pin before
adding a row.

`_trim` also decides blank lines for paragraph numbering (`:207`) and builds
detector keys (`:253`, `:256`). Those uses carry the same label as the
definition.

## Consumer (0)

`artifacts_core` does use other modules' objects:
- `preprocessing.PREPROCESSING_RULES`' `html_tag` pattern (`:56-57`);
- `preprocessing.CSS_AT_RE` (`:79`, `:228`);
- `preprocessing.is_css_rule_block` (`:237`).

The checker raised none of them as discoveries here, so none is counted.

## Local (71)

### `setec/preflight/artifacts_core.py` (20)

- **Enum tables (4):** `CATEGORIES` (`:29`), `ARTIFACT_TYPES` (`:30`),
  `DISPOSITIONS` (`:40`) and `VARIATION_CLASSES` (`:43`). They name artifact
  types, policy outcomes and calibration labels.
- **Artifact detectors (13).** These are matched against lines of the Cohort
  C view to emit observations. None changes the text. They are the census's
  own analytic rules, the same ground as "Q4 ruled local":
  - `SCRIPT_OPEN`, `SCRIPT_CLOSE` and `SCRIPT_EVENT` (`:58-60`);
  - `MARKDOWN` (`:61`), `TEI` (`:63`) and `FOOTNOTE` (`:64`);
  - `PAGE` (`:65`), `NUMBER` (`:66`), `OCR_END` and `OCR_START` (`:67-68`);
  - `FENCE` (`:69`) and `ASCII_LETTER` (`:70`);
  - `_PRIVATE_USE` (`:154`).

  Their bytes are pinned by `DETECTOR_SHA256`.
- **Detector key and coordinate helpers (3):**
  - `_collapse` (`:166`) maps whitespace runs to one space. It builds the
    running-head key and the navigation lookup key (`:254`, `:264`, `:283`).
  - The `split("\n")` at `:200` breaks the already-folded view into lines.
    The census reports observations by line number and paragraph number
    (`:317`). Paragraphs are maximal runs of lines that are not blank after
    `_trim` (`:205-215`). Under "Q6 parts only", the census itself (line and
    paragraph numbering plus the detectors) gets no row. Its whitespace part is
    the Hold above. The paragraph numbering exists only inline, so it has no
    symbol to register.

    On 20,000 seeded multi-paragraph texts, its paragraph count differed from
    `len(stylometry_core.paragraphs(text))` (Cohort R) on 5,290. All of them
    used a U+001C to U+001F separator line. With those separators excluded
    there were zero differences, and the intake rejects those characters.
  - The fence info-string emptiness test `suffix.strip(" \t")` (`:291`).

### `setec/consumer_client.py` (21)

- **SemVer grammar (7):** `_NUMERIC_RE` and `_IDENTIFIER_RE` (`:74-75`,
  `re.ASCII`), and the five `split` calls in `parse_version` (`:127`, `:135`,
  `:138`, `:143`, `:162`).
- **Warning classifier patterns (11):** `RELIABILITY_PATTERNS` (`:218-228`).
  They match the producer's own warning strings in a success envelope, not
  author prose. A probe confirmed that `classify_warning` returns
  `"reliability"` whether or not a pattern matches (`:238-241`), so the
  patterns don't change the outcome (see "Outside the sweep").
- **Envelope key tables (2):** `_REQUIRED_ENVELOPE_KEYS` (`:277`) and
  `_ERROR_ENVELOPE_KEYS` (`:282`).
- **Emptiness predicate (1):** `completed.stdout.strip()` in `run_dispatcher`
  (`:595`).

### `external_mirror/ingest_outputs.py` (22)

- **Model-output parsing patterns (11):**
  - `_PREAMBLE_PATTERNS` (`:35-38`);
  - `_TRAILING_COMMENTARY_PATTERNS` (`:42-43`);
  - `_REFUSAL_PATTERNS` (`:47-50`);
  - `_CODE_FENCE_RE` (`:53`).

  They recognize chatbot preambles, sign-offs, refusals and code fences in
  pasted-back model output.
- **Paste-back filename (1):** `_WINDOW_FILE_RE` (`:132`).
- **`normalize_output` (6):**
  - `:66` (strip before the fence match);
  - `:78` (quote-wrapper test);
  - the whitespace tail: `:94` (`\n{3,}`), `:95` (`split` and `rstrip`) and
    `:96` (`strip`).

  This is model-output parsing. The text is a model's continuation, cleaned so
  that `compute_distances` can compare it with the operator's target
  continuation (`compute_distances.py:353-358`). Every stripping step except
  the whitespace tail is logged in `normalization_actions`. The tail is not
  BF (see BF's table), so labeling it Local does not split a registered unit.
  On 20,000 strings that triggered no action, the live `normalize_output`
  equalled a hand-copied tail with zero differences.
- **`detect_refusal` (1):** `text[:200].strip()` (`:102`).
- **T4 batched JSON parsing (2):** `parse_t4_batched` strips the file and the
  fence body before `json.loads` (`:353`, `:357`).
- **Emptiness predicate (1):** `_build_record`'s `normalized.strip()`
  (`:448`).

### `external_mirror/build_prompts.py` (8)

- **Bare digests, Q1 (4):**
  - `sha256_hex` (`:344`: `sha256` and `hexdigest`) hashes
    `s.encode("utf-8")` and reads no global. Its text policy is its callers'
    (BF and BE, at `:409` and `:436`), so it is out of scope under the Q1
    ruling.
  - `sha256_file` (`:348`: `sha256` and `hexdigest`) hashes the tool's own
    file bytes (`:447`).
- **Git metadata (1):** `git_head_sha`'s `stdout.strip()` (`:337`).
- **Command-line parsing (3):** `_parse_int_list` (`:463`, `split` and two
  `strip`) parses `--positions` and `--context-grid`.

## Word counts and splitters: what this shard adds

| Unit | Where | Disposition |
|---|---|---|
| `\S+` count | `ingest_outputs.count_words` | joins Cohort T; no new unit |
| `\S+` offset spans | `build_prompts.tokenize` | Cohort BE; same boundaries as T, span output |
| explicit-`WS` per-line count | `artifacts_core:311` | Hold; equals `str.split()` on every intake-admitted text |

No sentence splitter is defined in any of the four files. The census's inline
paragraph numbering equals Cohort R's `paragraphs` count on admissible text.
It has no symbol, so it is Local under "Q6 parts only".

## Outside the sweep (recorded, not dispositioned)

- **`classify_warning`'s patterns don't change its result.** Both branches
  return `"reliability"` (`consumer_client.py:238-241`). Its docstring says
  this is deliberate: unmatched warnings fail upward. So the 11 patterns change
  nothing a consumer sees. The fixtures `warning_classifier_coverage.json`
  and `warning_producer_emissions.json` pin them (`:211-215`). Deleting
  the loop would change no output. It would leave the fixtures and producer
  firewall tests pinning strings that have no effect. This client is vendored
  byte-identically into two consumer repos (`:11-14`), so a cut would cross
  repos. I raise it only as a deletion-test observation, with no
  recommendation inside this sweep.
- **Mirror target and output are cleaned differently.** The operator's target
  continuation gets only `.strip()` (`compute_distances.py:103`). Model
  outputs get the ingest whitespace tail. For whitespace-insensitive metrics
  such as word sets and embeddings, this is unlikely to matter. I did not
  test the distances.

## Questions for the owner

None new. Q1, Q4, Q5 (by analogy, for `DETECTOR_SHA256` and `tool_sha256`) and
Q6 are applied as ruled. BE and BF need an owner choice after P4, as stated
above.

## Not verified

- I did not run `census`, `calibrate` or `run_dispatcher`. The probes called
  `detect_artifacts`, `_analysis`, `classify_warning`, the `build_prompts`
  helpers and `normalize_output` directly.
- The paragraph-count comparison used a fixed set of eight separator lines. It
  is not a proof over all line shapes.
- I did not count how many of the checker's 3,442 unresolved discoveries
  remain unreviewed across all shards.

## Method

1. Filtered the checker's JSON at `93675ba` to the four files: 23, 21, 23 and
   16 unresolved, 83 in all, with no other outcome.
2. Read each site in context, the importers (`p7_report`, `workflow`,
   `compute_distances`, `compose_evidence_pack`) and the preflight intake
   (`common.text_rule_violation`, `_analysis`).
3. Ran probes from the worktree root with Python 3.13, with
   `plugins/setec-voiceprint/scripts` on `sys.path` and `socket.connect`
   blocked. The `external_mirror` modules were loaded by file path. All
   imports completed offline. Every input was a synthetic string with seed 28.
   No corpus, output file or manifest was read, and no model or network call
   was made.
