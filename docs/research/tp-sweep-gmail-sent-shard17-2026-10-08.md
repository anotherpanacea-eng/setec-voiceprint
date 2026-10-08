# TP-SWEEP shard 17: Gmail sent-mail acquisition (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/acquire_gmail_sent.py`. This is a report only, with no
source, registry or checker change. Admission is by the owner, one cohort per
PR (spec v6). Earlier shards are drafts #584 to #600. The Cohort B contract is
draft #588.

Fleet custody: fleet-coordination #467 (CAM-12, TP-SWEEP).

Labels follow the owner's later 2026-10-08 rulings: Q1 bare digests are Local,
Q4 lexical lists are Local, Q6 composites are Local with their parts
registered, and Q7 spaCy-backed primitives are admissible. No site in this
shard is left Open.

The module acquires the owner's sent mail. This review read code only. It did
not run the acquisition, read a mailbox, token or corpus file, or call a
network service. Every probe used synthetic strings against imported pure
functions, with sockets disabled in the probe process.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/surfaces/acquire_gmail_sent.py` | 94 | 3 | 0 | 0 | 91 | 0 |

All 94 discoveries for this file are unresolved; the checker reports no other
outcome for it. Shards 18 and 19 run in parallel with this one, so this report
gives no combined remainder.

## What the module does to prose

The cleaning path for one message is `process_message` (`:1797`):

1. MIME decode with newline folding (`_decode_part`, `:306-318`).
2. RFC 3676 `format=flowed` unwrapping (`_unwrap_flowed_details`, `:643-695`),
   or the HTML fallback through `ac.html_to_text` (`:724`).
3. Forward, quote, attribution, signature and promo-footer trimming
   (`trim_body`, `:871-933`, built on `_semantic_line_classes`, `:391-610`).
4. A residual-attribution backstop (`:1859-1864`).
5. `ac.preprocess_text` (`:1866`), which is `acquisition_core.py:715`'s wrapper
   around `preprocessing.strip_non_prose` (Cohort T).
6. Address redaction (`_redact_addresses`, `:995-997`) and the `\S+`
   minimum-words gate (`:1872`).

Steps 1 and 6 hold the only text units that fit a family. Steps 2 to 4 are
email-transport segmentation. Step 5 reaches Cohort T only through
`acquisition_core`, so it is not a discovery here.

**Nothing in this module is shared.** No production module imports its
functions. `setec/surfaces/gmail_author_pipeline.py:278` runs it as a
subprocess, and `gmail_locator_map.py` reads only its sidecars (`:3-5`).
Hashing every function body with the docstring removed (`ast.dump`) across
`acquisition_core.py` and the other nine `setec/surfaces/acquire_*.py`
modules matched none of this module's 74 function definitions to any
definition elsewhere. Its marker
patterns (`:57-75`, `:825`) occur in no other production file
(`grep -F` on the pattern text).

## Register (3)

No new cohort is proposed; letters AI and AJ are unused. Both units join
cohorts already named. Under the Cohort B contract (#588), both are inline
spellings, not legacy sites. They stay unresolved candidates, counted here as
register-bound.

### Cohort AF: newline canonicalization (2)

`_decode_part` returns `decoded.replace("\r\n", "\n").replace("\r", "\n")`
(`:318`, 2 sites). Shard 15 (#600) already lists this as one of AF's bare-chain
spellings. Probe: over 100,000 seeded synthetic strings (CR, CRLF, LS, NEL,
NBSP, combining marks), `_decode_part` on a synthetic `text/plain` part equals
`setec/calibration/storyscope_atlas.py:155-156` `_normalize_newlines` on the
same string. Zero differences. `"a\r\r\nb\rc"` gives `"a\n\nb\nc"`.

### Cohort T: the `\S+` hygiene count (1)

`len(re.findall(r"\S+", cleaned)) < opts.min_words` (`:1872`) gates each piece
on `--min-words-per-piece` (default 40, `:50`). The pattern bytes equal
`preprocessing.TOKEN_RE` (`setec/core/preprocessing.py:20`), and the expression
is `count_tokens`'s body spelled inline. Probe: the AST-extracted `:1872` call,
evaluated over 100,000 seeded strings, equals `preprocessing.count_tokens` and
`len(s.split())`. Zero differences in both. This file adds no new word-count
unit.

If the owner instead folds `\S+` counts into the held whitespace family (shard
15 showed the two have the same boundaries), this site moves to Hold. The same
inline spelling appears at 14 other production sites, all belonging to other
shards:

- `acquisition_core.py:778` (`AcquiredPiece.word_count`, shard 19);
- `setec/surfaces/acquire_imessage_sent.py:630` (shard 18);
- `setec/surfaces/acquire_everycrsreport.py:349` and `:848` (shard 19);
- `setec/surfaces/acquire_courtlistener.py:400`,
  `acquire_govinfo_chrg.py:579`, `acquire_pdf_urls.py:266`,
  `acquire_openalex_core.py:305` and `acquire_mirrulations.py:651`;
- `acquire_epub.py:436`, `acquire_manuscript.py:317`,
  `acquire_blogger_takeout.py:223`, `pdf_inventory.py:368` and
  `external_mirror/ingest_outputs.py:107`.

### Constraint for the AF and T builders: the file byte-pins itself

`_extraction_code_sha256` (`:1041-1043`) hashes this file's bytes. The hash is
bound into:

- the live-smoke receipt (`:1172`), checked by the unwindowed-write gate
  (`:1127-1152`);
- the public behavior params (`:2268`), so also the behavior fingerprint and
  the resume fingerprint (`:2275`, `:2287-2294`), which `verify-acquisition`
  checks (`:2559-2562`).

So making `:318` or `:1872` import a registered object would change the hash.
Existing receipts would stop validating, and the operator would need a fresh
windowed smoke and `approve-smoke` before the next full write. That is
recoverable, but the operator would see it. The Cohort B contract doesn't
require these inline spellings to adopt anything. **Recommendation:** leave
both inline when AF and T are minted. Adopt only when the owner is re-smoking
Gmail anyway.

A related fact, not a finding against this shard: the pin covers only this
file. It does not cover `acquisition_core.html_to_text` or
`preprocessing.strip_non_prose`, which also shape the committed text. Cohort T's
whole-module row is what would give `strip_non_prose` an identity.

## Local (91)

### Q6 parts only: quote, forward and signature trimming (30)

The trimmer splits a body into authored, quoted, attribution, forwarded,
signature and promo-footer regions, then keeps the authored region. That is a
segmentation composite of the quote-region kind that shard 15 labeled Q6. Its
parts are marker patterns and physical-line splits, and none fits a family:

- marker patterns: `:57`, `:58`, `:60`, `:61` (attribution, original-message
  and forward lines), `:68`, `:69` (their flowed-span forms), and the Zillow
  share-footer pattern `:825`;
- `_semantic_line_classes`: `:408`, `:458`, `:498`, `:530`, `:571`, `:597`;
- `_rendered_semantic_lines` `:634`, `_has_weak_quote_signal` `:808`,
  `_prefix_provenance` `:868`;
- `_strip_promo_footer` `:840` and `_trim_signature` `:850`, `:853`, `:855`,
  `:857`, `:858`, `:859`;
- `trim_body` `:880`, `:887`, `:888`, `:900`, `:911`;
- `process_message`'s forward-marker and backstop line splits `:1833`, `:1860`.

**Why this is not a preprocessor row, unlike shard 16's `strip_gutenberg`.**
The trimmer's output is not a function of the text alone. Probes:

- `trim_body("Thanks for the note.\nsomeone wrote:\nquoted text", ...)` drops
  the message (`quote_boundary_unresolved`) with `is_reply=True` and keeps it
  whole with `is_reply=False`. `is_reply` comes from the `In-Reply-To` and
  `References` headers (`:1831`).
- `own_sig_lines`, which is operator config, decides whether a trailing
  `"J. Doe\nExample Org"` survives.
- The same rendered text `">not a quote\nnext"` is kept whole with its RFC 3676
  provenance (a space-stuffed authored `>`), and trimmed to `""` without it.
  That provenance is a tuple of dataclasses, which §3's JSON `args` cannot
  express.

**Deletion test.** If this stays unregistered, nothing can drift: no other
module has a copy. The behavior is already pinned by the file hash above, by
`EXTRACTION_POLICY_VERSION` (`:1027`), and by
`tests/test_acquire_gmail_sent.py`. A row would add a characterization oracle
and no new protection.

Only `_strip_promo_footer` (`:838-840`) is a pure text-to-text cut, like
`strip_gutenberg`. It is one pattern, called only from `_trim_signature`
(`:848`), so it is counted as a part of the composite. Q4's ruling gives the same
label if the marker patterns are read as lexicons.

### MIME transport and header parsing (16)

- RFC 3676 unwrapping: `:652`. This is transport decoding, keyed to MIME
  `format`/`delsp` params (`:715`, `:716`).
- Body-part selection: the disposition test `:742` and the emptiness predicates
  `:719`, `:723`.
- The HTML fallback's selector table `_HTML_STRIP_SELECTORS` (`:73`). These are
  DOM selectors passed to `ac.html_to_text` (`acquisition_core.py:1148`); they
  are not matched against prose. The HTML-to-text transform belongs to shard 19.
- Header parsing: the Message-ID pattern and its use (`:75`, `:131`), the From
  address match `:281`, `X-Gmail-Labels` `:288`×2, and `Auto-Submitted` and
  `Precedence` `:296`×2, `:299`×2.

### Recipient redaction and name-map validation (12)

`_ADDR_TOKEN` (`:74`) and `_redact_addresses` (`:997`) replace addresses with
labels from a persisted `StableRedactionMap`. The output depends on that stateful
map, not on the text alone. The rest are address-key normalization and label
checks: `_validate_name_map` `:943`×2, `:945`, `:951`×2, `:952`, `:957`,
`:959`, and `_build_recipient_map`'s key lambda `:980`×2.

### Digests over metadata, config and file bytes (21)

None of these hashes prose:

- `_private_locator` (`:136`×2): a domain-separated hash of a Message-ID, which
  is nontext metadata.
- `_file_sha256` (`:1033`, `:1037`): raw file bytes.
- The behavior fingerprint (8): `_behavior_fingerprint_from_public` (`:1046`,
  `:1076`, `:1097`), `_behavior_fingerprint` (`:1100`, `:1116`), and calls at
  `:1136`, `:2304` and `:2419`. It hashes option values, the policy version and
  the file hash. It is a config fingerprint of the module's own behavior, the
  same kind of thing Q5 put out of scope.
- `_roots_sha256` (`:1465`×2): the thread-root map.
- `_behavior_params_public` (`:2256`×2): a bare
  `sha256("\n".join(own_sig_lines))` of the operator's signature config.
  Q1 makes it Local.
- `_resume_fingerprint` (`:2287`, `:2294`×2) and its calls at `:2025` and
  `:2560`: JSON of run params.

### Validators and CLI (8)

- Digest formats: `:1252`, `:1480`.
- Manifest line parsing: `:1224`×2.
- CLI and interactive parsing: the `--own-address` lowercase `:1765`, the
  approve-smoke answer `:2511`×2, and `_KNOWN_SUBCOMMANDS` `:2585`.

### Other `process_message` sites (4)

- `:1827` is an emptiness predicate.
- `:1867` is `cleaned.strip()` after `ac.preprocess_text`. It has no effect on
  this path. The call uses the default `allow_non_prose=False`, so
  `strip_non_prose` always returns through `_collapse_whitespace`, which ends
  in `.strip()` (`setec/core/preprocessing.py:722-723`, `:873`, `:899`).
- `:1877`×2: `re.sub(r"\s+", " ", cleaned).strip()[-80:]`. This feeds only the
  trailing-text repetition counter, which the summary reports as counts
  (`:1596-1603`). No text leaves this site. The same collapse spelling appears
  at `setec/surfaces/acquire_imessage_sent.py:177`, `:187` and `:462`
  (shard 18).

## Cross-references for shards 18 and 19

None of these is dispositioned here:

- AF's newline chain also appears at
  `setec/surfaces/acquire_stackexchange.py:148`, inside `_normalize_ws`.
- `acquisition_core.preprocess_text` (`:715`) and `html_to_text` (`:1148`)
  carry this module's Cohort T and HTML cleaning.
- `acquisition_core.py:778` and the iMessage and everycrsreport `\S+` counts
  are T-shaped spellings; see the list above.

## Method

1. Filtered the checker output at `93675ba` to this file and confirmed 94
   unresolved discoveries.
2. Read each site in context. Traced the cleaning path from `process_message`
   and the importers of the module.
3. Hashed function bodies (`ast.dump`, docstrings removed) across the
   acquisition modules to look for copies.
4. Ran probes on synthetic strings only, against the imported module, with
   sockets disabled. No mailbox, export, credential or corpus file was read.
   No model call.
