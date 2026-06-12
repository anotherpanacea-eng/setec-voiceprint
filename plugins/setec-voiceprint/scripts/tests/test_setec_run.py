#!/usr/bin/env python3
"""Tests for the R2 dispatcher + R3 error model + R4 validity gate.

Pins (spec §2/§3/§4/§5):

  * the dispatcher resolves each of the 9 consumer surfaces to its
    manifest script (table-driven, no per-script knowledge);
  * version_floor / bad_input / missing_dependency each produce the right
    ``reason_category`` + exit code + ``available: false`` envelope;
  * the pov_voice_profile file->stdout projection emits a valid stdout
    envelope (the consumer never touches ``--json-out``);
  * the R4 validator rejects NaN/inf (any numeric leaf) and a negative raw
    surprisal/entropy, excludes z-scores/derivations, leaves cosine RANGE to
    the computing surface, and (through build_output) turns a violation into
    an OutputValidityError that the dispatcher wraps as an internal_error.
"""

from __future__ import annotations

import io
import json
import contextlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # scripts/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import capabilities  # type: ignore  # noqa: E402
import setec_run  # type: ignore  # noqa: E402
from claim_license import ClaimLicense  # type: ignore  # noqa: E402
from output_schema import (  # type: ignore  # noqa: E402
    OutputValidityError,
    REASON_CATEGORIES,
    build_error_output,
    build_output,
    validate_results_bounds,
)

TEST_DATA = ROOT / "test_data"

REQUIRED_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "task_surface", "tool", "version", "available",
    "target", "baseline", "results", "claim_license",
    "claim_license_rendered", "warnings", "ai_status",
})

# The consumer surfaces that carry json_delivery (the nine R1 promoted for
# apodictic + the four promoted for setec-voicewright in 1.115.0), with
# their expected script module basename and delivery mode.
EXPECTED_SURFACES = {
    "variance_audit": ("variance_audit.py", "stdout"),
    "voice_distance": ("voice_distance.py", "stdout"),
    "idiolect_detector": ("idiolect_detector.py", "stdout"),
    "punctuation_cadence_audit": ("punctuation_cadence_audit.py", "stdout"),
    "narrative_decision_audit": ("narrative_decision_audit.py", "stdout"),
    "manuscript_audit": ("manuscript_audit.py", "stdout"),
    "repetition_audit": ("repetition_audit.py", "stdout"),
    "voice_profile": ("voice_profile.py", "file"),
    "pov_voice_profile": ("pov_voice_profile.py", "file"),
    "voice_fingerprint": ("voice_fingerprint.py", "stdout"),
    "mimicry_cosplay_audit": ("mimicry_cosplay_audit.py", "stdout"),
    "binoculars_audit": ("binoculars_audit.py", "stdout"),
    "general_imposters": ("general_imposters.py", "file"),
}


@pytest.fixture(scope="module")
def manifest():
    return capabilities.load_manifest()


