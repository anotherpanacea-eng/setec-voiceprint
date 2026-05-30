#!/usr/bin/env bash
# bakeoff_mage_tier34.sh -- MAGE Tier 3+4 model-selection bake-off.
#
# Drives the 1.81.0 standalone calibration_survey.py CLI across the
# 4 embedding-model aliases (Tier 3) and 3 surprisal-model aliases
# (Tier 4) on a single shared 5K stratified MAGE subsample.
#
# Phase A (Tier 3): 4 embedding aliases x 5K subsample, --no-tier4.
# Phase B (Tier 4): 3 surprisal aliases x 5K subsample, --no-tier3.
# Phase A and Phase B can run in parallel on a GPU with >=8GB VRAM
# (embeddings ~300-400M params; smallest surprisal LM is gpt2 at
# 124M; tinyllama at 1.1B; llama32_1b at 1.23B).
#
# Phase C (full re-score with winners) is NOT in this script; once
# Phase A + B complete and the maintainer picks winners, the Phase C
# invocation drops --max-entries and adds the winning aliases.
#
# All 7 invocations share:
#   --max-entries 5000 --max-entries-seed 42   <- shared stratified subsample
#   --fpr-target 0.01                          <- MAGE Tier 1+2 baseline target
#   --bootstrap-engine torch                   <- GPU-accelerated bootstrap
#   --records-cache <unique per config>        <- per-config to avoid cache collision
#   --out <unique per config>                  <- per-config survey JSON
#
# Outputs land under SURVEYS_DIR (set below). The companion
# bakeoff_mage_tier34_compare.py reads all 7 surveys and emits a
# comparison table.
#
# **Smoke-test verification (2026-05-17)**: Phase A / mxbai ran end-
# to-end through the calibration_survey CLI at 5K, scored 5000/5000
# records in ~7 min on CPU, and emitted a survey JSON with the
# expected provenance fields (``embedding_model: mxbai`` in the
# top-level block; ``do_tier4: false``; correct ``per_signal``
# entries for adjacent_cosine_mean / adjacent_cosine_sd). The Tier 3
# columns came back None because sentence-transformers wasn't
# installed in the smoke env -- expected behavior from the 1.80.0
# fix (typed-error fall-through returns None rather than silently
# falling back to MiniLM). With sentence-transformers + a real GPU
# install the same invocation produces real cosine values.

set -euo pipefail

# -----------------------------------------------------------------
# Paths.
# -----------------------------------------------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
MANIFEST="${REPO_ROOT}/ai-prose-baselines-private/mage/manifest.jsonl"
SURVEYS_DIR="${REPO_ROOT}/ai-prose-baselines-private/calibration_runs/bakeoff_mage_tier34_5K"
SURVEY_SCRIPT="${REPO_ROOT}/plugins/setec-voiceprint/scripts/calibration/calibration_survey.py"

mkdir -p "${SURVEYS_DIR}"

# -----------------------------------------------------------------
# Shared flags.
# -----------------------------------------------------------------
SHARED_FLAGS=(
    --manifest "${MANIFEST}"
    --use validation
    --fpr-target 0.01
    --max-entries 5000
    --max-entries-seed 42
    --bootstrap-engine torch
    --bootstrap-resamples 2000
    --bootstrap-seed 42
    --records-cache-flush-every 100
    --json-only
)

# -----------------------------------------------------------------
# Phase A: Tier 3 embedding bake-off.
# -----------------------------------------------------------------
# Restrict to the 2 Tier 3 signals (adjacent_cosine_mean / _sd).
# --no-tier4 skips surprisal entirely so no LM is loaded; Tier 1+2
# columns get computed for free during the same scoring pass.

PHASE_A_SIGNALS=(adjacent_cosine_mean adjacent_cosine_sd)
PHASE_A_MODELS=(mxbai gemma harrier minilm)

