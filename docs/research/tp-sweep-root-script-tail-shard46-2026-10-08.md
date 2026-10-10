# TP-SWEEP shard 46: root-script tail (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for fifteen root-level scripts and one test-fixture generator. This is a report
only, with no source, registry or checker change.

Fleet custody: fleet-coordination #501 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site is left Open. No site registers, so no cohort is proposed;
the reserved letters CM and CN are unused.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `adversarial_robustness_card.py` | 6 | 0 | 0 | 0 | 6 | 0 |
| `confounder_audit.py` | 5 | 0 | 0 | 0 | 5 | 0 |
| `aesthetic_authority_audit.py` | 4 | 0 | 0 | 4 | 0 | 0 |
| `evidentiary_conditions_gate.py` | 4 | 0 | 0 | 1 | 3 | 0 |
| `prestige_metaphor.py` | 4 | 0 | 0 | 2 | 2 | 0 |
| `bigram_diff.py` | 3 | 0 | 0 | 0 | 3 | 0 |
| `fetch_brysbaert.py` | 3 | 0 | 0 | 0 | 3 | 0 |
| `gecscore_audit.py` | 3 | 0 | 0 | 0 | 3 | 0 |
| `voice_profile.py` | 3 | 0 | 1 | 0 | 2 | 0 |
| `test_data/pdf_inventory_fixture/_make_fixtures.py` | 3 | 0 | 0 | 0 | 3 | 0 |
| `acquire_corpus_template.py` | 2 | 0 | 0 | 0 | 2 | 0 |
| `baseline_discovery.py` | 2 | 0 | 0 | 0 | 2 | 0 |
| `known_editor_profile.py` | 2 | 0 | 0 | 0 | 2 | 0 |
| `explain.py` | 1 | 0 | 0 | 0 | 1 | 0 |
| `gen_contract_fixtures.py` | 1 | 0 | 0 | 0 | 1 | 0 |
| `manuscript_bigram_diff.py` | 1 | 0 | 0 | 0 | 1 | 0 |
| **Total** | **47** | **0** | **1** | **7** | **39** | **0** |

All 47 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## The bigram scripts have no word tokenizer

`bigram_diff.py` and `manuscript_bigram_diff.py` diff **POS** bigrams, not word
bigrams (module docstrings, `bigram_diff.py:4`, `manuscript_bigram_diff.py:4`).
Neither defines a regex or whitespace word unit, so neither places against
R, S, E, BI, K, N, AO or T. The unit is spaCy's: `variance_audit.pos_bigram_distribution`
(`variance_audit.py:336-360`) runs `_NLP(text)`, walks `doc.sents`, drops
`is_space` tokens, and keys adjacent pairs `f"{a.pos_}-{b.pos_}"` within each
sentence.

- **Shared code.** `manuscript_bigram_diff` imports `list_cluster_paths`,
  `parse_cluster_files`, `aggregate_cluster_pooled` and `aggregate_cluster_mean`
  from `bigram_diff` (`manuscript_bigram_diff.py:56-61`), so both reach the same
  tagging path. Each keeps its own `render_table`; docstring-stripped AST
  hashes differ (`d50c603902e6` against `6617d655d21e`) because the signatures
  differ (one examples map against two, plus corpus labels).
- **Inline copy of the key construction.** `bigram_diff._collect_examples`
  (`:97-113`) re-tags the text (`:105`) and rebuilds the same per-sentence,
  non-space POS-pair keys to collect example token pairs. Its body hash
  (`930c07e7d7d9`) differs from `pos_bigram_distribution`'s (`29a1957ea3bf`),
  since one counts and the other collects strings. Both read only `HAS_SPACY`
  and `_NLP` as globals. Probe: with a stub `_NLP` returning synthetic
  documents (embedded and sentence-initial space tokens, a one-token sentence,
  a five-token run), the per-key example counts from `_collect_examples`
  equaled `pos_bigram_distribution`'s counts on all three. Neither function is
  a discovery in this shard. Under Q7 (admit, `("spacy",)` backend, spec
  amendment pending), `pos_bigram_distribution` belongs to the
  `variance_audit` shard; if it is minted, `_collect_examples` is an inline
  second spelling of its unit, and a root-level one (precedent 5). The
  cheaper fix is deletion: have one tagging pass return counts and examples
  together. That also removes the second `_NLP` call per file.

All four bigram-script discoveries are Local (see below).

## Hold (7)

- `aesthetic_authority_audit.py:531` ×2 (`build_audit_payload` fallback when
  `total_tokens` is absent) and `:575` ×2 (`build_unavailable_payload`).
- `prestige_metaphor.py:673` ×2 (`build_unavailable_payload`).
- `evidentiary_conditions_gate.py:124` (`_read_target_length`, when target text
  is present; otherwise it reads `n_words` from upstream envelopes).

The first three spell the count `sum(1 for w in text.split() if w.strip())`.
The `w.strip()` filter is a no-op: over all 1,114,112 code points, `str.split()`
and `str.strip()` agree on whitespace (0 differences). Live probe on 14
synthetic strings (empty, NBSP, U+2003, U+200B, `\x1c` to `\x1f`, `\x85`,
`\f\v`, U+3000, CRLF, U+2028/U+2029, a combining accent, padded text): all four
builders' `target.words` and `_read_target_length` equaled `len(text.split())`
on every one. The checker splits each generator into a `.split` and a `.strip`
site; both are Hold. All seven feed `target.words` in the envelope.

