#!/usr/bin/env python3
"""setec_run.py — the normalized `setec run <surface> --json` dispatcher.

R2 + R3 of the SETEC normalized-entrypoint spec
(``references/setec-normalized-entrypoint-spec.md`` §2, §3, §4).

A thin, table-driven dispatcher over the capabilities manifest. It
resolves a consumer ``<surface>`` to its script, runs the script, and
**guarantees a ``schema_version: 1.0`` envelope reaches stdout** —
regardless of whether the underlying script delivers JSON on stdout or
into a private file artifact. The dispatcher owns exactly ONE consumer
flag (``--json``), eliminating the argparse-prefix-match and
``--json-out=`` variance from the consumer path that broke
``pov_voice_profile`` on APODICTIC PR #6.

Usage::

    python3 setec_run.py <surface> [surface args...] --json
    python3 setec_run.py --list                 # enumerate consumer surfaces

Responsibilities, IN ORDER (spec §2):

  1. Resolve ``<surface>`` from the manifest, else R3 ``bad_input``.
  2. Assert the manifest's ``min_setec_version`` <= the running
     ``setec_version`` (plugin.json), else R3 ``version_floor`` (reporting
     BOTH the requested floor and the observed version).
  3. Check the entry's ``dependencies.python`` are importable, else R3
     ``missing_dependency``.
  4. Exec the resolved script.
  5. Guarantee the envelope reaches stdout:
       * ``json_delivery: stdout`` (7 surfaces) — pass ``--json`` through,
         capture stdout, re-emit.
       * ``json_delivery: file`` (``pov_voice_profile`` + ``voice_profile``,
         the two voice-clone surfaces) — inject a private ``--json-out``
         under ``ai-prose-baselines-private/`` in a tempdir, read the
         artifact, project the consumer envelope to stdout, clean up the
         tempdir (spec §3).
  6. On script failure (nonzero exit / unparseable output), wrap as an R3
     error envelope.

R3 error model (spec §4): a failed/blocked run emits the SAME
``schema_version: 1.0`` envelope with ``available: false`` plus ``reason``
+ ``reason_category``. Exit codes: **0** success; **2** discovery/version
(bad surface, version floor); **3** contract/usage (bad input, policy
refusal, text-too-short, missing dependency surfaced as usage); **1**
unexpected internal error. The envelope is emitted on 2/3 so the consumer
branches on ``reason_category`` without scraping stderr.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import capabilities  # type: ignore
from output_schema import REASON_CATEGORIES, SCHEMA_VERSION, build_error_output  # type: ignore

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
# The repo root is the parent of plugins/ — manifest script_path values are
# repo-relative (``plugins/setec-voiceprint/scripts/<x>.py``).
REPO_ROOT = PLUGIN_ROOT.parent.parent

TOOL = "setec_run"
# The dispatcher's own version, independent of any surface's SCRIPT_VERSION
# and of the plugin semver. Bumped only when the dispatcher's behavior
# changes; the error envelope's ``version`` field uses it.
DISPATCHER_VERSION = "1.0.0"

# Exit-code scheme (spec §4). The envelope is still emitted on 2/3.
EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_DISCOVERY = 2   # bad surface, version floor
EXIT_CONTRACT = 3    # bad input, policy refusal, text too short, missing dep

# reason_category -> exit code. internal_error -> 1; version_floor /
# (bad_surface handled separately) -> 2; everything else (contract/usage)
# -> 3. A genuinely-unknown surface is a discovery failure (2); a known
# surface invoked with bad ARGS is a contract failure (3) — both carry
# reason_category ``bad_input`` but differ in exit code, so the mapping is
# resolved at the call site, not purely by category.
_CATEGORY_DEFAULT_EXIT = {
    "version_floor": EXIT_DISCOVERY,
    "missing_dependency": EXIT_CONTRACT,
    "bad_input": EXIT_CONTRACT,
    "text_too_short": EXIT_CONTRACT,
    "policy_refused": EXIT_CONTRACT,
    "internal_error": EXIT_INTERNAL,
}


# ---------- surface resolution -------------------------------------

def consumer_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return ``{surface_id: entry}`` for every consumer surface — i.e.
    every entry carrying a ``json_delivery`` field. That is exactly the
    promoted set (the nine APODICTIC surfaces from R1 + the four
    setec-voicewright fitness surfaces from 1.115.0); a manifest entry
    without ``json_delivery`` is not a normalized consumer surface and the
    dispatcher will not run it."""
    out: dict[str, dict[str, Any]] = {}
    for e in capabilities.entries(manifest):
        if e.get("json_delivery") is None:
            continue
        sid = e.get("id")
        if sid:
            out[sid] = e
    return out