run_phase_a() {
    local model="$1"
    local cache="${SURVEYS_DIR}/cache_phaseA_${model}.json"
    local out="${SURVEYS_DIR}/survey_phaseA_${model}.json"
    echo "[Phase A / ${model}] Scoring 5K subsample with Tier 3 only..."
    # calibration_survey's --signal flag is action="append", so each
    # restricted signal needs its own --signal X repetition (not a
    # single space-separated list).
    local signal_flags=()
    for s in "${PHASE_A_SIGNALS[@]}"; do
        signal_flags+=(--signal "${s}")
    done
    local rc=0
    python "${SURVEY_SCRIPT}" \
        "${SHARED_FLAGS[@]}" \
        --tier3 --no-tier4 \
        --embedding-model "${model}" \
        "${signal_flags[@]}" \
        --records-cache "${cache}" \
        --out "${out}" || rc=$?
    if [[ "${rc}" -eq 0 ]]; then
        return 0
    fi
    # calibration_survey returns 1 for "no signal passes all gates" but
    # writes the survey JSON first; that no-verdict is expected at this 5K
    # subsample, so continue. Anything else -- exit 2 (bad args), a
    # propagated scoring failure, or an exit-1 arg-validation SystemExit
    # that wrote no survey -- is a real failure: abort the bake-off.
    if [[ "${rc}" -eq 1 && -s "${out}" ]]; then
        echo "[Phase A / ${model}] calibration_survey exited 1 with a survey" \
             "written (expected 'no all-gates-pass' verdict at 5K). Continuing." >&2
        return 0
    fi
    echo "[Phase A / ${model}] calibration_survey FAILED (exit ${rc}; no usable" \
         "survey at ${out}); aborting bake-off." >&2
    return "${rc}"
}

# -----------------------------------------------------------------
# Phase B: Tier 4 surprisal bake-off.
# -----------------------------------------------------------------
# Restrict to the 3 Tier 4 signals. --no-tier3 skips the embedding
# pass entirely so no embedding model loads. Tier 1+2 columns
# computed for free.

PHASE_B_SIGNALS=(surprisal_mean surprisal_sd surprisal_acf_lag1)
PHASE_B_MODELS=(gpt2 tinyllama llama32_1b)

run_phase_b() {
    local model="$1"
    local cache="${SURVEYS_DIR}/cache_phaseB_${model}.json"
    local out="${SURVEYS_DIR}/survey_phaseB_${model}.json"
    echo "[Phase B / ${model}] Scoring 5K subsample with Tier 4 only..."
    local signal_flags=()
    for s in "${PHASE_B_SIGNALS[@]}"; do
        signal_flags+=(--signal "${s}")
    done
    local rc=0
    python "${SURVEY_SCRIPT}" \
        "${SHARED_FLAGS[@]}" \
        --no-tier3 --tier4 \
        --surprisal-model "${model}" \
        "${signal_flags[@]}" \
        --records-cache "${cache}" \
        --out "${out}" || rc=$?
    if [[ "${rc}" -eq 0 ]]; then
        return 0
    fi
    # See run_phase_a: tolerate only the exit-1 no-verdict (survey written),
    # abort on any other failure.
    if [[ "${rc}" -eq 1 && -s "${out}" ]]; then
        echo "[Phase B / ${model}] calibration_survey exited 1 with a survey" \
             "written (expected 'no all-gates-pass' verdict at 5K). Continuing." >&2
        return 0
    fi
    echo "[Phase B / ${model}] calibration_survey FAILED (exit ${rc}; no usable" \
         "survey at ${out}); aborting bake-off." >&2
    return "${rc}"
}

# -----------------------------------------------------------------
# Drivers.
# -----------------------------------------------------------------
# Usage:
#   bash bakeoff_mage_tier34.sh phase_a       # run all 4 Phase A configs serially
#   bash bakeoff_mage_tier34.sh phase_b       # run all 3 Phase B configs serially
#   bash bakeoff_mage_tier34.sh phase_a mxbai # run a single Phase A config
#   bash bakeoff_mage_tier34.sh all           # run everything serially (~hours wall-clock)
#
# For parallel: launch phase_a and phase_b in separate shells.
# Each phase is itself serial within (don't compete with self for GPU).

case "${1:-all}" in
    phase_a)
        if [[ -n "${2:-}" ]]; then
            run_phase_a "$2"
        else
            for m in "${PHASE_A_MODELS[@]}"; do run_phase_a "${m}"; done
        fi
        ;;
    phase_b)
        if [[ -n "${2:-}" ]]; then
            run_phase_b "$2"
        else
            for m in "${PHASE_B_MODELS[@]}"; do run_phase_b "${m}"; done
        fi
        ;;
    all)
        for m in "${PHASE_A_MODELS[@]}"; do run_phase_a "${m}"; done
        for m in "${PHASE_B_MODELS[@]}"; do run_phase_b "${m}"; done
        ;;
    *)
        echo "Usage: $0 {phase_a [model] | phase_b [model] | all}" >&2
        exit 1
        ;;
esac

echo
echo "Done. Compare results:"
echo "  python ${REPO_ROOT}/plugins/setec-voiceprint/scripts/calibration/bakeoff_mage_tier34_compare.py \\"
echo "      --surveys-dir ${SURVEYS_DIR}"
