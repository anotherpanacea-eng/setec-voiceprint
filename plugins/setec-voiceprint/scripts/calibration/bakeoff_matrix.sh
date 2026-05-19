#!/usr/bin/env bash
# bakeoff_matrix.sh -- Cloud-portable Tier-3 / Tier-4 bake-off matrix.
#
# Iterates the framework's Phase A (Tier-3 embedding) and Phase B
# (Tier-4 surprisal) model rosters, runs calibration_survey.py per
# (model x signal) cell, writes one survey JSON per model, then
# emits a provenance.json + a markdown summary at session end.
#
# Adapted from the 2026-05-18 laptop session's bakeoff_matrix_v2.sh
# (WSL+ROCm host) to be cloud-portable: paths via env vars, no
# offline-only HF flags, bootstrap-engine=torch (cloud GPUs don't
# have the WSL instability that forced numpy on the laptop run),
# cooldown 10s (no host-thermal recovery needed). Per SPEC_cloud_-
# bakeoff_matrix.md from the 2026-05-18 session export.
#
# === Required env vars ===
#   SETEC_CORPUS_DIR           -- corpus root (contains manifest.jsonl)
#   SETEC_BAKEOFF_DIR          -- per-cell output JSONs + record caches
#   SETEC_CALIBRATION_RUNS_DIR -- destination for mirrored survey JSONs
#                                 (the calibration_runs/bakeoff_*
#                                 directory the framework expects to
#                                 read survey results from)
#
# === Optional env vars ===
#   SETEC_CORPUS_LABEL         -- "mage" / "raid" / etc. (provenance +
#                                 default for SETEC_COMPARATOR_CLASS;
#                                 default "unknown")
#   SETEC_COMPARATOR_CLASS     -- 1.99.0+: comparator class for per-
#                                 signal direction routing. When set,
#                                 propagates to calibration_survey via
#                                 ``--comparator-class``; the
#                                 calibration pipeline then routes
#                                 surprisal_sd (and any other signal
#                                 with a per-comparator override) to
#                                 the correct direction. When omitted,
#                                 defaults from SETEC_CORPUS_LABEL if
#                                 the label is one of {mage, raid};
#                                 otherwise unset (pre-1.99 behavior).
#                                 Operators with non-standard corpora
#                                 set this explicitly to opt in / out.
#   SETEC_MAX_ENTRIES          -- subsample cap for calibration_survey
#                                 (default: empty -> full corpus)
#   SETEC_MAX_ENTRIES_SEED     -- subsample seed (default 42)
#   SETEC_BOOTSTRAP_ENGINE     -- numpy / torch (default torch)
#   SETEC_BOOTSTRAP_RESAMPLES  -- bootstrap iterations (default 2000)
#   SETEC_BOOTSTRAP_SEED       -- bootstrap seed (default 42)
#   SETEC_FPR_TARGET           -- target false-positive rate (default 0.01)
#   SETEC_COOLDOWN_SEC         -- inter-model sleep (default 10)
#   SETEC_RESET_SENTINELS=1    -- delete pre-existing survey JSONs before
#                                 looping (use to re-evaluate models that
#                                 were skip-sentineled on the laptop)
#   SETEC_PHASE_A_PATHS        -- JSON map of alias->path overriding the
#                                 baked-in Phase A roster
#   SETEC_PHASE_B_PATHS        -- JSON map of alias->path overriding the
#                                 baked-in Phase B roster
#   SETEC_LOG_DIR              -- where to write the session log + summary
#                                 (default $SETEC_BAKEOFF_DIR)
#   SETEC_DRY_RUN=1            -- print the matrix plan, exit without
#                                 running any calibration cell
#   SETEC_ALLOW_PARTIAL=1      -- exit 0 even if some cells failed.
#                                 Default is to exit 1 with a list of
#                                 failed cells so an operator
#                                 watching only the return code doesn't
#                                 mistake a 5-of-7-failed Phase B run
#                                 for success. Use this when partial
#                                 data is acceptable (known-flaky model,
#                                 best-effort overnight run).
#
# === CUDA_VISIBLE_DEVICES ===
# Honored end-to-end. For multi-GPU hosts, run two copies of this
# script with different CUDA_VISIBLE_DEVICES + SETEC_BAKEOFF_DIR
# values to parallelize across phases or model subsets. The
# idempotent-skip logic prevents two copies from clobbering each
# other if they share a SETEC_BAKEOFF_DIR.

