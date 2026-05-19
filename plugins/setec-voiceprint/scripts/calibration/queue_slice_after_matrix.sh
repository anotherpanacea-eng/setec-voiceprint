#!/usr/bin/env bash
# queue_slice_after_matrix.sh -- polling-loop driver that chains
# slice_bakeoff_v2.py + polarity_audit.py after bakeoff_matrix.sh.
#
# bakeoff_matrix.sh writes per-cell survey JSONs into $BAKEOFF_DIR as
# each (model x signal) cell completes. This driver watches that
# directory for newly arrived survey_*.json files and, for each one
# it hasn't seen before, runs the slicer (over the whole cache dir,
# since the slicer is cache-dir-scoped) and then the polarity audit
# (over the CSV the slicer just emitted).
#
# Per the roadmap item E.2 spec: "watches $BAKEOFF_DIR/ for completed
# survey_*.json files and triggers slice_bakeoff_v2.py + polarity_audit.py
# automatically." PR #100 (bakeoff_matrix.sh) kept the matrix script
# focused on the matrix proper; this driver is the chaining piece.
#
# === Usage ===
#   queue_slice_after_matrix.sh [WATCH_DIR]
#
# WATCH_DIR defaults to $SETEC_BAKEOFF_DIR if set; otherwise to
# $REPO_ROOT/ai-prose-baselines-private/calibration_runs/. The script
# polls WATCH_DIR every SETEC_QUEUE_POLL_INTERVAL seconds (default 30)
# looking for survey_*.json files that don't yet have a matching
# <survey>.sliced marker.
#
# Operators typically run this in a second terminal while
# bakeoff_matrix.sh runs in the first. With --once it processes the
# current backlog and exits, which is the test-friendly + cron-style
# mode.
#
# === Required env vars (no defaults) ===
#   SETEC_MANIFEST            -- manifest JSONL the matrix scored
#                                against. Passed to slice_bakeoff_v2.py
#                                as --manifest. Same file the operator
#                                passed to bakeoff_matrix.sh via
#                                $SETEC_CORPUS_DIR/manifest.jsonl.
#   SETEC_CORPUS_LABEL        -- "mage" / "raid" / etc. Passed to
#                                slice_bakeoff_v2.py as --corpus and
#                                serves as the comparator class default
#                                (mirroring bakeoff_matrix.sh's
#                                COMPARATOR_CLASS resolution).
#
# === Optional env vars ===
#   SETEC_BAKEOFF_DIR         -- per-cell output dir from
#                                bakeoff_matrix.sh. Used as the watch
#                                dir default + as --cache-dir for the
#                                slicer (the matrix writes both
#                                survey_*.json and cache_*.json there).
#                                If $1 is given, it overrides this for
#                                the watch dir.
#   SETEC_SLICE_OUT_DIR       -- where slice_bakeoff_v2.py writes
#                                slice_analysis.csv + slice_analysis.md
#                                + polarity_audit.json. Default:
#                                $WATCH_DIR/slice_output/.
#   SETEC_POLARITY_OUT_JSON   -- where polarity_audit.py writes its
#                                JSON verdict. Default:
#                                $SETEC_SLICE_OUT_DIR/polarity_audit_standalone.json
#                                (distinct from the slicer-integrated
#                                polarity_audit.json so both can coexist).
#   SETEC_QUEUE_POLL_INTERVAL -- poll interval in seconds (default 30).
#                                Ignored in --once mode.
#   SETEC_QUEUE_ONCE=1        -- equivalent to passing --once. Process
#                                current backlog once and exit.
#   SETEC_SLICE_AUDIT         -- audit mode for slice_bakeoff_v2.py
#                                (currently only 'polarity' supported).
#                                Default: 'polarity' so the
#                                slicer-integrated audit always fires.
#                                Set to empty string to disable.
#   SETEC_SLICE_COMPARATOR_KEY -- --comparator-key for the slicer's
#                                polarity audit. Default: derived from
#                                corpus label ('notes.original_source'
#                                for MAGE, 'notes.domain' for RAID,
#                                unset otherwise).
#   SETEC_COMPARATOR_CLASS    -- per-comparator routing class for the
#                                slicer (1.98.0+). Default: corpus
#                                label when it's 'mage' or 'raid',
#                                else unset (pre-1.98 behavior).
#   SETEC_SLICER_BIN          -- override path to slice_bakeoff_v2.py
#                                (default: sibling in calibration/).
#                                Test hook for swapping in a fake
#                                script; not normally needed.
#   SETEC_POLARITY_BIN        -- override path to polarity_audit.py
#                                (default: sibling in calibration/).
#                                Test hook for swapping in a fake
#                                script; not normally needed.
#
# === CLI flags ===
#   --once                    -- process the current backlog once and
#                                exit 0. Equivalent to SETEC_QUEUE_ONCE=1.
#                                Required for the test suite and useful
#                                for cron-style invocations where the
#                                scheduler owns the polling loop.
#
# === Exit codes ===
#   0    -- backlog processed (--once) or polling-loop terminated by
#           signal in continuous mode.
#   2    -- env validation failure (missing required var, watch dir
#           absent, etc.).
#
# Per-survey processing errors do NOT abort the loop; the script
# logs the failure, leaves the marker file absent so the survey gets
# retried on the next pass, and continues with the rest of the backlog.

