# 10-productive-roughness-audit

> A **strictly baseline-relative** descriptive profile of a writer's "productive
> roughness": fragments, sentence-initial conjunctions, contractions, adjacent-word
> repetition, asides, very-short sentences. Reports how a *draft* moves relative to
> the *writer's own stable roughness pattern* — never an absolute "good/rough" call.

- **Status:** Spec (build on the full-deps box — needs spaCy).
- **Tier:** Tier 4 (ROADMAP → "Productive Roughness Audit … methodologically fragile … has to be **strictly baseline-relative** … never absolute. Otherwise it encodes editorial preferences as voice. Build only with that constraint frontloaded.").
- **GPU required:** no, but **needs spaCy** (`en_core_web_sm`) for fragment / finite-verb detection — which is why this is a box build, not a sandbox build.
- **License:** N/A (local).

## Motivation & orthogonality

AI editing and copyediting sand off a writer's *productive* roughness — fragments
for emphasis, sentence-initial "And/But", contractions, repetition for rhythm. But
"roughness" is in the eye of the beholder, so the roadmap's hard constraint is that
this surface must be **baseline-relative**: it measures *this writer's* stable
roughness pattern (from their pre-draft baseline) and reports the draft's deviation
from it. It must never assert that roughness is good or bad in the abstract.
Orthogonal to `paragraph_audit` (paragraph shape) and `punctuation_cadence_audit`
(punctuation) — this is a sentence-internal roughness cluster.

## Method (spaCy-backed)

Per the draft and the baseline corpus, compute: fragment rate (sentences with no
finite/root verb — spaCy dependency parse), sentence-initial coordinating-conjunction
rate, contraction rate, adjacent-word-repetition rate, interjection/aside rate,
very-short-sentence (<5 words) rate. Report each as the **draft value, the baseline
mean ± sd, and the z-distance** — the deviation, not an absolute.

## Contract

- **task_surface:** new `productive_roughness` (voice-coherence family; add to enum + labels).
- **CLI:** `python3 scripts/productive_roughness_audit.py DRAFT --baseline-dir DIR [--json] [--out PATH]`. **`--baseline-dir` is REQUIRED** — the audit refuses to run on a single document (the baseline-relative constraint is enforced in code, not just docs).
- **JSON envelope:** `build_output(task_surface="productive_roughness", …)`; `baseline` block populated; `results` = per-feature {draft, baseline_mean, baseline_sd, z}. Carries `ClaimLicense`.
- **Claim license:** *licenses* "how this draft's roughness features deviate from this writer's own baseline pattern"; *refuses* any absolute roughness/quality judgment, any voice/authorship/AI verdict, and any use without a writer-specific baseline.
- **capabilities.yaml:** `id: productive_roughness_audit`, `surface: productive_roughness`, `status: heuristic`, `compute: {tier: spacy, length_floor_words: 1000}`, `dependencies.python: [spacy]`.

## Test contract (`tests/test_productive_roughness_audit.py`)

- `test_surface_registered`; `test_requires_baseline` (no `--baseline-dir` → clean error / `available=False`); `test_fragment_detection` (spaCy stub or `en_core_web_sm`); `test_features_are_relative` (results carry baseline_mean + z, not an absolute band); `test_claim_license_refuses_absolute_and_verdict`; `test_envelope_shape`; `test_deterministic`.

## Non-goals

- **Never absolute.** No "this is too smooth/too rough" verdict; no quality score.
- Not a voice-identity or AI call. Requires the writer's own baseline.

## Open questions

- Fragment detection precision across registers (dialogue-heavy fiction vs. essay);
  may need a register flag.