set -uo pipefail

# ----------------------------------------------------------------- Env validation

die() { echo "FATAL: $*" >&2; exit 2; }

: "${SETEC_CORPUS_DIR:?must set (path to corpus dir containing manifest.jsonl)}"
: "${SETEC_BAKEOFF_DIR:?must set (path for per-cell outputs + caches)}"
: "${SETEC_CALIBRATION_RUNS_DIR:?must set (path for mirrored survey JSONs)}"

CORPUS_DIR="$SETEC_CORPUS_DIR"
BAKEOFF_DIR="$SETEC_BAKEOFF_DIR"
RUNS_DIR="$SETEC_CALIBRATION_RUNS_DIR"
CORPUS_LABEL="${SETEC_CORPUS_LABEL:-unknown}"

# 1.99.0+: comparator class for per-signal direction routing.
# Explicit SETEC_COMPARATOR_CLASS wins; otherwise default from
# SETEC_CORPUS_LABEL when the label is one of the known framework
# classes (mage / raid); otherwise leave unset (pre-1.99 behavior --
# the calibration pipeline uses each spec's default direction).
if [ -n "${SETEC_COMPARATOR_CLASS:-}" ]; then
    COMPARATOR_CLASS="$SETEC_COMPARATOR_CLASS"
elif [ "$CORPUS_LABEL" = "mage" ] || [ "$CORPUS_LABEL" = "raid" ]; then
    COMPARATOR_CLASS="$CORPUS_LABEL"
else
    COMPARATOR_CLASS=""
fi
MAX_ENTRIES="${SETEC_MAX_ENTRIES:-}"
MAX_ENTRIES_SEED="${SETEC_MAX_ENTRIES_SEED:-42}"
BOOTSTRAP_ENGINE="${SETEC_BOOTSTRAP_ENGINE:-torch}"
BOOTSTRAP_RESAMPLES="${SETEC_BOOTSTRAP_RESAMPLES:-2000}"
BOOTSTRAP_SEED="${SETEC_BOOTSTRAP_SEED:-42}"
FPR_TARGET="${SETEC_FPR_TARGET:-0.01}"
COOLDOWN_SEC="${SETEC_COOLDOWN_SEC:-10}"
LOG_DIR="${SETEC_LOG_DIR:-$BAKEOFF_DIR}"
DRY_RUN="${SETEC_DRY_RUN:-0}"

[ -f "$CORPUS_DIR/manifest.jsonl" ] || die \
    "manifest.jsonl not found under SETEC_CORPUS_DIR=$CORPUS_DIR"

mkdir -p "$BAKEOFF_DIR" "$RUNS_DIR" "$LOG_DIR"

SESSION="$(date +%Y%m%d_%H%M%S)"
LOG="$LOG_DIR/bakeoff_matrix_${SESSION}.log"
SUMMARY="$LOG_DIR/bakeoff_matrix_${SESSION}_summary.md"
PROVENANCE="$LOG_DIR/bakeoff_matrix_${SESSION}_provenance.json"
ARGS_TMP="$LOG_DIR/bakeoff_matrix_${SESSION}_args.json"

if [ "$DRY_RUN" != "1" ]; then
    exec > >(tee -a "$LOG") 2>&1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROVENANCE_PY="$SCRIPT_DIR/_bakeoff_provenance.py"

# ----------------------------------------------------------------- Roster

# Default model roster. Aliases match the framework's calibration
# alias table; paths default to HF Hub identifiers (download on
# demand) — operators with pre-staged models override via
# SETEC_PHASE_{A,B}_PATHS.
# Values are framework aliases (NOT raw HF ids) so the canonical
# alias tables in ``embedding_backend.MODEL_ALIASES`` /
# ``surprisal_backend.MODEL_ALIASES`` stay the single source of
# truth. Hardcoding HF ids here would silently bypass alias drift
# — the original 2026-05-18 draft of this script did exactly that
# and shipped a non-canonical TinyLlama Chat tune + a Qwen3 missing
# its ``-Base`` suffix into the default Phase B roster. Operators
# wanting a non-aliased HF id pass it through
# ``SETEC_PHASE_{A,B}_PATHS`` as a full HF identifier explicitly.
DEFAULT_PHASE_A_PATHS='{
    "mxbai":   "mxbai",
    "gemma":   "gemma",
    "harrier": "harrier",
    "minilm":  "minilm"
}'
DEFAULT_PHASE_B_PATHS='{
    "gpt2":         "gpt2",
    "tinyllama":    "tinyllama",
    "llama32_1b":   "llama32_1b",
    "olmo2_1b":     "olmo2_1b",
    "qwen25_1_5b":  "qwen25_1_5b",
    "qwen3_1_7b":   "qwen3_1_7b",
    "smollm2_1_7b": "smollm2_1_7b"
}'

