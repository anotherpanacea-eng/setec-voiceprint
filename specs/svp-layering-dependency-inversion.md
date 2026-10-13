# SVP dependency inversion — non-text helpers

**Status:** BUILD-READY (independent seven-lens spec review, zero findings).
**Date:** 2026-10-13 · **Repo:** setec-voiceprint · **Fleet issue:** #708 (spec only).
**tier:** COMPLEX · **builder:** gpt-6.1-sol/high (supplied invocation metadata).
**Reason:** a small extraction must preserve mutable registry and calibration alias compatibility while removing real dependencies.

## Authority and verified baseline

Fleet BURN-DOWN-BOARD §5l CB-5 admits non-text helper extraction, one bounded
hub unit per PR, strict layering and the suite green. Text primitives stay
Claude-only. The packaging conversion spec's Layout and ownership item 4 and
§3 explicitly exclude helper extraction and shared surface harnesses; this
independently reviewed contract supplies the separate authority.

Source inspected at fetched origin/main
`606583a62a1fec7f6e0c2730d91f51325939818e`. All runtime paths below are under
plugins/setec-voiceprint/scripts/. Re-fetch and re-derive the graph before each
implementation; counts here describe this baseline, never replace the gate.

- `plugins/setec-voiceprint/scripts/setec/surfaces/variance_audit.py:1927` defines threshold statuses;
  `plugins/setec-voiceprint/scripts/setec/surfaces/variance_audit.py:1937` defines ThresholdSpec;
  direction resolvers start at lines 2099/2157, registry at 2177,
  status helpers at 2427, POS_BIGRAM_KL_HEURISTIC at 2539.
- `plugins/setec-voiceprint/scripts/calibration/calibrate_thresholds.py:83` resolves public runtime aliases
  lazily. Its COMPRESSION_HEURISTICS supplier imports variance_audit; its
  other scoring aliases still come from validation_harness.
- `plugins/setec-voiceprint/scripts/tests/test_calibrate_thresholds_import_boundary.py:78` protects genuine
  direct/from-import aliases, shared registry/value identity and caller patches.
  `plugins/setec-voiceprint/scripts/tests/test_per_judge_generator_routing.py:389` changes a registry item and
  observes a real classifier result.
- `plugins/setec-voiceprint/scripts/setec/surfaces/before_after_restoration.py:76` and
  `plugins/setec-voiceprint/scripts/calibration/calibration_survey.py:89` import
  only COMPRESSION_HEURISTICS from variance_audit, at module level.
- `tools/check_layering.py:40` reports cycles without gating them. Current
  strict JSON passes: 14 l1_to_l2, 279 l2_to_l2, five cycles. The exact exemption
  for calibration/calibrate_thresholds.py → variance_audit.py is l2_to_l2.

## First durable unit: threshold metadata and three eliminated dependencies

Create the proposed setec/core/compression_thresholds module. Move only
THRESHOLD_STATUS_VALUES, ThresholdSpec, resolve_direction_with_slice,
resolve_direction, COMPRESSION_HEURISTICS, POS_BIGRAM_KL_HEURISTIC,
signals_by_status, calibrated_signals, literature_anchored_signals,
empirically_oriented_signals, heuristic_signals and provisional_signals.

The new module consumes only stdlib dataclass/typing support and produces
the existing class, registry, metadata constant and callable interfaces.
Preserve every signature, default, field, validation error, registry order,
threshold, provenance/status, direction override and fallback rule. No
calibration policy change accompanies moving the registry.

variance_audit consumes and re-exports those same objects; it retains all
classification, formatting, envelope, CLI, text/NLP/model and audit execution.
Do not duplicate registry objects or replace default-argument registry identity.
In-place mutation through the surface alias must remain visible to existing
classification and moved status helpers. Nothing in the repo pickles
ThresholdSpec, and the re-export already resolves old pickled references, so
no `__module__` override is needed.

