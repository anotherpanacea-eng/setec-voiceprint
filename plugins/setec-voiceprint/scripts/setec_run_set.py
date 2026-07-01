#!/usr/bin/env python3
"""setec_run_set.py — multi-surface run-set runner for the decision loop.

Executes a named set of surfaces over ONE target, collects their
schema_version 1.0 envelopes into a run folder, feeds them to the existing
``surface_disagreement_resolver`` (imported, unchanged), and emits the
disagreement patterns plus a mechanical next-action block. **No composite
score. No verdict. Ever** — enforced at emit time by
:func:`assert_no_aggregate_verdict` (see below).

This is an operator-side SIBLING of ``setec_run.py``, not an extension of
it: the dispatcher is the pinned R2 consumer contract (it owns exactly one
consumer flag, ``--json``) and is consumed by apodictic + setec-voicewright
under version-pinned drift gates. The runner is NOT a consumer surface in
M1: its manifest fragment carries ``handoff: none`` and no ``json_delivery``
/ ``min_setec_version``, so it is invisible to ``setec_run.py --list`` and
has zero drift-gate impact downstream.

Usage::

    python3 setec_run_set.py --set full_picture --target draft.md \\
        [--baseline-dir baselines/blog-essay/] \\
        [--attach general_imposters=out/gi.json] \\
        [--attach idiolect_detector=out/idiolect.json] \\
        [--ai-status ai_edited] [--out-dir setec-run-sets/run1/] \\
        [--resume] [--json]
    python3 setec_run_set.py --list-sets
    python3 setec_run_set.py --situation "did an LLM smooth this essay?"

Key behaviors:

  * **Fixed argv projection.** Member scripts are exec'd directly from the
    manifest's ``script_path`` with argv built from a fixed projection
    table — no argparse-prefix guessing (the defect class the dispatcher
    was built to kill; APODICTIC PR #6).
  * **Partial success is normal.** A member failing on
    ``missing_dependency`` / ``bad_input`` is recorded as an R3 member
    envelope and the run continues; the resolver degrades gracefully.
  * **Attach-only members.** ``general_imposters`` and
    ``idiolect_detector`` need comparator corpora the runner has no args
    for (idiolect has no single-file target mode), so they join only via
    ``--attach <id>=<path>`` — operator-supplied envelopes honored
    verbatim, never executed.
  * **Belt / suspenders / buttons** (AGENTS.md §Long-running): the
    per-member ``envelopes/<id>.json`` files are the checkpoint (belt);
    a per-member stderr progress line (suspenders); ``--resume`` reuses
    completed member envelopes (buttons). ``--resume`` retries members
    whose stored envelope is an ``available: false`` error record, so a
    rerun with the previously-missing flag/attach actually runs them.
  * **Envelope→results unwrap.** The resolver consumes RAW report dicts
    (``variance["compression"]["band"]``), while the collected member
    files are schema-1.0 envelopes that nest that under ``results``. The
    runner unwraps ``envelope["results"]`` before calling ``resolve()`` —
    zero changes to ``surface_disagreement_resolver.py``.

Anti-Goodhart posture (the centerpiece): the runner-authored subtrees of
the combined envelope are walked AT EMIT TIME, on every real run, by
``assert_no_aggregate_verdict`` — a recursive banned-key walk (mirroring
``within_doc_segmentation.assert_no_authorship``, the runtime precedent;
test-side precedent in ``test_distinct_diversity_audit.py``) extended with
a numeric-leaf no-reduction check: any float leaf, or any int leaf outside
whitelisted ``n_*`` counts, trips the guard — a composite score cannot
exist even under an unbanned name. Pass-through member envelopes are
exempt from the walk (their own surfaces run their own guards) but are
covered by a JSON-identity check against the run-folder files instead.
Posture prose: ``corpus_novelty_audit.py`` ("a lone scalar is a verdict in
disguise").
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import capabilities  # type: ignore  # noqa: E402
import setec_run  # type: ignore  # noqa: E402
import surface_disagreement_resolver as resolver_mod  # type: ignore  # noqa: E402
from output_schema import (  # type: ignore  # noqa: E402
    REASON_CATEGORIES,
    OutputValidityError,
    build_error_output,
    build_output,
)

TASK_SURFACE = "validation"
TOOL_NAME = "setec_run_set"
SCRIPT_VERSION = "1.0"

# Exit-code scheme mirrors the dispatcher's R3 scheme (setec_run.py §4):
# 0 = run completed and the resolver produced a report (partial member
# failure is NORMAL); 2 = discovery (unknown set name / unknown surface id
# in --surfaces / --attach); 3 = contract/usage; 1 = internal (including a
# tripped aggregate-verdict guard). On 2/3/1 the runner still emits a
# build_error_output() envelope so a consumer branches on reason_category.
EXIT_OK = setec_run.EXIT_OK
EXIT_INTERNAL = setec_run.EXIT_INTERNAL
EXIT_DISCOVERY = setec_run.EXIT_DISCOVERY
EXIT_CONTRACT = setec_run.EXIT_CONTRACT

_SCRIPTS_REL = "plugins/setec-voiceprint/scripts"


# ---------- presets --------------------------------------------------

RUN_SETS: dict[str, tuple[str, ...]] = {
    # target-only; core/spaCy tier; every id verified in capabilities.d/
    # (a CI test pins every preset id to an existing fragment).
    "smoothing_core": (
        "variance_audit", "paragraph_audit", "aic_pattern_audit",
        "discourse_move_signature", "agency_abstraction_audit",
    ),
    # adds the comparator surfaces; voice_distance runs only with
    # --baseline-dir; general_imposters + idiolect_detector are attach-only
    # and listed here so the report names them as expected-but-absent when
    # not attached.
    "full_picture": (
        "variance_audit", "paragraph_audit", "aic_pattern_audit",
        "discourse_move_signature", "agency_abstraction_audit",
        "voice_distance", "general_imposters", "idiolect_detector",
    ),
}

# Members that can NEVER be executed by the runner: general_imposters needs
# a candidate + impostor-pool manifest; idiolect_detector has no
# single-file target mode (its target group is --target-dir | --manifest).
# Both join via --attach only.
ATTACH_ONLY: frozenset[str] = frozenset({
    "general_imposters", "idiolect_detector",
})

# The eight resolver inputs — the runner's CLOSED member universe. Every
# --surfaces / --attach / preset id must be one of these; anything else is
# refused (this is also what keeps the voice-clone privacy surfaces
# pov_voice_profile / voice_profile out of the decision loop by
# construction — they are not resolver inputs, so they cannot enter the
# run folder at all). Values are the resolve() kwarg each surface feeds.
KWARG_MAP: dict[str, str] = {
    "variance_audit": "variance",
    "voice_distance": "voice_distance",
    "general_imposters": "gi",
    "paragraph_audit": "paragraph",
    "discourse_move_signature": "discourse",
    "agency_abstraction_audit": "agency",
    "aic_pattern_audit": "aic",
    "idiolect_detector": "idiolect",
}

# The inverse of KWARG_MAP at the READING level: which surface populates
# each resolver reading (used by next_action.unknown_readings).
READING_TO_SURFACE: dict[str, str] = {
    "smoothing": "variance_audit",
    "pos_bigram_kl": "variance_audit",
    "voice_drift": "voice_distance",
    "gi_decision": "general_imposters",
    "aic_density": "aic_pattern_audit",
    "paragraph": "paragraph_audit",
    "discourse": "discourse_move_signature",
    "agency": "agency_abstraction_audit",
    "idiolect_survival": "idiolect_detector",
}

# The PRIMARY reading per member — the §4.4 sanity tripwire fires when a
# member's envelope was available but its primary reading came back
# unknown (a shape drift between that surface's results and the resolver's
# reader — today's silent failure mode). pos_bigram_kl is variance_audit's
# SECONDARY reading (legitimately unknown without a baseline) and is
# excluded from the tripwire.
MEMBER_PRIMARY_READING: dict[str, str] = {
    "variance_audit": "smoothing",
    "voice_distance": "voice_drift",
    "general_imposters": "gi_decision",
    "aic_pattern_audit": "aic_density",
    "paragraph_audit": "paragraph",
    "discourse_move_signature": "discourse",
    "agency_abstraction_audit": "agency",
    "idiolect_detector": "idiolect_survival",
}

# §4.3 mechanical attach validation: an attached file is accepted iff it
# parses as a JSON object AND either (a) has schema_version + results (a
# schema-1.0 envelope) or (b) has the surface's required top-level reading
# key(s) — a legacy raw report. The required keys are exactly the keys the
# resolver's readers consume (any ONE of the listed alternatives).
ATTACH_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "variance_audit": ("compression",),
    "voice_distance": ("overall",),
    "general_imposters": ("decision",),
    "idiolect_detector": ("preservation_list",),
    "paragraph_audit": ("compression",),
    "discourse_move_signature": ("compression",),
    "agency_abstraction_audit": ("compression",),
    # _read_aic_density reads patterns.<key>.density_per_1k with a legacy
    # top-level pattern_densities fallback — either key is a valid raw
    # report shape.
    "aic_pattern_audit": ("patterns", "pattern_densities"),
}


# ---------- argv projection (§4.1) -----------------------------------
#
# Fixed table; the tests assert exact argv. All six executable members are
# positional-first (positional name variance — `input` vs `target` — is
# irrelevant at exec time); voice_distance REQUIRES --baseline-dir (its
# argparse errors otherwise), so without one it is SKIPPED with a
# synthesized bad_input record, never argparse-crashed.

def member_argv(
    surface_id: str,
    target: str,
    baseline_dir: str | None,
) -> list[str] | None:
    """Return the exact argv (after the script path) for an executable
    member, or ``None`` when the member cannot run with the supplied
    inputs (voice_distance without --baseline-dir)."""
    if surface_id == "voice_distance":
        if not baseline_dir:
            return None
        return [target, "--baseline-dir", baseline_dir, "--json"]
    argv = [target, "--json"]
    if baseline_dir:
        argv += ["--baseline-dir", baseline_dir]
    return argv


# ---------- the mechanical anti-Goodhart gate (§6.4) ------------------
#
# Mirrors within_doc_segmentation.assert_no_authorship (the RUNTIME
# firewall precedent — checks run on every real emit, not only in tests;
# within_doc_segmentation.py:111-158). Test-side precedent:
# test_distinct_diversity_audit.py's _FORBIDDEN_KEYS + _walk_keys. Posture
# prose: corpus_novelty_audit.py:7 ("a lone scalar is a verdict in
# disguise"). NOT voice_verifier.py (it carries no such walk).

FORBIDDEN_AGGREGATE_KEYS: frozenset[str] = frozenset({
    "is_ai", "is_human", "verdict", "label", "score", "composite",
    "composite_score", "overall_score", "p_ai", "probability_ai",
    "confidence", "rating", "grade",
})
FORBIDDEN_AGGREGATE_SUBSTRINGS: tuple[str, ...] = ("verdict", "composite")


class AggregateVerdictError(RuntimeError):
    """Raised when a runner-authored subtree carries a verdict-shaped key
    or a reductive numeric leaf (the architected-against violation)."""


def assert_no_aggregate_verdict(runner_authored: Any, _key: str = "") -> None:
    """Recursive banned-key walk + numeric-leaf no-reduction check over a
    RUNNER-AUTHORED subtree. Rules:

    1. Any dict KEY in ``FORBIDDEN_AGGREGATE_KEYS`` (exact, case-folded)
       at any depth raises.
    2. Any dict KEY containing a ``FORBIDDEN_AGGREGATE_SUBSTRINGS`` token
       (case-folded, KEY-only) raises.
    3. No-reduction invariant: ANY float leaf raises; any int leaf whose
       key does not start with ``n_`` (a whitelisted count) raises. A
       composite score cannot exist even under an unbanned name. bools
       are skipped (never a metric).

    Called at emit time on the real output dict, every run — the
    pass-through member envelopes are exempt from this walk (their own
    surfaces enforce their own guards; e.g. voice_distance legitimately
    carries ``weighted_delta``) and are covered by the JSON-identity
    pass-through check instead.
    """
    if isinstance(runner_authored, bool):
        return
    if isinstance(runner_authored, float):
        raise AggregateVerdictError(
            f"no-reduction invariant: float leaf {runner_authored!r} at "
            f"key {_key!r} in a runner-authored subtree (the runner never "
            f"computes a number over member envelopes)"
        )
    if isinstance(runner_authored, int):
        if not _key.lower().startswith("n_"):
            raise AggregateVerdictError(
                f"no-reduction invariant: int leaf {runner_authored!r} at "
                f"non-count key {_key!r} in a runner-authored subtree "
                f"(only n_* counts are licensed)"
            )
        return
    if isinstance(runner_authored, dict):
        for k, v in runner_authored.items():
            k_lower = str(k).lower()
            if k_lower in FORBIDDEN_AGGREGATE_KEYS:
                raise AggregateVerdictError(
                    f"forbidden aggregate key {k!r} in a runner-authored "
                    f"subtree (no composite score, no verdict — ever)"
                )
            for sub in FORBIDDEN_AGGREGATE_SUBSTRINGS:
                if sub in k_lower:
                    raise AggregateVerdictError(
                        f"key {k!r} contains forbidden substring {sub!r} "
                        f"in a runner-authored subtree"
                    )
            assert_no_aggregate_verdict(v, str(k))
        return
    if isinstance(runner_authored, (list, tuple)):
        for item in runner_authored:
            assert_no_aggregate_verdict(item, _key)
        return
    # str / None: nothing to check.


def _guard_results(results: dict[str, Any]) -> None:
    """Apply the emit-time guard to the combined envelope's ``results``:
    every runner-authored subtree (run_set / disagreement / next_action)
    plus the top-level results key set. ``results.envelopes`` values are
    the pass-through exemption — the key NAME is still checked."""
    assert_no_aggregate_verdict({**results, "envelopes": None})


# ---------- small helpers ---------------------------------------------

def _emit(envelope: dict[str, Any]) -> None:
    print(json.dumps(envelope, indent=2, default=str))


def _error(
    reason: str,
    reason_category: str,
    exit_code: int,
    *,
    extra: dict[str, Any] | None = None,
) -> int:
    """Emit an R3 error envelope to stdout and return ``exit_code``."""
    envelope = build_error_output(
        task_surface=TASK_SURFACE,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        reason=reason,
        reason_category=reason_category,
        extra=extra,
    )
    _emit(envelope)
    return exit_code


def _member_error_envelope(
    surface_id: str,
    reason: str,
    reason_category: str,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synthesize an R3 member envelope (written to envelopes/<id>.json)."""
    merged: dict[str, Any] = {"surface": surface_id}
    if extra:
        merged.update(extra)
    return build_error_output(
        task_surface=None,
        tool=TOOL_NAME,
        version=SCRIPT_VERSION,
        reason=reason,
        reason_category=reason_category,
        extra=merged,
    )