set -euo pipefail

# ----------------------------------------------------------------- Helpers

die() { echo "FATAL: $*" >&2; exit 2; }
log() { echo "[queue] $*"; }

# ----------------------------------------------------------------- Paths

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"

SLICER_BIN="${SETEC_SLICER_BIN:-$SCRIPT_DIR/slice_bakeoff_v2.py}"
POLARITY_BIN="${SETEC_POLARITY_BIN:-$SCRIPT_DIR/polarity_audit.py}"

# ----------------------------------------------------------------- CLI

ONCE="${SETEC_QUEUE_ONCE:-0}"
WATCH_DIR_ARG=""
for arg in "$@"; do
    case "$arg" in
        --once)
            ONCE=1
            ;;
        --help|-h)
            sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//' -e '$d'
            exit 0
            ;;
        --*)
            die "unknown flag: $arg (only --once / --help supported)"
            ;;
        *)
            if [ -n "$WATCH_DIR_ARG" ]; then
                die "too many positional args; expected at most one WATCH_DIR"
            fi
            WATCH_DIR_ARG="$arg"
            ;;
    esac
done

# ----------------------------------------------------------------- Watch dir

if [ -n "$WATCH_DIR_ARG" ]; then
    WATCH_DIR="$WATCH_DIR_ARG"
elif [ -n "${SETEC_BAKEOFF_DIR:-}" ]; then
    WATCH_DIR="$SETEC_BAKEOFF_DIR"
else
    WATCH_DIR="$REPO_ROOT/ai-prose-baselines-private/calibration_runs"
fi

[ -d "$WATCH_DIR" ] || die "watch dir does not exist: $WATCH_DIR"

# ----------------------------------------------------------------- Required env

: "${SETEC_MANIFEST:?must set (manifest JSONL the matrix scored against)}"
: "${SETEC_CORPUS_LABEL:?must set (corpus label e.g. mage / raid)}"

[ -f "$SETEC_MANIFEST" ] || die "manifest does not exist: $SETEC_MANIFEST"
[ -f "$SLICER_BIN" ] || die "slicer binary does not exist: $SLICER_BIN"
[ -f "$POLARITY_BIN" ] || die "polarity binary does not exist: $POLARITY_BIN"

# ----------------------------------------------------------------- Optional env

POLL_INTERVAL="${SETEC_QUEUE_POLL_INTERVAL:-30}"
SLICE_OUT_DIR="${SETEC_SLICE_OUT_DIR:-$WATCH_DIR/slice_output}"
POLARITY_OUT_JSON="${SETEC_POLARITY_OUT_JSON:-$SLICE_OUT_DIR/polarity_audit_standalone.json}"
SLICE_AUDIT="${SETEC_SLICE_AUDIT-polarity}"

# Default comparator class from corpus label, mirroring bakeoff_matrix.sh's
# resolution (mage / raid default; everything else stays unset so the
# slicer uses pre-1.98 behavior).
if [ -n "${SETEC_COMPARATOR_CLASS:-}" ]; then
    COMPARATOR_CLASS="$SETEC_COMPARATOR_CLASS"
elif [ "$SETEC_CORPUS_LABEL" = "mage" ] || [ "$SETEC_CORPUS_LABEL" = "raid" ]; then
    COMPARATOR_CLASS="$SETEC_CORPUS_LABEL"
else
    COMPARATOR_CLASS=""
fi

# Default comparator-key from corpus label. MAGE uses notes.original_source,
# RAID uses notes.domain. Other corpora have no inferred default; operators
# pass SETEC_SLICE_COMPARATOR_KEY explicitly if they want the slicer's
# polarity-audit recommendation block populated.
if [ -n "${SETEC_SLICE_COMPARATOR_KEY:-}" ]; then
    COMPARATOR_KEY="$SETEC_SLICE_COMPARATOR_KEY"
elif [ "$SETEC_CORPUS_LABEL" = "mage" ]; then
    COMPARATOR_KEY="notes.original_source"
elif [ "$SETEC_CORPUS_LABEL" = "raid" ]; then
    COMPARATOR_KEY="notes.domain"
else
    COMPARATOR_KEY=""
fi

mkdir -p "$SLICE_OUT_DIR"
mkdir -p "$(dirname "$POLARITY_OUT_JSON")"

# ----------------------------------------------------------------- Banner

log "watch dir:      $WATCH_DIR"
log "manifest:       $SETEC_MANIFEST"
log "corpus:         $SETEC_CORPUS_LABEL"
log "comparator:     ${COMPARATOR_CLASS:-(unset)}  key=${COMPARATOR_KEY:-(unset)}"
log "slice out:      $SLICE_OUT_DIR"
log "polarity out:   $POLARITY_OUT_JSON"
log "slicer bin:     $SLICER_BIN"
log "polarity bin:   $POLARITY_BIN"
log "audit mode:     ${SLICE_AUDIT:-(disabled)}"
if [ "$ONCE" = "1" ]; then
    log "mode:           --once (process backlog and exit)"