PHASE_A_PATHS_JSON="${SETEC_PHASE_A_PATHS:-$DEFAULT_PHASE_A_PATHS}"
PHASE_B_PATHS_JSON="${SETEC_PHASE_B_PATHS:-$DEFAULT_PHASE_B_PATHS}"

# Phase A: Tier-3 embedding (adjacent_cosine_*).
# Phase B: Tier-4 surprisal (surprisal_*).
PHASE_A_SIGNALS=(adjacent_cosine_mean adjacent_cosine_sd)
PHASE_B_SIGNALS=(surprisal_mean surprisal_sd surprisal_acf_lag1)

# Aliases (loop order) from the JSON keys. Sorted so the order is
# deterministic across sessions; operators who care about a specific
# order can override SETEC_PHASE_{A,B}_PATHS with an OrderedDict-like
# JSON if a future Python version drops dict insertion order
# (currently 3.7+ guarantees it for parsed JSON).
#
# Pre-declared as empty arrays + populated one key per line so empty
# rosters (e.g., one-phase bake-offs via SETEC_PHASE_A_PATHS='{}' or
# SETEC_PHASE_B_PATHS='{}') don't trip nounset on the legacy bash
# 3.2 / 4.3 word-splitting path that would otherwise treat
# ``$()``-of-empty-string as an unbound array.
PHASE_A_ALIASES=()
while IFS= read -r line; do
    [ -n "$line" ] && PHASE_A_ALIASES+=("$line")