def _run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Thin monkeypatchable indirection over the dispatcher's runner
    (inherits its CWD semantics: input paths resolve against the
    operator's CWD, the script is invoked by absolute path)."""
    return setec_run._run_subprocess(cmd)


def _classify_script_failure(
    surface_id: str,
    proc: subprocess.CompletedProcess[str],
) -> tuple[str, str]:
    """Reuse setec_run._wrap_script_failure's classification BY IMPORT
    (not copy): it emits an R3 envelope to stdout and returns an exit
    code, so capture the emission and lift reason + reason_category out
    of it. Keeps the exit-2/argparse/policy disambiguation in exactly one
    place."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        setec_run._wrap_script_failure(surface_id, proc)
    wrapped = json.loads(buf.getvalue())
    return wrapped["reason"], wrapped["reason_category"]


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_live_manifest() -> dict[str, Any]:
    """Monkeypatchable manifest loader (tests inject a fake manifest)."""
    return capabilities.load_manifest()


def _is_envelope_shaped(doc: Any) -> bool:
    """§4.4 unwrap test: a dict with ``schema_version`` + ``results``."""
    return (
        isinstance(doc, dict)
        and "schema_version" in doc
        and "results" in doc
    )


def _payload_for_resolver(doc: dict[str, Any]) -> Any:
    """Unwrap an envelope to its ``results`` for resolve(); pass a legacy
    raw report through as-is (the resolver's readers tolerate both)."""
    if _is_envelope_shaped(doc):
        return doc.get("results")
    return doc


# ---------- member records ---------------------------------------------

class _MemberRecord:
    """Mutable per-member bookkeeping; projected into run_meta.json (full)
    and results.run_set.member_records (envelope subset — counts only, no
    aggregate numeric of any kind)."""

    def __init__(self, surface_id: str, compute_tier: str | None) -> None:
        self.surface_id = surface_id
        self.disposition = "skipped"  # executed | attached | skipped
        self.available = False
        self.reason_category: str | None = None
        self.reason: str | None = None
        self.compute_tier = compute_tier
        self.envelope_path: str | None = None
        self.envelope_sha256: str | None = None
        self.argv: list[str] | None = None
        self.exit: int | None = None
        self.missing_deps: list[str] = []
        self.envelope: dict[str, Any] | None = None

    def to_envelope_record(self) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "surface_id": self.surface_id,
            "disposition": self.disposition,
            "available": self.available,
            "compute_tier": self.compute_tier,
            "envelope_path": self.envelope_path,
            "envelope_sha256": self.envelope_sha256,
        }
        if self.reason_category is not None:
            rec["reason_category"] = self.reason_category
        if self.reason is not None:
            rec["reason"] = self.reason
        return rec

    def to_meta_record(self) -> dict[str, Any]:
        rec = self.to_envelope_record()
        rec["argv"] = self.argv
        rec["exit"] = self.exit
        return rec


def _write_member_envelope(
    envelopes_dir: Path,
    record: _MemberRecord,
    envelope: dict[str, Any],
) -> None:
    """Checkpoint a member envelope to disk (belt) and finish the record."""
    path = envelopes_dir / f"{record.surface_id}.json"
    path.write_text(
        json.dumps(envelope, indent=2, default=str) + "\n", encoding="utf-8",
    )
    record.envelope = envelope
    record.envelope_path = f"envelopes/{record.surface_id}.json"
    record.envelope_sha256 = _sha256_file(path)


def _copy_attach_verbatim(
    envelopes_dir: Path,
    record: _MemberRecord,
    attach_path: Path,
    parsed: dict[str, Any],
) -> None:
    """Copy an accepted attach byte-for-byte into the run folder."""
    dest = envelopes_dir / f"{record.surface_id}.json"
    shutil.copyfile(attach_path, dest)
    record.envelope = parsed
    record.envelope_path = f"envelopes/{record.surface_id}.json"
    record.envelope_sha256 = _sha256_file(dest)


def _progress(line: str) -> None:
    sys.stderr.write(f"[setec_run_set] {line}\n")


# ---------- member processing ------------------------------------------

def _process_attach(
    record: _MemberRecord,
    attach_path: Path,
    envelopes_dir: Path,
    regenerate_cmd: str,
) -> None:
    """§4.3 mechanical attach validation — accept/reject, no heuristics."""
    sid = record.surface_id
    record.disposition = "attached"
    try:
        parsed = json.loads(attach_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        record.disposition = "skipped"
        record.reason_category = "bad_input"
        record.reason = (
            f"attached file {attach_path} is not parseable JSON ({exc}); "
            f"regenerate it: {regenerate_cmd}"
        )
        _write_member_envelope(
            envelopes_dir, record,
            _member_error_envelope(sid, record.reason, "bad_input"),
        )
        return
    required = ATTACH_REQUIRED_KEYS[sid]
    if not isinstance(parsed, dict) or not (
        _is_envelope_shaped(parsed)
        or any(k in parsed for k in required)
    ):
        record.disposition = "skipped"
        record.reason_category = "bad_input"
        record.reason = (
            f"attached file {attach_path} is neither a schema-1.0 envelope "
            f"(schema_version + results) nor a legacy raw report carrying "
            f"one of the required key(s) {list(required)}; regenerate it: "
            f"{regenerate_cmd}"
        )
        _write_member_envelope(
            envelopes_dir, record,
            _member_error_envelope(sid, record.reason, "bad_input"),
        )
        return
    # Accepted: copied verbatim; the "attached" marker lives in the member
    # record / run_meta.json, NEVER inside the envelope itself
    # (pass-through purity, §6.4).
    _copy_attach_verbatim(envelopes_dir, record, attach_path, parsed)
    if _is_envelope_shaped(parsed) and parsed.get("available") is False:
        # An attached R3 refusal envelope is honored as a failed member:
        # excluded from resolve() (feeding {} would read all-unknown),
        # its own reason_category carried forward. A missing or
        # unrecognized category is sanitized to bad_input so the run-level
        # modal-category exit path only ever sees the R3 enum.
        record.available = False
        category = parsed.get("reason_category")
        if category not in REASON_CATEGORIES:
            category = "bad_input"
        record.reason_category = category
        record.reason = parsed.get("reason") or (
            "attached envelope carries available: false"
        )
    else:
        record.available = True


def _process_exec(
    record: _MemberRecord,
    entry: dict[str, Any],
    target: str,
    baseline_dir: str | None,
    envelopes_dir: Path,
) -> None:
    """Increment-1 execution path: dep pre-check → exec by script_path
    with the fixed argv projection → envelope recovery → checkpoint."""
    sid = record.surface_id

    # (1) Dependency pre-check (capabilities.entry_available) — missing
    # required deps synthesize an R3 member envelope and the run CONTINUES.
    ok, missing_required, _missing_opt = capabilities.entry_available(entry)
    if not ok:
        record.reason_category = "missing_dependency"
        record.missing_deps = list(missing_required)
        record.reason = (
            f"{sid} requires Python module(s) not installed: "
            f"{', '.join(missing_required)}. Install them and retry."
        )
        _write_member_envelope(
            envelopes_dir, record,
            _member_error_envelope(
                sid, record.reason, "missing_dependency",
                extra={"missing_dependency": {"python": missing_required}},
            ),
        )
        return

    # (2) Fixed argv projection.
    argv = member_argv(sid, target, baseline_dir)
    if argv is None:
        record.reason_category = "bad_input"
        record.reason = (
            f"{sid} requires --baseline-dir (its CLI errors without a "
            f"--baseline-dir or --manifest comparator); pass --baseline-dir "
            f"to include it in the run"
        )
        _write_member_envelope(
            envelopes_dir, record,
            _member_error_envelope(sid, record.reason, "bad_input"),
        )
        return

    script = setec_run._script_abspath(entry)
    cmd = [sys.executable, str(script), *argv]
    record.argv = argv
    record.disposition = "executed"
    proc = _run_subprocess(cmd)
    record.exit = proc.returncode

    if proc.returncode != 0:
        reason, category = _classify_script_failure(sid, proc)
        record.reason_category = category
        record.reason = reason
        _write_member_envelope(
            envelopes_dir, record,
            _member_error_envelope(sid, reason, category),
        )
        return

    envelope = setec_run._extract_envelope(proc.stdout)
    if envelope is None:
        record.reason_category = "internal_error"
        record.reason = (
            f"{sid}: script exited 0 but stdout carried no parseable "
            f"schema_version envelope; stderr: {(proc.stderr or '').strip()}"
        )
        _write_member_envelope(
            envelopes_dir, record,
            _member_error_envelope(sid, record.reason, "internal_error"),
        )
        return

    if envelope.get("available") is False:
        # A script-emitted structured refusal is honored verbatim (same
        # posture as the dispatcher); a missing/unrecognized category is a
        # contract bug → synthesized internal_error.
        category = envelope.get("reason_category")
        if category in setec_run._CATEGORY_DEFAULT_EXIT:
            record.reason_category = category
            record.reason = envelope.get("reason")
            _write_member_envelope(envelopes_dir, record, envelope)
        else:
            record.reason_category = "internal_error"
            record.reason = (
                f"{sid}: emitted an available:false envelope with a "
                f"missing/unrecognized reason_category ({category!r})"
            )
            _write_member_envelope(
                envelopes_dir, record,
                _member_error_envelope(sid, record.reason, "internal_error"),
            )
        return

    record.available = True
    _write_member_envelope(envelopes_dir, record, envelope)


def _try_resume(
    record: _MemberRecord,
    envelopes_dir: Path,
) -> bool:
    """§4.5 buttons: reuse an existing envelopes/<id>.json that parses as
    a schema-1.0 envelope — UNLESS it is an ``available: false`` error
    record (those are retried, so a rerun with the previously-missing
    dep / flag / attach actually runs the member)."""
    path = envelopes_dir / f"{record.surface_id}.json"
    if not path.is_file():
        return False
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not setec_run._is_envelope(parsed):
        return False
    if parsed.get("available") is False:
        return False
    record.disposition = "skipped"
    record.available = True
    record.reason = "reused existing envelope (--resume)"
    record.envelope = parsed
    record.envelope_path = f"envelopes/{record.surface_id}.json"
    record.envelope_sha256 = _sha256_file(path)
    return True


# ---------- next-action block (§6.3 — all-mechanical) -------------------

def _producer_command(
    surface_id: str,
    target: str,
    baseline_dir: str | None,
) -> str:
    """The standalone command that produces a surface's JSON (mechanical
    template; <angle-bracket> slots are operator-supplied comparators —
    the runner supplies no default comparator, ever)."""
    if surface_id == "general_imposters":
        return (
            f"python3 {_SCRIPTS_REL}/setec_run.py general_imposters "
            f"--target {target} --manifest <impostor-manifest.jsonl> "
            f"--candidate-persona <candidate-persona> --json "
            f"> general_imposters.json"
        )
    if surface_id == "idiolect_detector":
        return (
            f"python3 {_SCRIPTS_REL}/setec_run.py idiolect_detector "
            f"--manifest <corpus-manifest.jsonl> --json "
            f"> idiolect_detector.json"
        )
    if surface_id == "voice_distance":
        bdir = baseline_dir or "<baseline-dir>"
        return (
            f"python3 {_SCRIPTS_REL}/voice_distance.py {target} "
            f"--baseline-dir {bdir} --json > voice_distance.json"
        )
    entry_script = surface_id  # script module name == surface id here
    suffix = f" --baseline-dir {baseline_dir}" if baseline_dir else ""
    return (
        f"python3 {_SCRIPTS_REL}/{entry_script}.py {target} --json"
        f"{suffix} > {surface_id}.json"
    )


def _build_next_action(
    *,
    report: dict[str, Any],
    records: list[_MemberRecord],
    tripwires: list[dict[str, Any]],
    target: str,
    baseline_dir: str | None,
    out_dir: Path,
    set_label: str,
    attaches: dict[str, str],
) -> dict[str, Any]:
    """All-mechanical: commands, not judgments. Every entry is a
    condition-triggered command string whose trigger is set membership or
    a reason_category, never a value comparison. No priority ordering, no
    recommendation strength, no thresholds."""
    readings: dict[str, str] = report.get("readings", {})

    unknown_readings: list[dict[str, Any]] = []
    for reading, value in readings.items():
        if value != "unknown":
            continue
        sid = READING_TO_SURFACE[reading]
        entry: dict[str, Any] = {
            "reading": reading,
            "populating_surface": sid,
            "command": _producer_command(sid, target, baseline_dir),
        }
        if reading == "pos_bigram_kl":
            entry["command"] = _producer_command(
                sid, target, baseline_dir or "<baseline-dir>",
            )
        unknown_readings.append(entry)

    unavailable_members: list[dict[str, Any]] = []
    for rec in records:
        if rec.reason_category is None or rec.available:
            continue
        if rec.reason_category == "missing_dependency":
            unlock = (
                f"pip install {' '.join(rec.missing_deps)}"
                if rec.missing_deps else "install the missing dependency"
            )
        elif rec.reason_category == "bad_input":
            if rec.surface_id in ATTACH_ONLY:
                unlock = (
                    f"--attach {rec.surface_id}=<path>  # produce it: "
                    f"{_producer_command(rec.surface_id, target, baseline_dir)}"
                )
            elif rec.surface_id == "voice_distance" and not baseline_dir:
                unlock = "--baseline-dir <dir>"
            else:
                unlock = (
                    f"regenerate: "
                    f"{_producer_command(rec.surface_id, target, baseline_dir)}"
                )
        else:
            unlock = (
                f"inspect {rec.envelope_path or rec.surface_id} and the "
                f"member's stderr"
            )
        unavailable_members.append({
            "surface_id": rec.surface_id,
            "reason_category": rec.reason_category,
            "unlock": unlock,
        })
    # §4.4 sanity tripwire entries: available-but-unknown readings (shape
    # drift between a surface's results and the resolver's reader). The
    # envelope is the record of truth — stderr only mirrors these.
    unavailable_members.extend(tripwires)

    # restoration handoff — a set-membership condition, not a threshold:
    # emitted whenever >= 1 of variance/voice/idiolect/aic envelopes is
    # available.
    resto_flags = {
        "variance_audit": "--variance-json",
        "voice_distance": "--voice-json",
        "idiolect_detector": "--idiolect-json",
        "aic_pattern_audit": "--aic-json",
    }
    available_resto = [
        rec for rec in records
        if rec.available and rec.surface_id in resto_flags
    ]
    restoration_handoff: dict[str, Any] | None = None
    if available_resto:
        packet_parts = [f"python3 {_SCRIPTS_REL}/restoration_packet.py"]
        for rec in available_resto:
            packet_parts.append(
                f"{resto_flags[rec.surface_id]} "
                f"{out_dir / 'envelopes' / (rec.surface_id + '.json')}"
            )
        # before_after_restoration has --before/--after pairs for
        # variance / bigram / voice / idiolect (NO aic pair — verified
        # against its argparse), so aic feeds the packet only.
        ba_flags = {
            "variance_audit": "variance",
            "voice_distance": "voice",
            "idiolect_detector": "idiolect",
        }
        ba_parts = [f"python3 {_SCRIPTS_REL}/before_after_restoration.py"]
        for rec in available_resto:
            stem = ba_flags.get(rec.surface_id)
            if stem is None:
                continue
            ba_parts.append(
                f"--before-{stem}-json "
                f"{out_dir / 'envelopes' / (rec.surface_id + '.json')}"
            )
            ba_parts.append(f"--after-{stem}-json <rerun>")
        restoration_handoff = {
            "restoration_packet": " ".join(packet_parts),
            "before_after_restoration": " ".join(ba_parts),
        }

    rerun_parts = [
        f"python3 {_SCRIPTS_REL}/setec_run_set.py",
        set_label,
        f"--target {target}",
    ]
    if baseline_dir:
        rerun_parts.append(f"--baseline-dir {baseline_dir}")
    for sid, path in sorted(attaches.items()):
        rerun_parts.append(f"--attach {sid}={path}")
    for rec in records:
        if (
            rec.surface_id in ATTACH_ONLY
            and rec.surface_id not in attaches
            and not rec.available
        ):
            rerun_parts.append(f"--attach {rec.surface_id}=<path>")
    rerun_parts.append(f"--out-dir {out_dir}")
    rerun_parts.append("--resume")

    return {
        "unknown_readings": unknown_readings,
        "unavailable_members": unavailable_members,
        "restoration_handoff": restoration_handoff,
        "rerun": " ".join(rerun_parts),
    }


# ---------- report rendering --------------------------------------------

def _render_report_md(
    *,
    set_name: str,
    target: str,
    records: list[_MemberRecord],
    report: dict[str, Any],
    next_action: dict[str, Any],
) -> str:
    lines: list[str] = [
        "# SETEC run-set report",
        "",
        f"**Run set:** `{set_name}`",
        f"**Target:** `{target}`",
        f"**Tool:** `{TOOL_NAME}` v{SCRIPT_VERSION}",
        "",
        "## Members",
        "",
        "| surface | disposition | available | reason_category |",
        "|---|---|---|---|",
    ]
    for rec in records:
        lines.append(
            f"| {rec.surface_id} | {rec.disposition} | "
            f"{str(rec.available).lower()} | {rec.reason_category or ''} |"
        )
    lines.append("")
    # The resolver's own rendering, verbatim (readings table + matched
    # interpretations + the refuses-verdict claim license).
    lines.append(resolver_mod.render_report(report).rstrip())
    lines.append("")
    lines.append("## Next actions (mechanical)")
    lines.append("")
    if next_action["unknown_readings"]:
        lines.append("### Populate unknown readings")
        lines.append("")
        for u in next_action["unknown_readings"]:
            lines.append(
                f"- `{u['reading']}` ← `{u['populating_surface']}`:"
            )
            lines.append(f"  `{u['command']}`")
        lines.append("")
    if next_action["unavailable_members"]:
        lines.append("### Unavailable members")
        lines.append("")
        for u in next_action["unavailable_members"]:
            unlock = u.get("unlock") or u.get("reason") or ""
            lines.append(
                f"- `{u['surface_id']}` ({u['reason_category']}): {unlock}"
            )
        lines.append("")
    if next_action["restoration_handoff"]:
        lines.append("### Restoration handoff")
        lines.append("")
        rh = next_action["restoration_handoff"]
        lines.append(f"- `{rh['restoration_packet']}`")
        lines.append(f"- `{rh['before_after_restoration']}`")
        lines.append("")
    lines.append("### Rerun")
    lines.append("")
    lines.append(f"`{next_action['rerun']}`")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------- list-sets / situation ----------------------------------------

def _list_sets() -> int:
    for name in sorted(RUN_SETS):
        members = RUN_SETS[name]
        print(f"{name}:")
        for sid in members:
            marker = "  (attach-only)" if sid in ATTACH_ONLY else ""
            print(f"  {sid}{marker}")
    return EXIT_OK


def _report_situation(situation: str) -> int:
    """--situation is REPORT-ONLY in M1: print the ranked recommend()
    matches + which preset covers them, execute nothing. It does not
    supersede the /setec skill's recommendation authority — the skill
    remains the router of record when the two differ."""
    manifest = _load_live_manifest()
    results = capabilities.recommend(situation, manifest=manifest)
    print(capabilities.render_recommend(results, situation))
    matched_ids = {eid for eid, _e, _kw in results}
    print("## Preset coverage")
    print("")
    for name in sorted(RUN_SETS):
        covered = [sid for sid in RUN_SETS[name] if sid in matched_ids]
        cov = ", ".join(covered) if covered else "(none)"
        print(f"- `{name}` covers: {cov}")
    print("")
    print(
        "_Informational only: `--situation` never drives execution, and "
        "the `/setec` skill remains the router of record._"
    )
    return EXIT_OK


# ---------- the run --------------------------------------------------------

def run_set(
    *,
    set_name: str | None,
    surfaces: list[str] | None,
    target: str,
    baseline_dir: str | None,
    attaches: dict[str, str],
    ai_status: str | None,
    out_dir: Path,
    resume: bool,
    emit_json: bool,
    manifest: dict[str, Any],
    cli_argv: list[str],
) -> int:
    started_at = _utc_now()
    by_id = {e.get("id"): e for e in capabilities.entries(manifest)}

    # Resolve the member list.
    if set_name is not None:
        members = list(RUN_SETS[set_name])
        set_label = f"--set {set_name}"
    else:
        members = list(surfaces or [])
        set_label = f"--surfaces {','.join(members)}"
    # Attached ids extend the member set (a preset that doesn't list an
    # attached surface still collects it).
    for sid in attaches:
        if sid not in members:
            members.append(sid)

    # Preset ids are resolved against the LIVE manifest at runtime; an id
    # that no longer resolves is a bad_input (and a CI test pins every
    # preset id to an existing fragment, so a rename breaks the build, not
    # the operator).
    for sid in members:
        if sid not in by_id:
            return _error(
                f"member {sid!r} does not resolve in the capabilities "
                f"manifest (renamed or removed surface?)",
                "bad_input", EXIT_CONTRACT,
            )
        # Membership guard, execution-scoped: the runner never injects
        # --json-out and never handles private voice-clone artifacts. The
        # closed member universe (KWARG_MAP, checked in main) already
        # excludes pov_voice_profile / voice_profile; this guard
        # additionally refuses to EXEC any member whose manifest entry
        # grew `json_delivery: file` (general_imposters carries it but is
        # attach-only, so it never reaches the exec path).
        if (
            sid not in ATTACH_ONLY
            and sid not in attaches
            and by_id[sid].get("json_delivery") == "file"
        ):
            return _error(
                f"member {sid!r} is a json_delivery: file surface; the "
                f"runner never injects --json-out and never handles "
                f"private artifacts. Supply it via --attach instead.",
                "bad_input", EXIT_CONTRACT,
            )

    # Validate the target BEFORE touching the run folder, so a typo'd
    # target doesn't leave a half-created out-dir behind that then trips
    # the non-empty refusal on the corrected rerun.
    target_path = Path(target)
    if not target_path.is_file():
        return _error(
            f"--target not found: {target}", "bad_input", EXIT_CONTRACT,
        )
    target_text = target_path.read_text(encoding="utf-8", errors="ignore")

    # Run-folder handling: refuse a non-empty out-dir without --resume
    # (prevents silent cross-run contamination).
    if out_dir.exists() and any(out_dir.iterdir()) and not resume:
        return _error(
            f"--out-dir {out_dir} is not empty; pass --resume to reuse its "
            f"member envelopes or point at a fresh directory",
            "bad_input", EXIT_CONTRACT,
        )
    envelopes_dir = out_dir / "envelopes"
    envelopes_dir.mkdir(parents=True, exist_ok=True)

    # ---- member loop (increment 1) ----
    records: list[_MemberRecord] = []
    n_members = len(members)
    for i, sid in enumerate(members, 1):
        entry = by_id[sid]
        tier = (entry.get("compute") or {}).get("tier")
        record = _MemberRecord(sid, tier)
        records.append(record)

        attach_path = attaches.get(sid)
        # --resume reuses a completed (available != false) envelope; an
        # EXPLICIT --attach for the member overrides a stored envelope
        # (the rerun template suggests exactly that combination).
        if resume and attach_path is None and _try_resume(record, envelopes_dir):
            _progress(
                f"{i}/{n_members} {sid}: resumed (reused existing envelope)"
            )
            continue

        if attach_path is not None:
            _process_attach(
                record, Path(attach_path), envelopes_dir,
                _producer_command(sid, target, baseline_dir),
            )
        elif sid in ATTACH_ONLY:
            record.reason_category = "bad_input"
            record.reason = (
                f"attach-only member; supply --attach {sid}=<path> "
                f"(see next_action)"
            )
            _write_member_envelope(
                envelopes_dir, record,
                _member_error_envelope(sid, record.reason, "bad_input"),
            )
        else:
            _process_exec(record, entry, target, baseline_dir, envelopes_dir)

        _progress(
            f"{i}/{n_members} {sid}: {record.disposition} "
            f"available={str(record.available).lower()}"
            + (
                f" reason_category={record.reason_category}"
                if record.reason_category else ""
            )
            + f" -> {record.envelope_path}"
        )

    # ---- run_meta.json (belt bookkeeping; the FULL record incl. argv /
    # exit / sha lives here, outside the envelope) ----
    run_meta = {
        "set": set_name,
        "surfaces": surfaces,
        "requested_members": members,
        "member_records": [r.to_meta_record() for r in records],
        "setec_version": capabilities.setec_version(),
        "argv": cli_argv,
        "started_at": started_at,
        "finished_at": _utc_now(),
    }
    (out_dir / "run_meta.json").write_text(
        json.dumps(run_meta, indent=2, default=str) + "\n", encoding="utf-8",
    )

    # ---- all-members-failed: the run itself fails with the modal member
    # reason_category (mapped exit); >= 1 available envelope → exit 0. ----
    n_available = sum(1 for r in records if r.available)
    if records and n_available == 0:
        counts: dict[str, int] = {}
        for r in records:
            if r.reason_category:
                counts[r.reason_category] = counts.get(r.reason_category, 0) + 1
        modal = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        exit_code = setec_run._CATEGORY_DEFAULT_EXIT.get(modal, EXIT_INTERNAL)
        return _error(
            f"no member produced an available envelope (modal member "
            f"reason_category: {modal}); member envelopes are checkpointed "
            f"under {envelopes_dir} for --resume",
            modal, exit_code,
        )

    # ---- resolver wiring (increment 2): unwrap envelope["results"] before
    # resolve() — the resolver consumes RAW report dicts (anchor #6). ----
    resolve_kwargs: dict[str, Any] = {}
    for rec in records:
        if not rec.available or rec.envelope is None:
            continue
        resolve_kwargs[KWARG_MAP[rec.surface_id]] = _payload_for_resolver(
            rec.envelope,
        )
    report = resolver_mod.resolve(target_text=target_text, **resolve_kwargs)
    # Ordering contract (§4.4): _claim_license reads report["ai_status"]
    # for the B.3 with_state_caveats routing, so populate it BEFORE the
    # _claim_license(report) call — exactly as the resolver's own main()
    # does. Pass-through only; no interpretation.
    if ai_status:
        report["ai_status"] = ai_status
    claim_license = resolver_mod._claim_license(report)

    # §4.4 sanity tripwire (mechanical, not a threshold): available member
    # whose primary reading is unknown → a shape-drift record INSIDE the
    # combined envelope (next_action.unavailable_members); stderr mirrors.
    readings = report.get("readings", {})
    tripwires: list[dict[str, Any]] = []
    for rec in records:
        if not rec.available:
            continue
        primary = MEMBER_PRIMARY_READING[rec.surface_id]
        if readings.get(primary) == "unknown":
            reason = (
                f"envelope was available but the resolver reading "
                f"{primary!r} came back unknown — the surface's results "
                f"shape may have drifted from the resolver's reader"
            )
            tripwires.append({
                "surface_id": rec.surface_id,
                "reason_category": "shape_drift",
                "reason": reason,
                "unlock": (
                    f"inspect {rec.envelope_path} against "
                    f"surface_disagreement_resolver's _read_* readers"
                ),
            })
            _progress(f"warning: {rec.surface_id}: {reason}")

    next_action = _build_next_action(
        report=report,
        records=records,
        tripwires=tripwires,
        target=target,
        baseline_dir=baseline_dir,
        out_dir=out_dir,
        set_label=set_label,
        attaches=attaches,
    )

    results: dict[str, Any] = {
        "run_set": {
            "name": set_name or "(explicit --surfaces)",
            "requested_members": members,
            "member_records": [r.to_envelope_record() for r in records],
        },
        "envelopes": {
            r.surface_id: r.envelope
            for r in records if r.envelope is not None
        },
        "disagreement": report,
        "next_action": next_action,
    }

    # ---- the mechanical anti-Goodhart gate (§6.4) — RUNTIME, on the real
    # output dict, every run. Fail-closed: a tripped guard is a runner bug
    # (internal_error, exit 1, report NOT written). ----
    try:
        _guard_results(results)
    except AggregateVerdictError as exc:
        return _error(
            f"aggregate-verdict guard tripped: {exc}",
            "internal_error", EXIT_INTERNAL,
        )

    baseline_block = (
        {"baseline_dir": str(baseline_dir)} if baseline_dir else None
    )
    try:
        envelope = build_output(
            task_surface=TASK_SURFACE,
            tool=TOOL_NAME,
            version=SCRIPT_VERSION,
            target_path=target,
            target_words=len(target_text.split()),
            baseline=baseline_block,
            results=results,
            claim_license=claim_license,
            ai_status=ai_status,
        )
    except OutputValidityError as exc:
        # R4 walked the full results (incl. pass-through payloads); an
        # out-of-bounds value is fail-closed, same as the dispatcher.
        return _error(
            f"output validity gate rejected the combined results: {exc}",
            "internal_error", EXIT_INTERNAL,
        )

    # ---- pass-through shape check (§6.4): results.envelopes[<id>] must be
    # JSON-equal to the parsed run-folder file — the runner is structurally
    # incapable of quietly adjusting a member envelope. ----
    for rec in records:
        if rec.envelope is None or rec.envelope_path is None:
            continue
        on_disk = json.loads(
            (out_dir / rec.envelope_path).read_text(encoding="utf-8"),
        )
        if on_disk != results["envelopes"][rec.surface_id]:
            return _error(
                f"pass-through identity violated for {rec.surface_id}: "
                f"results.envelopes differs from {rec.envelope_path}",
                "internal_error", EXIT_INTERNAL,
            )

    report_md = _render_report_md(
        set_name=set_name or "(explicit --surfaces)",
        target=target,
        records=records,
        report=report,
        next_action=next_action,
    )
    (out_dir / "report.json").write_text(
        json.dumps(envelope, indent=2, default=str) + "\n", encoding="utf-8",
    )
    (out_dir / "report.md").write_text(report_md, encoding="utf-8")

    if emit_json:
        _emit(envelope)
    else:
        sys.stdout.write(report_md)
    return EXIT_OK


# ---------- CLI ------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="setec_run_set.py",
        description=(
            "Run a named set of SETEC surfaces over one target, collect "
            "their schema-1.0 envelopes into a run folder, feed them to "
            "the surface_disagreement_resolver, and emit disagreement "
            "patterns + a mechanical next-action block. No composite "
            "score. No verdict. Ever."
        ),
    )
    p.add_argument("--set", dest="set_name", help="Named preset (§--list-sets).")
    p.add_argument(
        "--surfaces",
        help="Comma-separated explicit member list (mutually exclusive "
             "with --set).",
    )
    p.add_argument("--target", help="The draft file to audit.")
    p.add_argument(
        "--baseline-dir",
        help="Optional; projected into every member that accepts it. "
             "Without it voice_distance is skipped with a bad_input "
             "member record (never argparse-crashed).",
    )
    p.add_argument(
        "--attach", action="append", default=[], metavar="SURFACE=PATH",
        help="Join an operator-supplied, pre-computed envelope (or legacy "
             "raw report) without executing anything. Repeatable. An "
             "explicit --attach overrides a stored envelope on --resume.",
    )
    p.add_argument(
        "--ai-status", default=None,
        help="Passed through to the resolver report (B.3 state-routed "
             "caveats) and stamped on the combined envelope. Pass-through "
             "only; no interpretation.",
    )
    p.add_argument(
        "--out-dir", default=None,
        help="Run folder (default: ./setec-run-sets/<UTCstamp>-<set>/).",
    )
    p.add_argument(
        "--resume", action="store_true",
        help="Reuse any completed envelopes/<id>.json already in "
             "--out-dir instead of re-running that member (error records "
             "with available:false are retried).",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the combined envelope to stdout (default: rendered "
             "markdown). Both are always written to the run folder.",
    )
    p.add_argument(
        "--situation",
        help="REPORT-ONLY: print capabilities.recommend() matches + which "
             "preset covers them, then exit. Never drives execution; the "
             "/setec skill remains the router of record.",
    )
    p.add_argument(
        "--list-sets", action="store_true",
        help="Enumerate presets and their members, exit 0.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    args = build_arg_parser().parse_args(args_list)

    if args.list_sets:
        return _list_sets()
    if args.situation:
        return _report_situation(args.situation)

    # ---- discovery/contract validation (envelope on 2/3, like R3) ----
    if args.set_name and args.surfaces:
        return _error(
            "--set and --surfaces are mutually exclusive",
            "bad_input", EXIT_CONTRACT,
        )
    if not args.set_name and not args.surfaces:
        return _error(
            "no run set given: pass --set <name> or --surfaces a,b,c "
            "(or --list-sets to enumerate presets)",
            "bad_input", EXIT_CONTRACT,
        )
    if args.set_name and args.set_name not in RUN_SETS:
        known = ", ".join(sorted(RUN_SETS))
        return _error(
            f"unknown set {args.set_name!r}; known sets: {known}",
            "bad_input", EXIT_DISCOVERY,
        )

    surfaces: list[str] | None = None
    if args.surfaces:
        surfaces = [s.strip() for s in args.surfaces.split(",") if s.strip()]
        if not surfaces:
            return _error(
                "--surfaces given but empty", "bad_input", EXIT_CONTRACT,
            )
        dupes = {s for s in surfaces if surfaces.count(s) > 1}
        if dupes:
            return _error(
                f"duplicate member id(s) in --surfaces: "
                f"{', '.join(sorted(dupes))}",
                "bad_input", EXIT_CONTRACT,
            )
        unknown = [s for s in surfaces if s not in KWARG_MAP]
        if unknown:
            return _error(
                f"unknown surface id(s) in --surfaces: "
                f"{', '.join(unknown)}; the runner's member universe is: "
                f"{', '.join(sorted(KWARG_MAP))}",
                "bad_input", EXIT_DISCOVERY,
            )

    attaches: dict[str, str] = {}
    for spec in args.attach:
        if "=" not in spec:
            return _error(
                f"malformed --attach {spec!r}; expected SURFACE=PATH",
                "bad_input", EXIT_CONTRACT,
            )
        sid, _, path = spec.partition("=")
        sid = sid.strip()
        if sid not in KWARG_MAP:
            return _error(
                f"unknown surface id in --attach: {sid!r}; attachable "
                f"surfaces are: {', '.join(sorted(KWARG_MAP))}",
                "bad_input", EXIT_DISCOVERY,
            )
        if not path or not Path(path).is_file():
            return _error(
                f"unreadable --attach file for {sid}: {path!r}",
                "bad_input", EXIT_CONTRACT,
            )
        attaches[sid] = path

    if not args.target:
        return _error(
            "no --target given (required unless --list-sets/--situation)",
            "bad_input", EXIT_CONTRACT,
        )

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        label = args.set_name or "surfaces"
        out_dir = Path("setec-run-sets") / f"{stamp}-{label}"

    manifest = _load_live_manifest()
    return run_set(
        set_name=args.set_name,
        surfaces=surfaces,
        target=args.target,
        baseline_dir=args.baseline_dir,
        attaches=attaches,
        ai_status=args.ai_status,
        out_dir=out_dir,
        resume=args.resume,
        emit_json=args.json,
        manifest=manifest,
        cli_argv=args_list,
    )


if __name__ == "__main__":
    sys.exit(main())