else
    log "mode:           polling every ${POLL_INTERVAL}s (Ctrl-C to stop)"
fi

# ----------------------------------------------------------------- Slicer call

run_slicer() {
    # Re-runs the slicer over the entire cache dir. Slicer is whole-dir-
    # scoped (it reads every cache_phase[AB]_*.json under --cache-dir),
    # so per-survey invocation isn't meaningful -- one slicer run produces
    # one slice_analysis.csv that covers every cache present.
    local slice_args=(
        --corpus "$SETEC_CORPUS_LABEL"
        --cache-dir "$WATCH_DIR"
        --manifest "$SETEC_MANIFEST"
        --out-dir "$SLICE_OUT_DIR"
    )
    if [ -n "$SLICE_AUDIT" ]; then
        slice_args+=(--audit "$SLICE_AUDIT")
    fi
    if [ -n "$COMPARATOR_KEY" ]; then
        slice_args+=(--comparator-key "$COMPARATOR_KEY")
    fi
    if [ -n "$COMPARATOR_CLASS" ]; then
        slice_args+=(--comparator-class "$COMPARATOR_CLASS")
    fi
    python3 "$SLICER_BIN" "${slice_args[@]}"
}

run_polarity() {
    # The standalone polarity audit consumes the CSV the slicer just wrote.
    # The slicer's --audit polarity mode produces an integrated polarity_audit.json
    # on its own; this standalone run is the additional verdict the operator
    # would otherwise produce manually. Both outputs coexist (different
    # filenames) so the operator has both the integrated + standalone shapes.
    local csv="$SLICE_OUT_DIR/slice_analysis.csv"
    if [ ! -f "$csv" ]; then
        echo "[queue]   skip polarity_audit: $csv not present" >&2
        return 1
    fi
    local pol_args=(
        --input-csv "$csv"
        --out-json "$POLARITY_OUT_JSON"
    )
    if [ -n "$COMPARATOR_KEY" ]; then
        pol_args+=(--comparator-key "$COMPARATOR_KEY")
    fi
    python3 "$POLARITY_BIN" "${pol_args[@]}"
}

# ----------------------------------------------------------------- Backlog pass

process_backlog() {
    # One backlog pass: list every survey_*.json under WATCH_DIR, partition
    # into already-processed (marker file present) vs new (no marker), and
    # if any new ones are present run slicer + polarity_audit once over the
    # whole cache dir. Marker files are written per-survey on success so
    # subsequent passes correctly skip already-processed surveys even if
    # the slicer / polarity scripts fail partway through.
    #
    # Returns 0 always (per-survey errors are logged, not propagated, so a
    # single bad survey doesn't kill the polling loop).
    local new_surveys=()
    # Use nullglob-style guard via find so an empty dir doesn't expand to
    # a literal pattern. find ... -print0 + read -d '' handles paths with
    # spaces safely.
    local survey
    while IFS= read -r -d '' survey; do
        if [ -f "${survey}.sliced" ] && [ -f "${survey}.polarity" ]; then
            continue
        fi
        new_surveys+=("$survey")
    done < <(find "$WATCH_DIR" -maxdepth 1 -type f -name 'survey_*.json' -print0 2>/dev/null)

    if [ "${#new_surveys[@]}" -eq 0 ]; then
        return 0
    fi

    log "Found ${#new_surveys[@]} new survey(s); processing..."
    for s in "${new_surveys[@]}"; do
        log "  - $(basename "$s")"
    done

    # The slicer runs once over the whole cache dir, not per-survey.
    # If it fails, leave all markers absent so the whole batch retries
    # next pass.
    local slicer_rc=0
    run_slicer || slicer_rc=$?
    if [ "$slicer_rc" -ne 0 ]; then
        log "slice_bakeoff_v2 failed (rc=$slicer_rc); leaving markers absent for retry"
        return 0
    fi
    log "slice_bakeoff_v2 ok"
    for s in "${new_surveys[@]}"; do
        : > "${s}.sliced"
    done

    local pol_rc=0
    run_polarity || pol_rc=$?
    if [ "$pol_rc" -ne 0 ]; then
        log "polarity_audit failed (rc=$pol_rc); .polarity markers withheld for retry"
        return 0
    fi
    log "polarity_audit ok"
    for s in "${new_surveys[@]}"; do
        : > "${s}.polarity"
    done

    return 0
}

# ----------------------------------------------------------------- Main loop

if [ "$ONCE" = "1" ]; then
    process_backlog
    log "--once complete; exiting"
    exit 0
fi

# Continuous polling. Operator stops with Ctrl-C; SIGINT lands as exit 130
# under bash defaults which is the conventional signal-terminated status.
while true; do
    process_backlog
    sleep "$POLL_INTERVAL"
done