def _dispatch_capture(surface, args, *, manifest, observed_version):
    """Run dispatch(), capturing the emitted envelope from stdout.
    Returns (exit_code, envelope_dict)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = setec_run.dispatch(
            surface, args, manifest=manifest, observed_version=observed_version,
        )
    out = buf.getvalue().strip()
    env = json.loads(out) if out else None
    return rc, env


# ---- surface -> script resolution (table-driven) -----------------------

def test_consumer_entries_are_exactly_the_thirteen(manifest):
    surfaces = setec_run.consumer_entries(manifest)
    assert set(surfaces) == set(EXPECTED_SURFACES)


@pytest.mark.parametrize("surface", sorted(EXPECTED_SURFACES))
def test_each_surface_resolves_to_expected_script(surface, manifest):
    entry = setec_run.consumer_entries(manifest)[surface]
    script = setec_run._script_abspath(entry)
    expected_basename, expected_delivery = EXPECTED_SURFACES[surface]
    assert script.name == expected_basename
    assert script.exists(), f"{surface} script missing at {script}"
    assert entry.get("json_delivery") == expected_delivery


# ---- R3: bad_input (unknown surface) -----------------------------------

def test_unknown_surface_is_bad_input_exit_2(manifest):
    rc, env = _dispatch_capture(
        "no_such_surface", [], manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_DISCOVERY == 2
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert env["schema_version"] == "1.0"
    # The R3 envelope is the success shape + the two additive keys.
    assert REQUIRED_TOP_LEVEL_KEYS <= set(env)
    assert {"reason", "reason_category"} <= set(env)


# ---- R3: version_floor -------------------------------------------------

def test_version_floor_below_floor_exit_2(manifest):
    # narrative_decision_audit floors at 1.107.0; pretend we run 1.0.0.
    rc, env = _dispatch_capture(
        "narrative_decision_audit", ["x.md"],
        manifest=manifest, observed_version="1.0.0",
    )
    assert rc == setec_run.EXIT_DISCOVERY == 2
    assert env["available"] is False
    assert env["reason_category"] == "version_floor"
    # BOTH the requested floor and the observed version are reported
    # machine-readably (no invented default).
    assert env["version_floor"] == {"required": "1.107.0", "observed": "1.0.0"}
    assert "1.107.0" in env["reason"] and "1.0.0" in env["reason"]


def test_version_floor_satisfied_proceeds_to_run(manifest, monkeypatch):
    # At/above floor, the dispatcher proceeds past the floor check. We stub
    # the actual run so the test stays dependency- and IO-free.
    called = {}

    def fake_stdout(surface, entry, args):
        called["surface"] = surface
        print(json.dumps({"ok": True}))
        return setec_run.EXIT_OK

    monkeypatch.setattr(setec_run, "_run_stdout_surface", fake_stdout)
    rc, env = _dispatch_capture(
        "variance_audit", ["x.md"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == 0
    assert called["surface"] == "variance_audit"


def test_version_satisfies_floor_semver():
    assert setec_run.version_satisfies_floor("1.112.0", "1.86.0")
    assert setec_run.version_satisfies_floor("1.107.0", "1.107.0")
    assert not setec_run.version_satisfies_floor("1.0.0", "1.107.0")
    assert setec_run.version_satisfies_floor("2.0.0", "1.999.0")
    # pre-release / build metadata is tolerated (stripped to the triple)
    assert setec_run.version_satisfies_floor("1.112.0-rc1", "1.112.0")


# ---- R3: missing_dependency --------------------------------------------

def test_missing_dependency_exit_3(manifest, monkeypatch):
    # Force scipy (idiolect_detector's required dep) to look absent.
    real = capabilities.is_installed
    monkeypatch.setattr(
        capabilities, "is_installed",
        lambda mod: False if mod == "scipy" else real(mod),
    )
    rc, env = _dispatch_capture(
        "idiolect_detector", ["--help"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_CONTRACT == 3
    assert env["available"] is False
    assert env["reason_category"] == "missing_dependency"
    assert env["missing_dependency"] == {"python": ["scipy"]}


# ---- R2/R3: script failure wrapping ------------------------------------

def test_script_nonzero_exit_wrapped_as_internal_error(manifest, monkeypatch):
    import subprocess

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "variance_audit", ["x.md"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_INTERNAL == 1
    assert env["reason_category"] == "internal_error"
    assert "boom" in env["reason"]


def test_script_exit_2_wrapped_as_policy_refused(manifest, monkeypatch):
    import subprocess

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 2, stdout="", stderr="Refusing to write: private",
        )

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "voice_profile", ["--baseline-dir", "x"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_CONTRACT == 3
    assert env["reason_category"] == "policy_refused"


def _refusal_envelope(reason_category="bad_input", *, tool="general_imposters"):
    """A surface-emitted structured R3 refusal envelope (the shape
    general_imposters now writes when it refuses below MIN_IMPOSTORS)."""
    return {
        "schema_version": "1.0",
        "task_surface": "voice_coherence",
        "tool": tool,
        "version": "9.9.9",
        "available": False,
        "target": {"path": None, "words": 0},
        "baseline": None,
        "results": {},
        "claim_license": None,
        "claim_license_rendered": None,
        "warnings": [],
        "ai_status": None,
        "reason": "Need at least 5 distinct impostor personas; got 3.",
        "reason_category": reason_category,
    }


def test_stdout_surface_honors_script_emitted_refusal(manifest, monkeypatch):
    """A stdout surface that emits its OWN available=False envelope with a
    reason_category is honored: the dispatcher re-emits it verbatim and
    derives the exit code from reason_category (bad_input on a known surface
    → EXIT_CONTRACT), rather than re-emitting at rc 0 or scraping stderr."""
    import subprocess

    env_out = _refusal_envelope(tool="variance_audit")

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(env_out), stderr="",
        )

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "variance_audit", ["x.md"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_CONTRACT == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"
    assert "impostor" in env["reason"].lower()  # script's own reason preserved


def test_file_surface_honors_script_emitted_refusal(manifest, monkeypatch):
    """The file-delivery analogue (general_imposters' real path): the script
    writes an available=False refusal to the injected --json-out; the
    dispatcher re-emits it and exits with the reason_category's code."""
    import subprocess

    def fake_run(cmd, **kw):
        out_idx = cmd.index("--json-out")
        Path(cmd[out_idx + 1]).write_text(
            json.dumps(_refusal_envelope()), encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "general_imposters",
        ["--target", "t.txt", "--manifest", "m.jsonl",
         "--candidate-persona", "blog"],
        manifest=manifest, observed_version="1.115.0",
    )
    assert rc == setec_run.EXIT_CONTRACT == 3
    assert env["available"] is False
    assert env["reason_category"] == "bad_input"


def test_available_false_without_category_synthesizes_internal_error(manifest, monkeypatch):
    """An available=False envelope that names no reason_category is a contract
    bug (a surface that says 'unavailable' without saying why). The dispatcher
    does NOT pass the malformed envelope through — it SYNTHESIZES an
    internal_error envelope so the consumer still gets a branchable
    reason_category (R3), and exits 1."""
    import subprocess

    bad = _refusal_envelope()
    del bad["reason_category"]

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(bad), stderr="",
        )

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "variance_audit", ["x.md"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_INTERNAL == 1
    assert env["available"] is False
    # The emitted envelope is branchable (synthesized), not the malformed one.
    assert env["reason_category"] == "internal_error"
    assert "reason_category" in env["reason"]


def test_available_false_unrecognized_category_synthesizes_internal_error(manifest, monkeypatch):
    """A present-but-unrecognized reason_category is equally unbranchable, so
    it is synthesized to internal_error rather than honored."""
    import subprocess

    bad = _refusal_envelope(reason_category="banana")

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(bad), stderr="",
        )

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "variance_audit", ["x.md"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_INTERNAL == 1
    assert env["reason_category"] == "internal_error"


def test_unparseable_stdout_wrapped_as_internal_error(manifest, monkeypatch):
    import subprocess

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="not json at all", stderr="",
        )

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "variance_audit", ["x.md"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_INTERNAL == 1
    assert env["reason_category"] == "internal_error"


def test_stdout_wrong_schema_version_wrapped_as_internal_error(manifest, monkeypatch):
    """A stdout surface that regresses to a non-1.0 schema_version must NOT be
    re-emitted as success — _extract_envelope requires schema_version == '1.0',
    the SAME gate the file-delivery artifact path applies (_is_envelope)."""
    import subprocess

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=json.dumps({"schema_version": "2.0", "tool": "variance_audit"}),
            stderr="",
        )

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "variance_audit", ["x.md"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_INTERNAL == 1
    assert env["reason_category"] == "internal_error"


# ---- SHOULD-FIX 3: robust stdout envelope parse ------------------------
# A surface may emit a non-JSON preamble on stdout (a model-download /
# progress line, an NLTK [nltk_data] notice) BEFORE the envelope. The
# whole-buffer json.loads then failed and a SUCCESSFUL run was mislabeled
# internal_error. The dispatcher now tolerates the preamble: it extracts the
# schema_version envelope object from stdout. Clean single-object stdout is
# unchanged (fast path); pure garbage is still internal_error.

def _success_envelope_json():
    """A minimal-but-valid schema_version 1.0 success envelope, serialized
    the way a stdout surface emits it (pretty, multi-line)."""
    env = build_output(
        task_surface="smoothing_diagnosis", tool="variance_audit",
        version="9.9.9", target_path="x.md", target_words=2480,
        baseline=None, results={"tier1": {"mtld": 92.5}},
        claim_license=ClaimLicense(
            task_surface="smoothing_diagnosis", licenses="x",
            does_not_license="y",
        ),
    )
    return json.dumps(env, indent=2)


def test_preamble_before_envelope_is_parsed_as_success(manifest, monkeypatch):
    import subprocess

    envelope_json = _success_envelope_json()
    polluted = "Downloading model...\nresolving shards: 100%\n" + envelope_json

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=polluted, stderr="")

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "variance_audit", ["x.md"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_OK == 0, env
    # The re-emitted envelope is exactly the surface's envelope — clean 12-key
    # success, no R3 error keys, no preamble residue.
    assert set(env.keys()) == REQUIRED_TOP_LEVEL_KEYS
    assert env["available"] is True
    assert env["tool"] == "variance_audit"
    assert env["results"]["tier1"]["mtld"] == 92.5
    assert "reason_category" not in env


def test_pure_garbage_stdout_is_internal_error(manifest, monkeypatch):
    import subprocess

    # No envelope object anywhere — not even an unrelated JSON object — so the
    # extractor returns None and the run is an internal_error (the SHOULD-FIX
    # explicitly preserves this behavior for genuinely-broken stdout).
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="Downloading model...\nstill not json\n", stderr="",
        )

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "variance_audit", ["x.md"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_INTERNAL == 1
    assert env["reason_category"] == "internal_error"


class TestExtractEnvelope:
    """Unit pins for the _extract_envelope helper (the parse robustness)."""

    def _env_str(self):
        return _success_envelope_json()

    def test_clean_single_object_fast_path(self):
        s = self._env_str()
        env = setec_run._extract_envelope(s)
        assert env is not None and env["schema_version"] == "1.0"

    def test_preamble_then_envelope(self):
        s = "Downloading model... [████] 100%\n" + self._env_str()
        env = setec_run._extract_envelope(s)
        assert env is not None and env["tool"] == "variance_audit"

    def test_no_envelope_returns_none(self):
        assert setec_run._extract_envelope("just some log lines\n") is None
        # A non-envelope JSON object (no schema_version) is NOT an envelope.
        assert setec_run._extract_envelope('{"hello": "world"}') is None
        # A bare JSON value (list) is not an envelope object either.
        assert setec_run._extract_envelope("[1, 2, 3]") is None

    def test_braces_inside_json_strings_do_not_confuse_matcher(self):
        # The envelope's reason/claim text could contain literal braces; the
        # balanced-brace scan must respect JSON string quoting + escapes.
        env = build_output(
            task_surface="smoothing_diagnosis", tool="variance_audit",
            version="9.9.9", target_path="x.md", target_words=10,
            baseline=None,
            results={"note": 'has a brace } and a quote \\" inside {nested}'},
            claim_license=ClaimLicense(
                task_surface="smoothing_diagnosis", licenses="x",
                does_not_license="y",
            ),
        )
        s = "preamble line\n" + json.dumps(env, indent=2)
        got = setec_run._extract_envelope(s)
        assert got is not None
        assert got["results"]["note"] == 'has a brace } and a quote \\" inside {nested}'

    def test_last_envelope_object_wins(self):
        # If the buffer somehow carries two envelope-shaped objects, the LAST
        # one (the real trailing envelope) is chosen over an earlier preamble
        # object.
        first = json.dumps({"schema_version": "1.0", "tool": "preamble"})
        second = self._env_str()
        env = setec_run._extract_envelope(first + "\n" + second)
        assert env is not None and env["tool"] == "variance_audit"


# ---- R2 stdout surface: real smoke (variance_audit) --------------------

def test_variance_audit_stdout_smoke(manifest, tmp_path):
    target = tmp_path / "target.txt"
    target.write_text(
        "The morning light came slow through the blinds. She did not move "
        "at first. Then she rose, crossed the cold floor, and put the "
        "kettle on. Outside a dog barked twice and went quiet. The kettle "
        "ticked as it warmed. She thought about the letter on the table, "
        "unopened since Tuesday. It would keep. Coffee first, then the "
        "rest of it. The rest of it could always wait a little longer "
        "than you thought.\n",
        encoding="utf-8",
    )
    rc, env = _dispatch_capture(
        "variance_audit", [str(target)],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == 0, env
    assert set(env.keys()) == REQUIRED_TOP_LEVEL_KEYS  # clean 12-key success
    assert env["schema_version"] == "1.0"
    assert env["tool"] == "variance_audit"
    assert env["task_surface"] == "smoothing_diagnosis"
    assert env["available"] is True
    # No --json-out variance leaks into the envelope; delivery owned by the
    # dispatcher.
    assert "reason_category" not in env


def test_dispatcher_strips_consumer_json_flag(manifest, monkeypatch):
    """The dispatcher owns the one consumer flag --json; it must not forward
    it (delivery is dispatcher-controlled)."""
    seen = {}

    def fake_stdout(surface, entry, args):
        seen["args"] = list(args)
        print(json.dumps({"ok": True}))
        return setec_run.EXIT_OK

    monkeypatch.setattr(setec_run, "_run_stdout_surface", fake_stdout)
    # main() does the --json stripping before dispatch().
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = setec_run.main(["variance_audit", "in.txt", "--json"])
    assert rc == 0
    assert "--json" not in seen["args"]
    assert seen["args"] == ["in.txt"]


# ---- R2/R3: pov_voice_profile file -> stdout projection ----------------

@pytest.mark.skipif(
    not (TEST_DATA / "federalist_pov_manifest.jsonl").exists(),
    reason="pov manifest fixture absent",
)
def test_pov_voice_profile_file_to_stdout_projection(manifest):
    """The file-delivery surface: the dispatcher injects a private
    --json-out, reads the artifact, and projects a valid stdout envelope.
    The consumer passes no --json-out. The injected tempdir is cleaned up.
    """
    pov_manifest = TEST_DATA / "federalist_pov_manifest.jsonl"
    rc, env = _dispatch_capture(
        "pov_voice_profile", ["--manifest", str(pov_manifest)],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == 0, env
    assert set(env.keys()) == REQUIRED_TOP_LEVEL_KEYS
    assert env["schema_version"] == "1.0"
    assert env["tool"] == "pov_voice_profile"
    assert env["task_surface"] == "voice_coherence"
    assert env["available"] is True
    # The projected envelope carries the consumer-facing results subset.
    assert "povs" in env["results"]


def test_pov_projection_cleans_up_tempdir(manifest, monkeypatch):
    """The injected private tempdir must be removed even on the happy path."""
    created = {}

    real_mkdtemp = setec_run.tempfile.mkdtemp

    def spy_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        created["dir"] = d
        return d

    monkeypatch.setattr(setec_run.tempfile, "mkdtemp", spy_mkdtemp)

    # Stub the subprocess to "write" the artifact the dispatcher will read.
    import subprocess

    def fake_run(cmd, **kw):
        # The injected --json-out path is the last arg. Write a VALID
        # schema_version 1.0 envelope so the dispatcher's artifact check
        # passes (the happy path this test exercises).
        out_idx = cmd.index("--json-out")
        out_path = Path(cmd[out_idx + 1])
        out_path.write_text(
            json.dumps({"schema_version": "1.0", "tool": "pov_voice_profile"}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "pov_voice_profile", ["--manifest", "m.jsonl"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == 0
    assert env["tool"] == "pov_voice_profile"
    assert "dir" in created
    assert not Path(created["dir"]).exists(), "tempdir not cleaned up"


def test_file_surface_non_envelope_artifact_internal_error(manifest, monkeypatch):
    """A regressed file-delivery surface that writes a non-envelope artifact
    (no schema_version 1.0) must NOT slip through as success — the dispatcher
    wraps it as internal_error rather than exit 0 with a bogus payload."""
    import subprocess

    def fake_run(cmd, **kw):
        out_idx = cmd.index("--json-out")
        out_path = Path(cmd[out_idx + 1])
        # Valid JSON, but NOT a schema_version 1.0 envelope.
        out_path.write_text(
            json.dumps({"tool": "pov_voice_profile"}), encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "pov_voice_profile", ["--manifest", "m.jsonl"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_INTERNAL == 1
    assert env["reason_category"] == "internal_error"
    assert "schema_version" in env["reason"]


def test_argparse_usage_error_wrapped_as_bad_input(manifest, monkeypatch):
    """Exit 2 is overloaded: argparse usage errors (unrecognized flag, etc.)
    emit a 'usage:' line and must map to bad_input, NOT the voice-clone
    privacy refusal (policy_refused). Consumers branch on reason_category."""
    import subprocess

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 2, stdout="",
            stderr=(
                "usage: voice_profile.py [-h] ...\n"
                "voice_profile.py: error: unrecognized arguments: --bogus"
            ),
        )

    monkeypatch.setattr(setec_run, "_run_subprocess", fake_run)
    rc, env = _dispatch_capture(
        "voice_profile", ["--baseline-dir", "x", "--bogus"],
        manifest=manifest, observed_version="1.112.0",
    )
    assert rc == setec_run.EXIT_CONTRACT == 3
    assert env["reason_category"] == "bad_input"


# ---- R4: output-validity bounds gate -----------------------------------

class TestR4ValidityGate:
    def _lic(self):
        return ClaimLicense(
            task_surface="smoothing_diagnosis",
            licenses="x", does_not_license="y",
        )

    def test_nan_rejected(self):
        with pytest.raises(OutputValidityError, match="not finite"):
            validate_results_bounds({"shannon_entropy_bits": float("nan")})

    def test_inf_rejected(self):
        with pytest.raises(OutputValidityError, match="not finite"):
            validate_results_bounds({"some_metric": float("inf")})

    def test_cosine_range_not_checked_at_build_gate(self):
        # R4 review: the cosine RANGE arm was removed (it leaf-matched and
        # essentially never fired on the real nested stat-dict shape). Range
        # is now clamped at the computing surface; the build gate only keeps
        # the unconditional NaN/inf check for cosines. A cosine value just
        # outside [-1, 1] (the float-epsilon mode) is NOT rejected here.
        validate_results_bounds({"adjacent_cosine": 1.0000000002})  # no raise
        validate_results_bounds({"cosine_similarity": 1.7})         # no raise
        # But a NaN cosine is still caught (the real corruption mode).
        with pytest.raises(OutputValidityError, match="not finite"):
            validate_results_bounds({"adjacent_cosine": float("nan")})

    def test_negative_surprisal_rejected(self):
        with pytest.raises(OutputValidityError, match="surprisal"):
            validate_results_bounds({"surprisal_bits": -0.5})

    def test_out_of_range_probability_rejected(self):
        with pytest.raises(OutputValidityError, match="probability"):
            validate_results_bounds({"token_probability": 1.5})

    def test_zscored_entropy_below_baseline_passes(self):
        # BLOCKER 1: function_word_grammar_audit / stance_modality_audit emit a
        # baseline_comparison block of z-scored entropies that are signed and
        # routinely NEGATIVE (target entropy below baseline mean). A z-score is
        # a standardization, not a raw entropy, so the >= 0 surprisal check
        # must NOT fire on it. This mirrors the real emit shape.
        validate_results_bounds({
            "baseline_comparison": {
                "available": True,
                "z_function_bigram_entropy": -2.3,
                "z_preposition_entropy": -0.8,
                "z_subordinator_entropy": -1.1,
                "z_stance_entropy": -3.0,
            }
        })  # no raise
        # A genuinely-negative RAW surprisal/entropy is STILL rejected.
        with pytest.raises(OutputValidityError, match="surprisal"):
            validate_results_bounds({"surprisal_bits": -0.5})
        with pytest.raises(OutputValidityError, match="surprisal/entropy"):
            validate_results_bounds({"shannon_entropy_bits": -0.1})

    def test_zscored_entropy_nan_still_rejected(self):
        # The finiteness check is unconditional: a NaN z-score is still caught
        # even though its range is left unchecked.
        with pytest.raises(OutputValidityError, match="not finite"):
            validate_results_bounds(
                {"baseline_comparison": {"z_stance_entropy": float("nan")}}
            )

    def test_log_probability_is_not_treated_as_probability(self):
        # Regression: fast_detect_curvature emits actual_log_prob_sum_nats
        # (a negative log-probability sum). A log/sum transform of a
        # probability is NOT in [0, 1]; the gate must leave it alone.
        validate_results_bounds({"actual_log_prob_sum_nats": -100.0})
        validate_results_bounds({"log_prob": -3.2})
        validate_results_bounds({"sampled_log_prob_mean_nats": -42.0})

    def test_surprisal_ratio_and_delta_are_not_bounded(self):
        # A ratio/delta of a surprisal can be negative; only the RAW
        # surprisal/entropy is bounded >= 0.
        validate_results_bounds({"surprisal_ratio": -0.7})
        validate_results_bounds({"entropy_delta": -1.3})
        # but a raw surprisal below 0 is still rejected
        with pytest.raises(OutputValidityError, match="surprisal"):
            validate_results_bounds({"surprisal_bits": -0.5})

    def test_log_prob_nan_still_rejected(self):
        # NaN/inf is invalid even for an otherwise-unchecked transformed
        # field — the finiteness guard runs before the transform guard.
        with pytest.raises(OutputValidityError, match="not finite"):
            validate_results_bounds({"actual_log_prob_sum_nats": float("nan")})

    def test_bools_are_skipped(self):
        validate_results_bounds({"available": True, "windowed": False})

    def test_nested_and_list_values_checked(self):
        # A numeric in a list under a surprisal key inherits the bound.
        with pytest.raises(OutputValidityError, match="surprisal"):
            validate_results_bounds({"surprisal_bits": [4.2, 3.1, -2.0]})
        # A NaN anywhere in a list is caught unconditionally.
        with pytest.raises(OutputValidityError, match="not finite"):
            validate_results_bounds({"adjacent_cosine": [0.2, float("nan")]})
        # Nested dict.
        with pytest.raises(OutputValidityError, match="not finite"):
            validate_results_bounds({"tier1": {"mtld": float("nan")}})

    def test_valid_payload_passes(self):
        validate_results_bounds({
            "tier1": {"shannon_entropy_bits": 9.81, "mtld": 92.5},
            "tier3": {"adjacent_cosine": 0.42},
            "surprisal_bits": 4.2,
            "p_value": 0.03,
        })

    def test_build_output_rejects_out_of_bounds_via_gate(self):
        # A NaN anywhere in results trips the gate through build_output.
        with pytest.raises(OutputValidityError):
            build_output(
                task_surface="smoothing_diagnosis", tool="t", version="0",
                target_path="x", target_words=10, baseline=None,
                results={"tier3": {"adjacent_cosine": float("nan")}},
                claim_license=self._lic(),
            )
        # A negative raw surprisal likewise.
        with pytest.raises(OutputValidityError):
            build_output(
                task_surface="smoothing_diagnosis", tool="t", version="0",
                target_path="x", target_words=10, baseline=None,
                results={"surprisal_bits": -1.0},
                claim_license=self._lic(),
            )

    def test_build_output_passes_zscored_entropy(self):
        # BLOCKER 1 end-to-end: a below-baseline z-scored entropy must NOT
        # crash the build_output path the audits use.
        env = build_output(
            task_surface="smoothing_diagnosis", tool="t", version="0",
            target_path="x", target_words=10, baseline=None,
            results={"baseline_comparison": {
                "available": True, "z_function_bigram_entropy": -2.3}},
            claim_license=self._lic(),
        )
        assert env["available"] is True
        assert (
            env["results"]["baseline_comparison"]["z_function_bigram_entropy"]
            == -2.3
        )

    def test_build_output_can_bypass_gate(self):
        # validate_bounds=False is the documented escape hatch.
        env = build_output(
            task_surface="smoothing_diagnosis", tool="t", version="0",
            target_path="x", target_words=10, baseline=None,
            results={"surprisal_bits": -1.0},
            claim_license=self._lic(), validate_bounds=False,
        )
        assert env["available"] is True

    def test_unavailable_envelope_skips_gate(self):
        # available=False legitimately carries partial/empty data; the gate
        # does not run.
        env = build_output(
            task_surface="smoothing_diagnosis", tool="t", version="0",
            target_path="x", target_words=0, baseline=None,
            results={"surprisal_bits": -99.0}, claim_license=None,
            available=False, warnings=["short"],
        )
        assert env["available"] is False


# ---- R3 builder unit checks --------------------------------------------

class TestErrorBuilder:
    def test_error_envelope_has_success_keys_plus_additive(self):
        env = build_error_output(
            task_surface=None, tool="setec_run", version="1.0.0",
            reason="nope", reason_category="bad_input",
        )
        assert REQUIRED_TOP_LEVEL_KEYS <= set(env)
        assert env["available"] is False
        assert env["claim_license"] is None
        assert env["results"] == {}
        assert env["reason"] == "nope"
        assert env["reason_category"] == "bad_input"
        assert env["schema_version"] == "1.0"

    def test_unknown_reason_category_raises(self):
        with pytest.raises(ValueError, match="Unknown reason_category"):
            build_error_output(
                task_surface=None, tool="t", version="0",
                reason="x", reason_category="not_a_category",
            )

    def test_all_categories_accepted(self):
        for cat in REASON_CATEGORIES:
            env = build_error_output(
                task_surface=None, tool="t", version="0",
                reason="x", reason_category=cat,
            )
            assert env["reason_category"] == cat

    def test_extra_collision_raises(self):
        with pytest.raises(ValueError, match="collides"):
            build_error_output(
                task_surface=None, tool="t", version="0",
                reason="x", reason_category="bad_input",
                extra={"available": "stomp"},
            )
