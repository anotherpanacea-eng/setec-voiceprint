# TP-SWEEP shard 8: `setec/core/preprocessing.py` (2026-10-08)

Independent review of the 64 unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `plugins/setec-voiceprint/scripts/setec/core/preprocessing.py`. This is a
report only, with no source, registry or checker change. Admission is by the
owner, one cohort per PR (spec v6). Earlier shards are drafts #584 to #587 and
#589 to #591. The Cohort B contract is draft #588.

Fleet custody: fleet-coordination #449 (CAM-12, TP-SWEEP).

## Fold

| Disposition | Count |
|---|---:|
| Register: bound by the proposed `strip_non_prose` row | 51 |
| Register: the `count_tokens` row | 3 |
| Local: rule-option parsing and metadata | 10 |
| **Total** | **64** |

After shards 1 to 8, 2,826 of the checker's 3,442 unresolved discoveries remain
unreviewed.

## Context

Spec §1 already settles ownership. "`preprocessing.py` owns prose
transformations and its `r"\S+"` corpus-hygiene unit. Preprocessing is a
separate family; its token count is not treated as interchangeable with an
analysis tokenizer." The module is already in `setec/core`, so nothing moves.

`strip_non_prose` (`:769`) is the module's only public transform, and 18
production modules import from this module. Earlier shards refer to it in three
ways:

- Their bare `sha256(cleaned_text)` fingerprints get their text policy from it.
  The owner's Q1 ruling puts those fingerprints out of scope.
- It is the consumer target for many surfaces.
- `voice_distance` and `idiolect_detector` depend on its cleaned output for
  self-exclusion.

## Proposed Cohort T (two rows)

### `strip_non_prose`: preprocessor row bound to the whole module

The function's behavior combines:

- 17 strip-rule patterns (`:26-233`): front matter, HTML comments and blocks,
  code fences, inline code, tags, headings, blockquotes, long quotes, statute
  citations, assistant boilerplate, prompt echoes, images, links, autolinks,
  bare URLs, footnotes and author-year citations;
- the CSS and structured-block detectors: patterns at `:393-427` and
  `:481-500`, plus the `_HTML_TAG_NAMES` and `_CSS_PROPERTY_NAMES` tables built
  by module-level splits at `:433` and `:450`;
- the rule appliers (`:350-386`), the strip recorder (`:333-346`), the CSS
  predicates (`:522-606`) and the whitespace collapse (`:721-723`).

Every top-level definition in the module serves this transform or its options.
So the binding-list approach used for Cohort B (#588) would have to list nearly
the whole file.

Proposal: bind the whole canonical module, as the frozen passage tokenizer did
(spec §2). Any edit to `preprocessing.py` then requires a new versioned ID. That
matches §4's "calibrated and hash-bound primitives remain behavior-pinned":
cleaned text feeds calibrated surfaces, and changing one rule changes their
inputs.

Characterization must pass `strip_rules`, `allow_non_prose` and
`strip_aggressive` explicitly. §3 forbids omitted default arguments. It should
cover at least front matter, a fenced code block, a CSS block, a long quotation,
a bare URL, and a prose paragraph that must survive unchanged.

Register: 51.

### `count_tokens`: the `\S+` hygiene unit

`count_tokens(text)` returns `len(TOKEN_RE.findall(text))`, with
`TOKEN_RE = re.compile(r"\S+")` (`:20`, `:248-249`). It sizes stripped
material in the metadata (`:341`) and the before and after counts (`:792`,
`:874`). Spec §1 keeps it distinct from analysis tokenizers.

Proposed: its own row. The builder has to choose the family: `preprocessor`,
since spec §1 places it with preprocessing, or `tokenizer`, since it counts
units. This report suggests `preprocessor`. Under the whole-module binding
above, an edit anywhere in the module also re-mints this row. A narrower
binding list (the function plus `TOKEN_RE`) would avoid that. Register: 3.

## Local (10)

These parse rule options and aggregate metadata rather than transform prose. A
whole-module digest binds them anyway.

- `parse_rule_names` (`:257`×2, `:259`): comma-separated rule names.
- `resolve_masking_rules` (`:305`, `:311`×3, `:313`×2): masking profiles and
  rule lists.
- `aggregate_preprocessing_metadata` (`:726`): flagged for its name only.

## Method

1. Ran the checker at `93675ba` and filtered to the module.
2. Listed the module's top-level definitions and the call graph from
   `strip_non_prose`.
3. Counted its importers by repository grep. No model call.