# ---------- semver floor check -------------------------------------

def _parse_semver(v: str) -> tuple[int, int, int]:
    """Parse ``MAJOR.MINOR.PATCH`` into a comparable tuple. Pre-release /
    build metadata (``-rc1`` / ``+build``) is stripped — the floor check is
    coarse-grained on the release triple, which is all the manifest floors
    and plugin.json use. Raises ``ValueError`` on a malformed string."""
    core = v.strip().split("+", 1)[0].split("-", 1)[0]
    parts = core.split(".")
    if len(parts) != 3:
        raise ValueError(f"not a MAJOR.MINOR.PATCH semver: {v!r}")
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def version_satisfies_floor(observed: str, floor: str) -> bool:
    """True iff ``observed`` >= ``floor`` by semver triple comparison."""
    return _parse_semver(observed) >= _parse_semver(floor)


# ---------- dependency check ---------------------------------------

def missing_required_deps(entry: dict[str, Any]) -> list[str]:
    """Required ``dependencies.python`` modules that are NOT importable.
    Reuses ``capabilities.is_installed`` (importlib spec probe; no import
    side effects)."""
    deps = (entry.get("dependencies") or {}).get("python") or []
    return [d for d in deps if not capabilities.is_installed(d)]


# ---------- script execution + envelope extraction -----------------

def _script_abspath(entry: dict[str, Any]) -> Path:
    """Resolve the manifest's repo-relative ``script_path`` to an absolute
    path under this checkout."""
    rel = entry.get("script_path")
    return (REPO_ROOT / rel).resolve()


def _run_subprocess(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # Inherit the dispatcher's CWD (the consumer's working directory). The
    # script is invoked by its ABSOLUTE path (_script_abspath), so it is
    # always found regardless of CWD; but the consumer's INPUT paths (a
    # target file, a --manifest with manifest-relative entry paths) must
    # resolve relative to the consumer's CWD, not the repo root. Forcing
    # cwd=REPO_ROOT would break every relative input path the consumer
    # passes.
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )


def _emit(envelope: dict[str, Any]) -> None:
    """Write a finished envelope to stdout (pretty + stable)."""
    print(json.dumps(envelope, indent=2, default=str))


def _emit_surface_envelope(envelope: dict[str, Any]) -> int:
    """Re-emit a surface-produced schema_version 1.0 envelope and return the
    dispatcher exit code.

    A success envelope (``available`` not False) re-emits and exits 0. A
    surface that emits its OWN structured R3 refusal — ``available: false``
    with a ``reason_category`` (e.g. general_imposters refusing when the
    manifest has too few impostor personas) — is HONORED: the envelope is
    re-emitted verbatim (its ``reason``/``reason_category`` are richer and
    more accurate than the dispatcher scraping stderr in
    ``_wrap_script_failure``) and the exit code is derived from
    ``reason_category`` via the SAME mapping the dispatcher's synthesized
    errors use, so a script-emitted refusal and a dispatcher-synthesized one
    are indistinguishable to the consumer. A missing/unknown category on an
    available=False envelope is treated as ``internal_error`` (exit 1) — a
    surface that says "unavailable" without saying why is a contract bug.
    Applied on BOTH delivery paths (stdout + file) so they cannot diverge."""
    _emit(envelope)
    if envelope.get("available") is False:
        category = envelope.get("reason_category")
        return _CATEGORY_DEFAULT_EXIT.get(category, EXIT_INTERNAL)
    return EXIT_OK


def _error(
    *,
    surface: str | None,
    reason: str,
    reason_category: str,
    exit_code: int,
    extra: dict[str, Any] | None = None,
) -> int:
    """Emit an R3 error envelope to stdout and return ``exit_code``."""
    assert reason_category in REASON_CATEGORIES
    envelope = build_error_output(
        task_surface=None,
        tool=TOOL,
        version=DISPATCHER_VERSION,
        reason=reason,
        reason_category=reason_category,
        extra=extra,
    )
    # Carry the requested surface name for the consumer's logs (additive,
    # never collides with a reserved key).
    if surface is not None and "surface" not in envelope:
        envelope["surface"] = surface
    _emit(envelope)
    return exit_code


