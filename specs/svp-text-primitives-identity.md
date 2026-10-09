# SVP Text-Primitives Registry — one owner per primitive

**Status:** BUILT (v7) · **Date:** 2026-10-09 · **Repo:** `setec-voiceprint`
**v7 (owner direction, 2026-10-09):** simplified to consolidate + characterize + a copy lint. Earlier versions (v5, 2026-08-05; v6, 2026-10-04) minted source-hash IDs per row, required merge-base byte equality and per-cohort contracts, and gated on a 3,442-site discovery inventory. Nothing outside that checker read the IDs, and the characterization oracle already pins behavior, so v7 drops that machinery. The text of v5 and v6 is in this file's history.

## Outcome

Voiceprint audits share basic text primitives: word counters, tokenizers, sentence and paragraph splitters, function-word sets, whitespace normalizers and fingerprints. When several modules carry their own copy, the copies drift, and two audits disagree for reasons unrelated to the prose. This spec keeps exactly one owner for each shared primitive, pins each primitive's behavior with synthetic cases, and refuses new copies.

It changes no primitive's output and adds no output-envelope field, stamp, policy state, consumer schema change or recalibration artifact.

## 1. One owner, one import point

`plugins/setec-voiceprint/scripts/setec/core/textprims.py` holds `PRIMITIVES`, an immutable map from each registered name to its owner module. A primitive used by several modules either already has a single owner, which stays in place (`passage_tokenizer_v1.tokenize`, `verbatim_cover._tokens` and `_content_fingerprint`, `paragraph_parser.split_paragraphs` and `split_sentences`, `preflight.common._analysis`), or moves into `textprims.py` byte-for-byte and the old modules import it under their established names (the function-word sets, the two sentence splitters, `_normws`, `count_words_alpha`).

Every registered name is importable from `setec.core.textprims`. Names owned elsewhere resolve lazily through the module's `__getattr__`, so importing the registry loads none of their owner modules (no plugin data, model stack or preflight package).

A moved function keeps its body. When a registry name has to differ from the original (for example `count_words_alpha`, chosen because many unrelated functions are called `count_words`), consumers import it under their old name: `from setec.core.textprims import count_words_alpha as count_words`.

Registering a new primitive means adding its `PRIMITIVES` entry, consolidating its copies, and adding characterization rows, all in one PR, with no separate contract amendment.

## 2. Characterization

`plugins/setec-voiceprint/references/textprims/characterization.json` (schema `textprims-characterization/2`) is a pure-call oracle. Each row is exactly:

```text
case_id: unique string
callable: a PRIMITIVES name, or "<word-set name>.__contains__"
args, kwargs: JSON passed to the call (for `_analysis`, each arg is {"hex": "..."} decoded to bytes)
result_path: selectors applied to the return value
comparator: json_exact | sequence_exact | set_exact | bytes_hex_exact | exception_exact
expected: comparator-specific JSON value
mutant: {args, kwargs, result_path, expected}
```

`tools/run_textprims_characterization.py` resolves each callable through `setec.core.textprims`, proves the primary case and the mutant both match, and refuses a mutant whose expectation equals the primary's. Every `PRIMITIVES` name needs at least one row. The runner checks every expectation against a live call. Rows cover the inputs where a primitive's behavior is easy to get wrong: empty input, digits, hyphens, straight and curly apostrophes, non-ASCII letters, NFC versus NFD forms, abbreviations, ellipses, and paragraph or line boundaries where they apply.

Native Punkt rows read only NLTK 3.9.4 and the four English `punkt_tab` files from `nltk_data` revision `550b6625bcef1f2abff2ff770a5a0d272c9c6b2a`, provisioned by `tools/prepare_punkt_characterization.py` into an isolated root. Characterization never downloads and fails rather than skips when the data is missing.

`set_exact` compares live string sets (or frozensets) against a JSON list of unique strings, ignoring order while preserving exact membership and case. Duplicate expectations and non-string members are refused.

## 3. Copy lint

`tools/gen_textprims_inventory.py --check` (the existing CI step) fails when a function anywhere in the plugin's production source has the same body and parameters as a registered primitive, up to renaming the parameters (ignoring its name, annotations and docstring), and reads module-level names with the same values. It also fails on a word-set literal equal to a registered set, and on a `PRIMITIVES` entry whose owner module does not define it. Same-named functions with different code are unrelated and are not reported. The fix for a reported copy is to import the registered primitive.

## Out of scope

- Changing any primitive's behavior. A behavior change is a separate, reviewed change that updates the characterization rows it moves.
- Reconciling the remaining same-purpose primitives that are not byte-identical (for example the several sentence splitters that disagree on `"one. two"`). Each is a behavior decision for its own PR.
- Envelope stamps, provenance fields, or IDs for primitives.