## Consumer (1)

`voice_profile.py:303` calls `preprocessing.strip_non_prose("", ...)` to
validate `--strip-rules` before loading. Probe: on `""` it returns `""` with
metadata, and an unknown rule raises `ValueError`, which `main` turns into a
parser error.

## Local (39)

- **Render and claim-license strips (10):** `adversarial_robustness_card.py:412`,
  `:550`; `confounder_audit.py:798`, `:921`; `evidentiary_conditions_gate.py:764`,
  `:853`; `explain.py:136`; `gecscore_audit.py:629`; `known_editor_profile.py:485`,
  `:590`.
- **Markdown cell and table rewrites (4):** `voice_profile.py:42` ×2 (`md_cell`);
  `bigram_diff.py:301` and `manuscript_bigram_diff.py:132` (`"-"` to `"+"` in
  the displayed POS-bigram key).
- **Key and header tables (6):** `adversarial_robustness_card.py:423`, `:487`;
  `evidentiary_conditions_gate.py:775`; `confounder_audit.py:354` (signal-name
  set); `bigram_diff.py:296`; `baseline_discovery.py:70` (`MANIFEST_NAMES`).
- **CLI and manifest parsing (5):** `adversarial_robustness_card.py:563`, `:564`
  (`LABEL:PATH`); `baseline_discovery.py:200` (non-blank manifest lines);
  `gecscore_audit.py:646` (path suffix), `:649` (JSONL line).
- **Path filter (1):** `bigram_diff.py:77` (README filename test).
- **Lexicon matches, Q4 ruled local (3):** `confounder_audit.py:272`, `:285`
  (case-insensitive substring match of idiolect preservation phrases);
  `prestige_metaphor.py:231` (lowercased lookup in `PRESTIGE_DOMAIN_VOCAB` and
  user domains).
- **Library-output parsing (1):** `prestige_metaphor.py:276` takes the lemma from
  a WordNet synset name (`name().split(".")[0]`).
- **Acquisition length gate (2):** `acquire_corpus_template.py:239`, `:254`
  skip a body under 200 characters after `strip()`, before and after
  `ac.preprocess_text`. These are character counts, not a word unit. The same
  gate appears on 18 lines in 9 production acquirers.
- **Fetcher (3):** `fetch_brysbaert.py:52` (`_OUT_HEADER`), `:210` (expected XLSX
  header), `:274` (`os.replace`, an atomic rename). Its only key normalization
  is a call to `concreteness.rating_key` (`:251`), which is not a discovery.
- **Golden-envelope builder (1):** `gen_contract_fixtures.py:653`
  (`_build_voice_fingerprint`) was flagged for its name. It assembles a fixed
  `voice_fingerprint` envelope (registered at `:1092`) and transforms no text.
- **Test-fixture generator (3):** `_make_fixtures.py:66` (`SYNTHETIC_PARAGRAPHS`,
  synthetic prose) and `:122-123` (greedy 80-character word wrap for PDF
  layout). It is a dev-time `reportlab` script; only its committed PDFs are used,
  by `tests/test_pdf_inventory_extract.py`. No production caller.

## Notes for the closing roll-up

- **`acquire_corpus_template.py`.** It has no inline T, AF or AM spelling. HTML
  goes through `ac.html_to_text` (AM, named in the `extract_one` docstring at
  `:173`), cleaning through `ac.preprocess_text` (`:248`), and the word count and
  content hash come from `ac.AcquiredPiece` (`:266`, `:284`, `:301`). The only
  inline text rule a new acquirer copies is the 200-character gate above.
- **`gecscore_audit.py`.** It imports Cohort R's `word_tokens`
  (`:96`) and calls it at `:392`, `:739` and `:848`. These are Consumer uses,
  but none is a discovery here.
- **Word units:** none new. Hold covers seven whitespace counts.
- **Sentence and paragraph splitters, quantiles, fingerprints:** none.
- **Optional-dependency branches:** `prestige_metaphor`'s WordNet fallback
  changes domain classification when NLTK and WordNet data are installed; the
  output records `wordnet_used` (`:441`), though not the WordNet version. The
  bigram scripts require spaCy and exit when it is absent.
- **Dead code:** none among the flagged functions; each has a caller.
  `_collect_examples` duplicates work (see above).

## Method

1. Filtered the checker's JSON at `93675ba` to the sixteen files: 47 unresolved
   discoveries, matching the expected total and the per-file counts above.
2. Read each site in context, with callers.
3. Probes used synthetic strings only, with `socket.connect` blocked and an
   import guard refusing `variance_audit`, spaCy and torch. The Hold probe
   imported `evidentiary_conditions_gate`, `aesthetic_authority_audit` and
   `prestige_metaphor` live (none loads `variance_audit`). The bigram functions
   were AST-extracted, docstring-stripped and hashed, then executed against a
   stub `_NLP`; no spaCy model was loaded. The `strip_non_prose` probe imported
   `preprocessing` live.

## Not verified

- The spaCy path itself. The stub shows the two functions build the same keys
  from the same token stream; it does not exercise a real tagger.
