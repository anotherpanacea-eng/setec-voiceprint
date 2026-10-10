# TP-SWEEP shard 36: run, run-set, capabilities and disagreement plumbing (2026-10-08)

Independent review of the unresolved discoveries that
`tools/gen_textprims_inventory.py --check` reports at `origin/main` `93675ba`
for `setec/surfaces/setec_run_set.py`, `setec/contract/capabilities.py`,
`setec_run.py` and `setec/surfaces/surface_disagreement_resolver.py`. This is
a report only, with no source, registry or checker change.

Fleet custody: fleet-coordination #489 (CAM-12, TP-SWEEP).

Labels follow the owner's 2026-10-08 rulings (Q1 bare digests Local; Q4 to Q7
as ruled). No site in this shard is left Open, and no cohort is proposed.

## Scope and fold

Paths are relative to `plugins/setec-voiceprint/scripts/`.

| File | Discoveries | Register | Consumer | Hold | Local | Open |
|---|---:|---:|---:|---:|---:|---:|
| `setec/surfaces/setec_run_set.py` | 14 | 0 | 0 | 1 | 13 | 0 |
| `setec/contract/capabilities.py` | 13 | 0 | 0 | 0 | 13 | 0 |
| `setec/surfaces/surface_disagreement_resolver.py` | 10 | 0 | 0 | 0 | 10 | 0 |
| `setec_run.py` | 9 | 0 | 0 | 0 | 9 | 0 |
| **Total** | **46** | **0** | **0** | **1** | **45** | **0** |

All 46 discoveries for these files are unresolved; the checker reports no other
outcome for them.

## Hold (1)

`setec_run_set.py:1222`: `target_words=len(target_text.split())`, the target's
whitespace word count recorded in the run-set report. Shard 5's held whitespace
unit.

## Local (45)

### `setec/contract/capabilities.py` (13)

- **Capability router, 4:** `_normalize` lowercases, strips and collapses
  whitespace in the operator's free-text situation and in capability
  descriptions (`:515` ×3), and `recommend` picks words of five or more letters
  from it (`:555`) to match against `use_when` and `purpose` strings. This is
  keyword routing over a request and the manifest, not analysis of prose; it
  fits no family. Same reasoning as the Q4 ruling.
- **Plumbing, 9:** module-name normalization in `is_installed` (`:204`), table
  column and line lists (`:307`, `:325`), render strips (`:403`, `:425`, `:610`,
  `:630`) and a bare digest of bytes (`:677` ×2; Q1).

### `setec/surfaces/setec_run_set.py` (13)

- Envelope key checks in the no-aggregate-verdict invariant (`:284`, `:293`).
- A bare digest of a file (`:393` ×2; Q1).
- Subprocess stderr trimming (`:630`), report rendering (`:894`, `:899`,
  `:932`, `:1092`) and CLI surface-list parsing (`:1367` ×3, `:1396`).

### `setec/surfaces/surface_disagreement_resolver.py` (10)

- **Report reading, 6:** it reads other surfaces' published band strings and
  readings (`:132` ×3, `:156`) and matches expected-reading patterns such as
  `(a|b)` (`:497`); `metadata_keys` is a key table (`:650`).
- **Idiolect phrase survival, 2:** `_read_idiolect_survival` checks whether
  each reported idiolect phrase still occurs in the target, case-insensitively
  (`:306`, `:315`). Lexical phrase matching against prose; Local under Q4.
- **Rendering, 2:** `:638`, `:731`.

### `setec_run.py` (9)

Semver parsing (`:122` ×3, `:123`), subprocess error wrapping and the
"can't open file" pattern (`:285`, `:305`, `:327`) and stdout or stderr
trimming (`:458`, `:502`).

## Method

1. Filtered the checker's JSON at `93675ba` to the four files (46 unresolved).
2. Read each site in context. No probe was needed: no site defines or re-spells
   a text unit apart from the held whitespace count.

No corpus, model or network was used.