calibration/calibrate_thresholds consumes the same COMPRESSION_HEURISTICS
object from core in its existing lazy alias resolver. Preserve its other lazy
scoring aliases, cache behavior, plain-import/help behavior and caller-installed
patches. Correct its registry-location documentation and the operator message
at `plugins/setec-voiceprint/scripts/calibration/calibrate_thresholds.py:3097`
("Next: edit scripts/variance_audit.py…") in the same file. Retarget the one-line
COMPRESSION_HEURISTICS imports in before_after_restoration and
calibration_survey to core.

Delete precisely the three obsolete l2_to_l2 layer_exemptions rows with to_path
plugins/setec-voiceprint/scripts/variance_audit.py and from_path
calibration/calibrate_thresholds.py, calibration/calibration_survey.py or
setec/surfaces/before_after_restoration.py (each under
plugins/setec-voiceprint/scripts/). Preserve all unrelated rows and the
packaging exemptions key. Do not reseed the baseline, add exceptions, change the checker, or hide surface edges.
At the cited baseline this removes three L2 exceptions; it does not complete the
variance hub or remove an L1 dependency. Fresh-base counts may differ.

## Acceptance and delivery

1. Run existing threshold-spec, comparator/slice routing, compression-polarity
   and calibrate-thresholds import-boundary behavior tests without dependency
   skips obscuring the new core tests. Add only behavioral regressions needed
   for this extraction: core import and metadata use in a fresh stdlib-only
   subprocess that refuses surface/NLP/model imports; genuine class/registry
   aliases and in-place mutation effects. No source-text location test.
2. Compare fetched-base and candidate classifier outputs on deterministic
   synthetic audit dictionaries spanning available/missing signals, length
   floors and comparator/judge/generator fallback. Reuse the real producer
   contract fixture builder: all consumer fixture values remain unchanged.
   Do not retain an implementation hash/AST snapshot as a refactor oracle.
3. Exercise imports and existing normalized variance CLI in a copied plugin
   from a foreign cwd without installation or PYTHONPATH; reuse the zero-install
   gate. Preserve legacy/package surface-module alias identity and metadata
   availability without model downloads. Run strict layering and packaging
   checks; verify the named dependency disappeared and all surviving exceptions
   remain valid. Record cycle differences diagnostically.
4. Run full pytest on base and candidate with matching dependencies/environment,
   compare outcomes, and report inherited failures/skips explicitly. CB-5's
   green-suite gate remains required; inherited failures mean that gate is
   unresolved, not permission to claim full clearance. Run existing producer
   drift/docs/fixture gates and git diff --check. No hosted arming is authorized.
5. The implementation needs its own exact path checkout, scoped plan and independent build review
   before push/opening a draft constituent. Code-safe local receipts identify
   exact baseline/head and actual outcomes. Normal Claude/Codex review and train
   clearance remain required; this spec and its first unit authorize no merge.

## Increment boundary and later work

The current spec PR edits only this contract and its changelog fragment.
Its first implementation unit is limited to the four existing runtime modules
named above, the proposed core module, exact dead layer rows, relevant behavioral tests and
its own changelog fragment. Implementation paths need a separate grant.

Defer all text primitives/tokenization, POS/dependency helpers, spaCy state,
backend factories, score extraction, classification extraction, harness/CLI
consolidation and remaining metadata-only consumers. Beyond its one import
line, calibration_survey, shard_runner and train_xgboost packaging decisions,
the acquisition_core overlap in baselines-fallback PR #738, and binoculars
relocation PR #739 are outside this scope.
CB-6 owns separate backend/hermetic work; any later shared variance path work
must serialize. No provider use, training, private data, guard restoration,
wheel packaging, schema changes, threshold ratification or spend.

Later acquisition_core, manifest_validator, validation_harness and tail hubs
require refreshed source/consumer inventory, a bounded written work order,
custody and independent review before each PR. Do not infer extraction authority
for text helpers or completion of all dependency edges from this first unit.
