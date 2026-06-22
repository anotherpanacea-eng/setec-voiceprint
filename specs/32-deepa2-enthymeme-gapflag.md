# Spec 32: `deepa2-enthymeme-gapflag` — model-free structural enthymeme (suppressed-premise) LOCATION flags

**Capability id:** `enthymeme_gapflag` (tool/script: `enthymeme_gapflag.py`)
**Task surface (REUSED):** `argument_pattern_scan` — the existing ArgScope surface that already hosts `fallacy_scan` and `warrant_probe`. No new surface fragment.
**Family:** `argument-pattern`
**arXiv root:** Betz, Richardson, *"DeepA2: A Modular Framework for Deep Argument Analysis with Pretrained Neural Text2Text Language Models"*, **arXiv:2110.01509**. Cited here, in the PR body, and in the `changelog.d/` fragment per the fleet rule.

> **Review folded (`deepa2-enthymeme-gapflag-findings.md`, verdict GO-WITH-CHANGES).** The findings are folded into this in-repo copy:
> - **[P1]** `compute.tier: cpu` is NOT a valid tier — the live vocabulary is `{acquisition, api_llm, core, ocr, optional, spacy, surprisal}`. The stdlib path is **`tier: core`** (matching `originality_audit` / the stdlib siblings).
> - **[P2]** Distinguished from the `argmove_profile` (surface `assertoric`, family `argument-moves` — aggregates stance/abstraction signals) and `discourse_move_signature` (surface `smoothing_diagnosis` — typed discourse markers + move sequences) neighbors: those profile move *types/sequences*; this flags premise→conclusion *jumps lacking a warrant marker*. Different surface, different axis.
> - **[P3]** `originality_audit` ships **no** band object, so the band-shape precedent cited is **`tocsin_audit` / `dependency_distance_audit`** (a VALUE + a PROVISIONAL band over the rate's own axis + `calibration_status`), not `originality_audit`.
> - **Build-time correction (mode-9/mode-6, caught in self-review):** the warrant-bridge lexicon must **exclude** the high-frequency bare words `for` / `as` / `if`, which double as non-inferential prepositions/conjunctions ("toxic chemicals **for** years", "the best choice **for** the city") and would silently *suppress* legitimate enthymeme flags — the dominant false-negative for a marker-only detector. The warrant lexicon is restricted to reliable inferential connectives (`because`, `since`, `given that`, `on the grounds that`, `it follows from`, `due to`, `as a rule`, `whenever`, `in general`, `by virtue of`, …). Pinned by a regression test.

---

## 1. Framing (one paragraph)

**DeepA2** (arXiv:2110.01509) reconstructs an informal argument into a premise–conclusion structure, and a core DeepA2 sub-task is **inserting the missing (suppressed) premise** an enthymeme leaves implicit. That reconstruction is a **generative, model-driven** act — exactly the part SETEC's posture forbids a detector from doing autonomously. This capability splits DeepA2 at the seam the posture demands. **M1 (this spec, stdlib, no model):** *detect the location* of a candidate enthymeme — a conclusion-marked sentence whose inferential support in the local window is **not bridged** by a warrant/connective marker — and emit it as a **candidate suppressed-premise LOCATION** (a span pointer + the structural evidence). It surfaces; it does not author. **M2 (separate later PR, model, gated):** the DeepA2-style reconstruction that *authors* the missing premise, behind a lazy-import + `skipif`; never runs in CI, never ships in M1. This is the stdlib, location-first sibling of `warrant_probe`: where `warrant_probe` asks an LLM judge whether a claim's warrant is present/partial/absent, this points model-free at *where* a warrant looks elided.

---

## 2. Unit of analysis

- **Input:** one UTF-8 argument-shaped nonfiction passage (`--target`). No baseline, no pool.
- **Segmentation:** the **sentence** (deterministic stdlib split on terminal punctuation), walked within paragraphs; the global 0-based `sentence_index` and the `paragraph_index` are retained (warrant_probe parity).
- **The flagged unit:** a candidate suppressed-premise LOCATION = a *conclusion-marked* sentence (or a paragraph-terminal assertion after ≥1 ground sentence) whose inferential support in the local, paragraph-bounded window is **not bridged** by an explicit warrant/connective marker.

### How the JUMP is detected (model-free, deterministic, stdlib)

A flag is raised when **all** hold over a local window (this is the *evidence*, not a ruling):

1. **A conclusion is asserted.** The sentence contains a *conclusion* connective from a fixed lexicon (`therefore`, `thus`, `hence`, `so`, `consequently`, `it follows`, `which shows`, …) **OR** it is the paragraph-terminal assertion after ≥1 prior ground sentence (the classic implicit-conclusion shape). The matched marker (or `"terminal-assertion"`) is recorded.
2. **No warrant bridge is present.** The window spanning the conclusion + its preceding ground sentence(s) within the paragraph contains **no** reliable *warrant/inferential-bridge* marker (`because`, `since`, `given that`, `on the grounds that`, `it follows from`, `due to`, `as a rule`, …). A bridge present ⇒ **not** flagged (the link is stated). Bare `for`/`as`/`if` are deliberately excluded (review correction above).
3. **The jump spans distinct content.** The conclusion and its ground window share stopword-filtered content-token Jaccard **below** a fixed ceiling (a pure restatement — "X, therefore X" — is a tautological echo, not a suppressed-premise jump).

The lexicons are fixed, SETEC-internal, versioned constants (`MARKER_VERSION = "enthymeme_markers_v1"`) — markers, not a learned classifier — so M1 is deterministic, CI-runnable, and Goodhart-free.

---

## 3. EXACT result data shape (and the proof it carries NO verdict)

`detect_enthymemes(...)` returns the script-specific `results` payload passed to `build_output(...)`:

```jsonc
{
  "method_version": "enthymeme_gapflag_structural_v1",
  "marker_version": "enthymeme_markers_v1",
  "enthymeme_gap_flags": [                       // THE DELIVERABLE — document order, never ranked
    {
      "candidate_type": "suppressed_premise",    // the ONLY type; carries the framing
      "sentence_index": 2,
      "paragraph_index": 0,
      "span_text": "Therefore the plant must be shut down.",
      "jump_evidence": {                         // WHY it was flagged — structural facts only
        "conclusion_marker": "therefore",        // matched marker, or "terminal-assertion"
        "warrant_bridge_present": false,
        "ground_window_sentence_indices": [0, 1],
        "content_overlap_jaccard": 0.0           // condition 3 value (NOT a verdict)
      }
      // NO reconstructed/suggested/filled premise key — M1 never authors it.
    }
  ],
  "gap_density": {                               // VALUE + PROVISIONAL band, never a gate
    "value": 0.5, "n_flags": 1, "n_inferential_steps": 2,
    "band": "typical",                           // sparse / typical / dense — over the rate's OWN axis
    "band_edges": { "low": 0.15, "high": 0.65 }, // PROVISIONAL named constants, NOT a gate
    "calibration_status": "uncalibrated"
  },
  "marker_tally": { "conclusion_markers": 1, "terminal_assertions": 0, "warrant_bridges": 1 },
  "n_flags": 1, "n_sentences": 3, "n_paragraphs": 1,
  "register_warnings": [ /* soft caveats */ ],
  "calibration_status": "uncalibrated"
}
```

**No decision key** (`verdict` / `soundness` / `unsound` / `incomplete` / `quality` / `score` / `*_score` absent — recursive-walk test). **No generated-premise key** (`reconstructed_premise` / `suggested_premise` / `filled_premise` absent). **`gap_density`** is a VALUE + a PROVISIONAL band + `calibration_status: "uncalibrated"`; a higher rate is explicitly **not** "worse". **Never-selects:** flags in `sentence_index` order, no rank/severity/confidence scalar.

---

## 4. M1 scope (build now) vs the M2 seam (gated, later)

- **M1 — model-free stdlib, CI-runnable (THIS BUILD).** Pure `re` + stdlib sets; imports only `output_schema` + `claim_license`. No `judge_backends`, `transformers`/`torch`, embeddings, spaCy, or network. Deterministic; runs in CI with no skip.
- **M2 — DeepA2 reconstruction (SEPARATE PR, gated, NOT this build).** A `--reconstruct` path that, for a chosen flag, runs a DeepA2-style model to *author* the candidate missing premise as a clearly-marked, human-review reconstruction, never written into the no-verdict M1 `results`. Lazy-import inside the branch; `@pytest.mark.skipif` on backend availability. Even M2 outputs a *candidate* the human accepts/rejects; it never emits a soundness verdict.

---

## 5. Acceptance criteria

AC-1 stdlib-only import (no transformers/torch/spacy/judge_backends). AC-2 deterministic. AC-3 flags a marked jump with no warrant bridge. AC-4 does NOT flag a stated warrant. AC-5 does NOT flag a tautological echo. AC-6 terminal-assertion shape. AC-7 never authors (no premise key, recursively). AC-8 no-verdict recursive walk + `calibration_status == "uncalibrated"`. AC-9 band is descriptive, not a gate. AC-10 never-selects (document order, no rank/severity/confidence). AC-11 length floor (`bad_input` below `HARD_MIN_WORDS`) + soft register caveats. AC-12 claim license refuses authorship + completeness/soundness verdict; `task_surface == "argument_pattern_scan"`. AC-13 drop-in registration round-trips (per-id golden, no `==N`). AC-14 arXiv:2110.01509 cited in PR + changelog. (26 tests.)

### Posture guards

No-verdict recursive walk (AC-8); never-selects (AC-10); never-authors (AC-7); anti-Goodhart — M1 ships **no** calibration, so there is no threshold to overfit; the marker lexicons are author-independent linguistic constants, not tuned against any operator's arguments; any future PROVISIONAL `band_edges` derived from a corpus must be fit on a corpus **disjoint** from any fixture/validation set.

---

## 6. Registration (drop-in, NO `==N`)

- **Surface:** REUSE `claim_license_surfaces/argument_pattern_scan.txt` — no new surface fragment.
- **Capability fragment:** `capabilities.d/enthymeme_gapflag.yaml` (`tier: core`, `dependencies.python: []`, `status: literature_anchored`, `family: argument-pattern`, references → signals-glossary + arXiv:2110.01509).
- **Golden fragment:** per-id `scripts/tests/_golden_capabilities/enthymeme_gapflag.json` (count derived from fragments — no `==N` literal).
- **Docs-freshness:** `changelog.d/feat-32-enthymeme-gapflag.md`; `gen_calibration_readiness.py` re-run.