done < <(python3 -c "
import json
for k in json.loads('''$PHASE_A_PATHS_JSON''').keys():
    print(k)
")
PHASE_B_ALIASES=()
while IFS= read -r line; do
    [ -n "$line" ] && PHASE_B_ALIASES+=("$line")
done < <(python3 -c "
import json
for k in json.loads('''$PHASE_B_PATHS_JSON''').keys():
    print(k)
")

resolve_path() {
    # Look up the path for an alias in one of the phase JSON maps.
    # Usage: resolve_path "$PHASE_A_PATHS_JSON" "$alias"
    python3 -c "
import json,sys
d = json.loads('''$1''')
print(d['$2'])
"
}

# ----------------------------------------------------------------- Banner

echo "============================================================"
echo "Cloud bake-off matrix -- session $SESSION at $(date +%H:%M:%S)"
echo "  corpus:    $CORPUS_LABEL  ($CORPUS_DIR)"
echo "  comparator_class: ${COMPARATOR_CLASS:-(none, pre-1.99 behavior)}"
echo "  surveys -> $BAKEOFF_DIR/  -> $RUNS_DIR/"
echo "  log:       $LOG"
echo "  summary:   $SUMMARY"
echo "  provenance: $PROVENANCE"
echo "  cuda_visible_devices: ${CUDA_VISIBLE_DEVICES:-(unset)}"
echo "  max_entries: ${MAX_ENTRIES:-(full)}"
echo "  bootstrap: engine=$BOOTSTRAP_ENGINE resamples=$BOOTSTRAP_RESAMPLES"
echo "  fpr_target: $FPR_TARGET   cooldown: ${COOLDOWN_SEC}s"
echo "  phase_a_aliases: ${PHASE_A_ALIASES[*]:-(none)}"
echo "  phase_b_aliases: ${PHASE_B_ALIASES[*]:-(none)}"
echo "============================================================"

if [ "${SETEC_RESET_SENTINELS:-0}" = "1" ]; then
    echo "SETEC_RESET_SENTINELS=1 -- deleting any pre-existing survey JSONs"
    rm -f "$BAKEOFF_DIR"/survey_phase{A,B}_*.json
fi

# ----------------------------------------------------------------- Provenance write

# Serialise args + roster to JSON so the Python helper can build the
# provenance dict without re-parsing bash strings. Bash variables go
# through the env (no string-interpolation hazards on operator-
# supplied paths or roster JSON); the helper itself handles git /
# platform / package-version probing.
export _SETEC_ARGS_TMP="$ARGS_TMP"
export _SETEC_SESSION="$SESSION"
export _SETEC_CORPUS_LABEL="$CORPUS_LABEL"
export _SETEC_COMPARATOR_CLASS="$COMPARATOR_CLASS"
export _SETEC_MANIFEST_PATH="$CORPUS_DIR/manifest.jsonl"
export _SETEC_PHASE_A_JSON="$PHASE_A_PATHS_JSON"
export _SETEC_PHASE_B_JSON="$PHASE_B_PATHS_JSON"
export _SETEC_PHASE_A_SIGNALS="${PHASE_A_SIGNALS[*]}"
export _SETEC_PHASE_B_SIGNALS="${PHASE_B_SIGNALS[*]}"
export _SETEC_MAX_ENTRIES="$MAX_ENTRIES"
export _SETEC_BOOTSTRAP_ENGINE="$BOOTSTRAP_ENGINE"
export _SETEC_BOOTSTRAP_RESAMPLES="$BOOTSTRAP_RESAMPLES"
export _SETEC_FPR_TARGET="$FPR_TARGET"
export _SETEC_COOLDOWN_SEC="$COOLDOWN_SEC"
export _SETEC_BAKEOFF_DIR="$BAKEOFF_DIR"
export _SETEC_REPO_ROOT="$REPO_ROOT"
python3 - <<'PY' || die "failed to write args file for provenance helper"
import json, os

phase_a = json.loads(os.environ["_SETEC_PHASE_A_JSON"])
phase_b = json.loads(os.environ["_SETEC_PHASE_B_JSON"])
me = os.environ["_SETEC_MAX_ENTRIES"].strip()
out = {
    "session_id": os.environ["_SETEC_SESSION"],
    "corpus_label": os.environ["_SETEC_CORPUS_LABEL"],
    # 1.99.0+: comparator class for per-signal direction routing.
    # Empty string when unset (pre-1.99 behavior). Recorded in
    # provenance so replays can reconstruct the exact direction
    # regime the matrix ran under.
    "comparator_class": (
        os.environ["_SETEC_COMPARATOR_CLASS"] or None
    ),
    "manifest_path": os.environ["_SETEC_MANIFEST_PATH"],
    "phase_a_aliases": list(phase_a.keys()),
    "phase_b_aliases": list(phase_b.keys()),
    "phase_a_signals": os.environ["_SETEC_PHASE_A_SIGNALS"].split(),
    "phase_b_signals": os.environ["_SETEC_PHASE_B_SIGNALS"].split(),
    "phase_a_paths": phase_a,
    "phase_b_paths": phase_b,
    "max_entries": int(me) if me else None,
    "bootstrap_engine": os.environ["_SETEC_BOOTSTRAP_ENGINE"],
    "bootstrap_resamples": int(os.environ["_SETEC_BOOTSTRAP_RESAMPLES"]),
    "fpr_target": float(os.environ["_SETEC_FPR_TARGET"]),
    "cooldown_sec": int(os.environ["_SETEC_COOLDOWN_SEC"]),
    "survey_dir": os.environ["_SETEC_BAKEOFF_DIR"],
    "repo_root": os.environ["_SETEC_REPO_ROOT"],
}
with open(os.environ["_SETEC_ARGS_TMP"], "w") as f:
    json.dump(out, f)
PY

python3 "$PROVENANCE_PY" write-provenance "$ARGS_TMP" "$PROVENANCE" || \
    die "provenance write failed"

if [ "$DRY_RUN" = "1" ]; then
    echo
    echo "SETEC_DRY_RUN=1 -- printing matrix plan and exiting without running cells."
    echo
    # The ``${ARR[@]+"${ARR[@]}"}`` idiom expands to nothing when
    # ARR is empty / unset, safely on bash 3.2+ (macOS default) under
    # ``set -u``. A bare ``"${ARR[@]}"`` would trip nounset on empty
    # arrays in bash 4.3 and earlier.
    echo "Phase A cells:"
    for ALIAS in ${PHASE_A_ALIASES[@]+"${PHASE_A_ALIASES[@]}"}; do
        path=$(resolve_path "$PHASE_A_PATHS_JSON" "$ALIAS")
        echo "  $ALIAS  ($path)  signals: ${PHASE_A_SIGNALS[*]}"
    done
    echo "Phase B cells:"
    for ALIAS in ${PHASE_B_ALIASES[@]+"${PHASE_B_ALIASES[@]}"}; do
        path=$(resolve_path "$PHASE_B_PATHS_JSON" "$ALIAS")
        echo "  $ALIAS  ($path)  signals: ${PHASE_B_SIGNALS[*]}"
    done
    echo
    echo "Provenance written to $PROVENANCE"
    exit 0
fi

# ----------------------------------------------------------------- Cell runners

BASE_ARGS=(
    --manifest "$CORPUS_DIR/manifest.jsonl"
    --use validation
    --fpr-target "$FPR_TARGET"
    --bootstrap-engine "$BOOTSTRAP_ENGINE"
    --bootstrap-resamples "$BOOTSTRAP_RESAMPLES"
    --bootstrap-seed "$BOOTSTRAP_SEED"
    --records-cache-flush-every 100
    --json-only
)
# 1.99.0+: propagate comparator_class into every calibration_survey
# call so RAID bake-offs evaluate surprisal_sd under direction='lt'
# rather than the MAGE default 'gt'. Without this, the slicer-side
# auto-default from PR #103 takes effect for the slicer but NOT for
# the per-cell calibration scoring, leaving the cache full of
# verdicts computed under the wrong direction.
if [ -n "$COMPARATOR_CLASS" ]; then
    BASE_ARGS+=(--comparator-class "$COMPARATOR_CLASS")
fi
if [ -n "$MAX_ENTRIES" ]; then
    BASE_ARGS+=(--max-entries "$MAX_ENTRIES" --max-entries-seed "$MAX_ENTRIES_SEED")
fi

mirror_to_runs() {
    # If a per-cell output exists, mirror it to $RUNS_DIR so the
    # framework's calibration-output consumers see it. Idempotent;
    # silent on failure (the source survey JSON is still the source
    # of truth for the matrix).
    local OUT="$1"
    local BASENAME="$2"
    [ -s "$OUT" ] && cp "$OUT" "$RUNS_DIR/$BASENAME" 2>/dev/null || true
}

run_phase_a() {
    local ALIAS=$1
    local PATH_OR_ALIAS
    PATH_OR_ALIAS=$(resolve_path "$PHASE_A_PATHS_JSON" "$ALIAS")
    local CACHE="$BAKEOFF_DIR/cache_phaseA_${ALIAS}.json"
    local OUT="$BAKEOFF_DIR/survey_phaseA_${ALIAS}.json"
    local BASENAME="survey_phaseA_${ALIAS}.json"

    if python3 "$PROVENANCE_PY" check-done "$OUT" 2>/dev/null; then
        echo "[Phase A/${ALIAS}] SKIP (output already present + non-empty)"
        mirror_to_runs "$OUT" "$BASENAME"
        return 0
    fi

    local SIGFLAGS=()
    for s in "${PHASE_A_SIGNALS[@]}"; do SIGFLAGS+=(--signal "$s"); done
    echo
    echo "============================================================"
    echo "Phase A / ${ALIAS}  ('${PATH_OR_ALIAS}')  at $(date +%H:%M:%S)"
    echo "============================================================"
    local T0=$(date +%s)
    python3 "$PLUGIN_ROOT/scripts/calibration/calibration_survey.py" \
        "${BASE_ARGS[@]}" \
        --tier3 --no-tier4 \
        --embedding-model "$PATH_OR_ALIAS" \
        "${SIGFLAGS[@]}" \
        --records-cache "$CACHE" \
        --out "$OUT"
    local RC=$?
    local T1=$(date +%s)
    echo "Phase A/${ALIAS} rc=$RC after $((T1-T0))s"
    mirror_to_runs "$OUT" "$BASENAME"
    return $RC
}

run_phase_b() {
    local ALIAS=$1
    local PATH_OR_ALIAS
    PATH_OR_ALIAS=$(resolve_path "$PHASE_B_PATHS_JSON" "$ALIAS")
    local CACHE="$BAKEOFF_DIR/cache_phaseB_${ALIAS}.json"
    local OUT="$BAKEOFF_DIR/survey_phaseB_${ALIAS}.json"
    local BASENAME="survey_phaseB_${ALIAS}.json"

    if python3 "$PROVENANCE_PY" check-done "$OUT" 2>/dev/null; then
        echo "[Phase B/${ALIAS}] SKIP (output already present + non-empty)"
        mirror_to_runs "$OUT" "$BASENAME"
        return 0
    fi

    local SIGFLAGS=()
    for s in "${PHASE_B_SIGNALS[@]}"; do SIGFLAGS+=(--signal "$s"); done
    echo
    echo "============================================================"
    echo "Phase B / ${ALIAS}  ('${PATH_OR_ALIAS}')  at $(date +%H:%M:%S)"
    echo "============================================================"
    local T0=$(date +%s)
    python3 "$PLUGIN_ROOT/scripts/calibration/calibration_survey.py" \
        "${BASE_ARGS[@]}" \
        --no-tier3 --tier4 \
        --surprisal-model "$PATH_OR_ALIAS" \
        "${SIGFLAGS[@]}" \
        --records-cache "$CACHE" \
        --out "$OUT"
    local RC=$?
    local T1=$(date +%s)
    echo "Phase B/${ALIAS} rc=$RC after $((T1-T0))s"
    mirror_to_runs "$OUT" "$BASENAME"
    return $RC
}

# ----------------------------------------------------------------- Run loops
#
# Accumulate failed cells across both phases. The script DOES NOT
# use ``set -e`` because we want every cell to run even if a
# previous one failed — partial bake-off data is still valuable
# (the slicer handles missing rows). But we MUST surface failures
# in the final exit code; otherwise an operator watching only the
# return code sees ``rc=0`` from a run where five out of seven
# Phase B cells crashed. ``SETEC_ALLOW_PARTIAL=1`` opts back into
# silent partial completion when the operator explicitly accepts
# missing cells (e.g., a known-flaky model that crashes once an
# hour and the operator wants the rest of the matrix anyway).

FAILED_CELLS=()

# The ``${ARR[@]+"${ARR[@]}"}`` idiom expands to nothing when ARR is
# empty / unset, safely on bash 3.2+ (macOS default) under ``set -u``.
# Operators run one-phase bake-offs by passing
# ``SETEC_PHASE_{A,B}_PATHS='{}'`` -- the empty array would otherwise
# trip nounset before the failure-summary block ran.
for ALIAS in ${PHASE_A_ALIASES[@]+"${PHASE_A_ALIASES[@]}"}; do
    if ! run_phase_a "$ALIAS"; then
        FAILED_CELLS+=("Phase A/${ALIAS}")
    fi
    echo "  cooldown ${COOLDOWN_SEC}s..."
    sleep "$COOLDOWN_SEC"
done

for ALIAS in ${PHASE_B_ALIASES[@]+"${PHASE_B_ALIASES[@]}"}; do
    if ! run_phase_b "$ALIAS"; then
        FAILED_CELLS+=("Phase B/${ALIAS}")
    fi
    echo "  cooldown ${COOLDOWN_SEC}s..."
    sleep "$COOLDOWN_SEC"
done

# ----------------------------------------------------------------- Summary

echo
echo "============================================================"
echo "Matrix complete at $(date +%H:%M:%S). Writing summary..."
echo "============================================================"

python3 "$PROVENANCE_PY" summarize "$RUNS_DIR" "$ARGS_TMP" "$SUMMARY" && \
    cat "$SUMMARY"

rm -f "$ARGS_TMP"
echo
if [ ${#FAILED_CELLS[@]} -gt 0 ]; then
    echo "============================================================"
    echo "WARNING: ${#FAILED_CELLS[@]} cell(s) failed:"
    for cell in "${FAILED_CELLS[@]}"; do
        echo "  - $cell"
    done
    echo "============================================================"
    if [ "${SETEC_ALLOW_PARTIAL:-0}" = "1" ]; then
        echo "SETEC_ALLOW_PARTIAL=1 -- treating partial completion as success."
        echo "=== Matrix run complete (partial). Surveys at $RUNS_DIR/, summary at $SUMMARY, provenance at $PROVENANCE ==="
        exit 0
    fi
    echo "Exiting non-zero. Pass SETEC_ALLOW_PARTIAL=1 to opt in to partial completion."
    echo "(Surveys + summary + provenance are still written: $RUNS_DIR/, $SUMMARY, $PROVENANCE)"
    exit 1
fi
echo "=== Matrix run complete. Surveys at $RUNS_DIR/, summary at $SUMMARY, provenance at $PROVENANCE ==="