def _wrap_script_failure(
    surface: str,
    proc: subprocess.CompletedProcess[str],
) -> int:
    """Map a nonzero-exit / unparseable script run to an R3 envelope.

    A SETEC consumer script signals a usage / policy refusal with a
    nonzero exit and a stderr message. Exit 2 is overloaded: the
    voice-clone privacy guard (``pov_voice_profile`` / ``voice_profile``)
    exits 2 on a refused output path, but Python ``argparse`` ALSO exits 2
    on a usage error (e.g. an unrecognized flag), emitting a ``usage:``
    line. We disambiguate so consumers can branch on ``reason_category``
    (R3): an argparse usage error (``usage:`` in stderr) -> ``bad_input``;
    any other exit 2 -> ``policy_refused`` (the privacy ratchet); anything
    else -> ``internal_error``. The stderr tail becomes the human
    ``reason``."""
    stderr_tail = (proc.stderr or "").strip()
    if not stderr_tail:
        stderr_tail = (
            f"script exited {proc.returncode} with no stderr and no "
            f"parseable envelope on stdout"
        )
    if proc.returncode == 2:
        # argparse usage errors (unrecognized flag, etc.) also exit 2 but
        # emit a "usage:" line — those are bad_input, not a privacy refusal.
        if "usage:" in stderr_tail.lower():
            return _error(
                surface=surface,
                reason=(
                    f"{surface}: invalid arguments (exit 2): {stderr_tail}"
                ),
                reason_category="bad_input",
                exit_code=EXIT_CONTRACT,
            )
        return _error(
            surface=surface,
            reason=(
                f"{surface}: refused by the script's policy guard "
                f"(exit 2): {stderr_tail}"
            ),
            reason_category="policy_refused",
            exit_code=EXIT_CONTRACT,
        )
    return _error(
        surface=surface,
        reason=(
            f"{surface}: script failed (exit {proc.returncode}): "
            f"{stderr_tail}"
        ),
        reason_category="internal_error",
        exit_code=EXIT_INTERNAL,
    )


def _is_envelope(obj: Any) -> bool:
    """True iff ``obj`` is the promised envelope: a dict whose
    ``schema_version`` is exactly ``SCHEMA_VERSION`` (``"1.0"``). BOTH delivery
    paths gate on this — stdout extraction (``_extract_envelope``) and the
    file-artifact re-emit (``_run_file_surface``) — so neither can re-emit a
    non-1.0 / non-envelope payload as a success, honoring R2's promise that
    ``setec run ... --json`` returns a schema_version 1.0 envelope. A schema
    bump is then a single-line change to ``output_schema.SCHEMA_VERSION``."""
    return isinstance(obj, dict) and obj.get("schema_version") == SCHEMA_VERSION


def _extract_envelope(stdout: str) -> dict[str, Any] | None:
    """Recover the schema_version 1.0 envelope from a surface's stdout.

    Fast path (unchanged behavior for clean stdout): ``json.loads(stdout)``
    over the WHOLE buffer — a surface that prints exactly one JSON object and
    nothing else parses here, byte-for-byte as before.

    Robust path: a surface may emit a non-JSON preamble on stdout before the
    envelope — a model-download / progress line (e.g.
    ``Downloading model...``), an NLTK ``[nltk_data]`` notice, etc. Such a
    preamble made the whole-buffer parse fail and mislabeled a SUCCESSFUL run
    as ``internal_error``. The dispatcher's target script prints the envelope
    as a single top-level JSON OBJECT, so we scan the buffer for balanced
    ``{...}`` blocks (respecting strings/escapes so braces inside JSON string
    values don't confuse the matcher) and return the LAST one that parses as a
    dict whose ``schema_version`` is ``1.0`` (the envelope's defining shape).
    The last such block is the most robust choice: any preamble objects a tool
    might print precede the real envelope, which is emitted last.

    Returns the parsed envelope dict, or ``None`` if no valid envelope object
    is found (the caller then raises ``internal_error``)."""
    # Fast path: clean single-object stdout. Confirm it is the envelope shape
    # (a dict with schema_version == 1.0) so a surface that prints a bare
    # non-envelope JSON value — or a wrong-version one — doesn't slip through
    # as a "success".
    try:
        whole = json.loads(stdout)
    except json.JSONDecodeError:
        whole = None
    if _is_envelope(whole):
        return whole

    # Robust path: scan for balanced top-level {...} blocks and keep the last
    # one that parses as an envelope-shaped dict.
    found: dict[str, Any] | None = None
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(stdout):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    block = stdout[start : i + 1]
                    try:
                        obj = json.loads(block)
                    except json.JSONDecodeError:
                        obj = None
                    if _is_envelope(obj):
                        found = obj  # keep scanning; prefer the LAST match
    return found


def _run_stdout_surface(
    surface: str,
    entry: dict[str, Any],
    surface_args: list[str],
) -> int:
    """Run a ``json_delivery: stdout`` surface: pass ``--json`` through,
    capture stdout, parse the envelope, and re-emit it. On failure, wrap
    as R3."""
    script = _script_abspath(entry)
    cmd = [sys.executable, str(script), *surface_args, "--json"]
    proc = _run_subprocess(cmd)
    if proc.returncode != 0:
        return _wrap_script_failure(surface, proc)
    # Parse robustly: a clean single-object stdout takes the fast path
    # (unchanged); a non-JSON preamble (model-download / progress line) before
    # the envelope is tolerated by extracting the envelope object from stdout.
    # Only genuine garbage (no envelope object at all) is an internal_error.
    envelope = _extract_envelope(proc.stdout)
    if envelope is None:
        return _error(
            surface=surface,
            reason=(
                f"{surface}: script exited 0 but stdout carried no parseable "
                f"schema_version envelope (after tolerating any non-JSON "
                f"preamble); stderr: {(proc.stderr or '').strip()}"
            ),
            reason_category="internal_error",
            exit_code=EXIT_INTERNAL,
        )
    return _emit_surface_envelope(envelope)


def _run_file_surface(
    surface: str,
    entry: dict[str, Any],
    surface_args: list[str],
) -> int:
    """Run a ``json_delivery: file`` surface (``pov_voice_profile`` /
    ``voice_profile`` — the voice-clone surfaces, which refuse JSON on
    stdout): inject a private ``--json-out`` under
    ``ai-prose-baselines-private/`` in a tempdir, run the script (which
    writes the FULL schema_version 1.0 envelope to that file under its
    default-private policy), read the artifact, and project the consumer
    envelope to stdout. The consumer never touches ``--json-out`` (spec §3).
    The tempdir is always cleaned up."""
    script = _script_abspath(entry)
    # The script's privacy guard requires the output path to live under a
    # directory named exactly ``ai-prose-baselines-private`` (resolved
    # path components). Build that inside a tempdir so the artifact is
    # private AND ephemeral.
    tmpdir = Path(tempfile.mkdtemp(prefix="setec_run_"))
    try:
        private_dir = tmpdir / "ai-prose-baselines-private"
        private_dir.mkdir(parents=True, exist_ok=True)
        artifact = private_dir / "pov_profile.json"
        cmd = [
            sys.executable, str(script),
            *surface_args,
            "--json-out", str(artifact),
        ]
        proc = _run_subprocess(cmd)
        if proc.returncode != 0:
            return _wrap_script_failure(surface, proc)
        if not artifact.exists():
            return _error(
                surface=surface,
                reason=(
                    f"{surface}: script exited 0 but wrote no envelope "
                    f"artifact to the injected --json-out path; stderr: "
                    f"{(proc.stderr or '').strip()}"
                ),
                reason_category="internal_error",
                exit_code=EXIT_INTERNAL,
            )
        try:
            envelope = json.loads(artifact.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return _error(
                surface=surface,
                reason=(
                    f"{surface}: the --json-out artifact was not a parseable "
                    f"JSON envelope ({exc})"
                ),
                reason_category="internal_error",
                exit_code=EXIT_INTERNAL,
            )
        # The artifact must BE the promised schema_version 1.0 envelope before
        # we re-emit it — the SAME gate the stdout path applies (_is_envelope).
        # A regressed surface that writes ``{}``, a wrong-version object, or
        # some other JSON would otherwise exit 0 and break R2's promise that
        # ``setec run ... --json`` returns a schema_version 1.0 envelope.
        if not _is_envelope(envelope):
            shape = (
                f"keys {sorted(envelope)[:8]}" if isinstance(envelope, dict)
                else type(envelope).__name__
            )
            return _error(
                surface=surface,
                reason=(
                    f"{surface}: the --json-out artifact is not a "
                    f"schema_version 1.0 envelope ({shape})"
                ),
                reason_category="internal_error",
                exit_code=EXIT_INTERNAL,
            )
        # Project the consumer envelope to stdout. The artifact IS the
        # build_output() consumer envelope (slim; no raw voice-clone
        # material — that lives in the rich --out markdown, which the
        # dispatcher never requests), so the projection is a faithful
        # re-emit. The rich private artifact stays inside the tempdir and
        # is destroyed on cleanup; nothing private reaches stdout beyond
        # what the audit already licenses. A script-emitted available=False
        # refusal (e.g. general_imposters' too-few-impostors gate) is honored
        # with its own reason_category and the mapped exit code.
        return _emit_surface_envelope(envelope)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------- top-level dispatch -------------------------------------

def dispatch(
    surface: str,
    surface_args: list[str],
    *,
    manifest: dict[str, Any] | None = None,
    observed_version: str | None = None,
) -> int:
    """Resolve and run ``surface`` with ``surface_args``. Returns the
    process exit code; always emits exactly one envelope to stdout (success
    or R3 error). ``manifest`` / ``observed_version`` are injectable for
    tests."""
    if manifest is None:
        manifest = capabilities.load_manifest()
    if observed_version is None:
        observed_version = capabilities.setec_version()

    surfaces = consumer_entries(manifest)

    # (1) Resolve surface -> entry, else R3 bad_input (discovery exit 2).
    entry = surfaces.get(surface)
    if entry is None:
        known = ", ".join(sorted(surfaces)) or "(none)"
        return _error(
            surface=surface,
            reason=(
                f"unknown surface {surface!r}; known consumer surfaces: "
                f"{known}"
            ),
            reason_category="bad_input",
            exit_code=EXIT_DISCOVERY,
        )

    # (2) Version-floor check, else R3 version_floor (discovery exit 2).
    floor = entry.get("min_setec_version")
    if floor:
        try:
            satisfied = version_satisfies_floor(observed_version, floor)
        except ValueError as exc:
            return _error(
                surface=surface,
                reason=(
                    f"{surface}: could not compare versions "
                    f"(observed={observed_version!r}, floor={floor!r}): {exc}"
                ),
                reason_category="internal_error",
                exit_code=EXIT_INTERNAL,
            )
        if not satisfied:
            return _error(
                surface=surface,
                reason=(
                    f"{surface} requires setec_version >= {floor}, but the "
                    f"running plugin is {observed_version}. Upgrade SETEC to "
                    f"at least {floor}."
                ),
                reason_category="version_floor",
                exit_code=EXIT_DISCOVERY,
                # Machine-readable pair so the consumer never re-derives it
                # from prose (the _install_instructions self-contradiction
                # bug): report BOTH requested floor and observed version.
                extra={
                    "version_floor": {
                        "required": floor,
                        "observed": observed_version,
                    },
                },
            )

    # (3) Required-dependency check, else R3 missing_dependency (contract
    # exit 3).
    missing = missing_required_deps(entry)
    if missing:
        return _error(
            surface=surface,
            reason=(
                f"{surface} requires Python module(s) not installed: "
                f"{', '.join(missing)}. Install them and retry."
            ),
            reason_category="missing_dependency",
            exit_code=EXIT_CONTRACT,
            extra={"missing_dependency": {"python": missing}},
        )

    # (4)+(5) Exec the script and guarantee the envelope reaches stdout.
    delivery = entry.get("json_delivery")
    if delivery == "stdout":
        return _run_stdout_surface(surface, entry, surface_args)
    if delivery == "file":
        return _run_file_surface(surface, entry, surface_args)
    # A surface with an unexpected json_delivery value is a manifest bug.
    return _error(
        surface=surface,
        reason=(
            f"{surface}: unsupported json_delivery {delivery!r} in the "
            f"manifest (expected 'stdout' or 'file')"
        ),
        reason_category="internal_error",
        exit_code=EXIT_INTERNAL,
    )


# ---------- CLI ----------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # --list / --help are handled before surface parsing so they don't
    # collide with surface arg passthrough.
    if args and args[0] in {"-h", "--help"}:
        print(__doc__)
        return EXIT_OK
    if args and args[0] in {"--list", "-l"}:
        try:
            manifest = capabilities.load_manifest()
        except Exception as exc:  # noqa: BLE001 — surfaced as text, not envelope
            print(f"setec_run: could not load manifest: {exc}", file=sys.stderr)
            return EXIT_INTERNAL
        for sid in sorted(consumer_entries(manifest)):
            print(sid)
        return EXIT_OK

    if not args:
        # No surface: a usage/bad_input failure. Emit an R3 envelope so the
        # consumer path is uniform even here.
        return _error(
            surface=None,
            reason=(
                "no surface given. Usage: setec_run.py <surface> [args] "
                "--json  (or --list to enumerate surfaces)"
            ),
            reason_category="bad_input",
            exit_code=EXIT_DISCOVERY,
        )

    surface = args[0]
    # The dispatcher owns exactly one consumer flag, ``--json``. It is
    # accepted (and required-by-convention) but NOT forwarded blindly: for
    # stdout surfaces the dispatcher adds ``--json`` itself; for the file
    # surface it must NOT be forwarded. Strip any consumer ``--json`` from
    # the passthrough args so the dispatcher controls delivery.
    rest = args[1:]
    surface_args = [a for a in rest if a != "--json"]

    return dispatch(surface, surface_args)


if __name__ == "__main__":
    sys.exit(main())
