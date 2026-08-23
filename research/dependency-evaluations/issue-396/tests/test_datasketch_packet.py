"""Focused tests for the Voiceprint #396 datasketch packet worker."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers" / "datasketch_probe.py"
REPO_ROOT = ROOT.parents[2]
SCRIPTS = REPO_ROOT / "plugins" / "setec-voiceprint" / "scripts"

# The worker reads `datasketch`'s installed metadata at import time, so without
# the packet's locked environment this module cannot even be loaded. Skip the
# file rather than erroring, matching test_trafilatura_packet.py.
_missing = [
    name for name in ("datasketch",) if importlib.util.find_spec(name) is None
]
if _missing:
    pytestmark = pytest.mark.skip(
        reason="issue-396 locked dependencies missing: " + ", ".join(_missing)
    )


def _load_worker():
    spec = importlib.util.spec_from_file_location("issue396_datasketch_probe", WORKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = None if _missing else _load_worker()


def _request(*, scheme: str = "default") -> dict:
    duplicate = " ".join(f"sharedword{i}" for i in range(80))
    unrelated = " ".join(f"differentword{i}" for i in range(80))
    return {
        "schema": probe.SCHEMA,
        "action": "accuracy",
        "expected_environment": {
            key: probe._environment().get(key)
            for key in (
                "datasketch", "implementation", "numpy", "python", "rapidfuzz", "scipy",
            )
        },
        "scheme": scheme,
        "threshold": 0.85,
        "num_perm": 128,
        "shingle_size": 5,
        "records": [
            ["duplicate-a", duplicate],
            ["duplicate-b", duplicate],
            ["unrelated", unrelated],
        ],
    }


def test_traced_real_seam_matches_unwrapped_control_and_exposes_layers():
    result = probe.evaluate_request(_request())

    assert result["traced_matches_control"] is True
    assert result["control_result_sha256"] == result["traced_result_sha256"]
    assert result["dedup_result"]["kept"] == ["duplicate-a", "unrelated"]
    assert result["dedup_result"]["dropped"] == ["duplicate-b"]
    expected = [["duplicate-a", "duplicate-b"]]
    assert result["layers"]["raw_lsh"] == expected
    # #407 removed estimated MinHash Jaccard from the production decision.
    assert result["layers"]["estimated_pass"] == []
    assert result["layers"]["exact_confirmed"] == expected
    assert result["layers"]["co_cluster"] == expected
    assert result["estimated_scores"] == []


def test_signature_is_deterministic_and_bound_to_shipped_normalization():
    first = probe.evaluate_request(_request())["signature"]
    second = probe.evaluate_request(_request())["signature"]

    assert first == second
    assert first["aggregate_sha256"]
    assert first["record_count"] == 3
    assert first["version"] == probe._environment()["datasketch"]
    assert first["num_perm"] == 128
    assert first["seed"] == 1
    assert first["shingle_size"] == 5
    assert first["normalization"].startswith("near_dup_dedup.shingles/")
    assert len(first["sample"]["hashvalues"]) == 128


def test_rapidfuzz_primary_cutoff_is_fixed_and_candidate_scoped():
    result = probe.evaluate_request(_request())["rapidfuzz"]
    if not result["available"]:
        pytest.skip("RapidFuzz is not installed in this environment")

    assert result["primary_cutoff"] == 85
    assert set(result["cutoffs"]) == {"80", "85", "90"}
    assert result["cutoffs"]["85"]["role"] == "primary"
    assert result["cutoffs"]["80"]["role"] == "exploratory"
    assert result["cutoffs"]["90"]["role"] == "exploratory"
    primary = result["cutoffs"]["85"]
    assert primary["passing_pairs"] == [["duplicate-a", "duplicate-b"]]
    assert primary["candidate_conditioned_metrics"]["precision"] == 1.0
    assert primary["candidate_conditioned_metrics"]["recall"] == 1.0


def test_legacy_adapter_forces_explicit_seed_and_scheme_on_compatible_class():
    class FakeMinHash:
        def __init__(
            self,
            num_perm=128,
            seed=999,
            scheme=None,
        ):
            self.num_perm = num_perm
            self.seed = seed
            self.scheme = scheme

        def jaccard(self, other):
            return 1.0

    adapted = probe._make_minhash_adapter(
        FakeMinHash, scheme="legacy", trace=None,
    )
    instance = adapted(num_perm=32, seed=77)
    assert instance.num_perm == 32
    assert instance.seed == 1
    assert instance.scheme == "legacy"


def test_legacy_request_runs_on_datasketch_2_or_refuses_older_version_cleanly():
    real_minhash, _ = probe._real_classes()
    if probe._supports_scheme(real_minhash):
        result = probe.evaluate_request(_request(scheme="legacy"))
        assert result["parameters"]["scheme"] == "legacy"
        assert result["signature"]["scheme"] == "legacy"
        assert result["traced_matches_control"] is True
    else:
        with pytest.raises(ValueError, match="requires datasketch 2.0.0 or newer"):
            probe.evaluate_request(_request(scheme="legacy"))


def test_cli_emits_one_canonical_json_object_and_no_diagnostics():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SCRIPTS)
    completed = subprocess.run(
        [sys.executable, str(WORKER)],
        input=probe.canonical_json_bytes(_request()),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode()
    assert completed.stderr == b""
    parsed = json.loads(completed.stdout)
    assert completed.stdout == probe.canonical_json_bytes(parsed)
    assert parsed["schema"] == probe.SCHEMA
    assert parsed["traced_matches_control"] is True


def test_scale_action_calls_real_seam_once_and_reports_known_families(monkeypatch):
    request = _request()
    request["action"] = "scale"
    request["known_families"] = [["duplicate-a", "duplicate-b"]]
    calls = 0
    real_dedup = probe.ndd.dedup_records

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_dedup(*args, **kwargs)

    monkeypatch.setattr(probe.ndd, "dedup_records", counted)
    result = probe.evaluate_request(request)

    assert calls == 1
    assert result["action"] == "scale"
    assert result["elapsed_seconds"] >= 0
    assert result["dedup_result"]["dropped"] == ["duplicate-b"]
    assert result["layers"]["co_cluster"] == [
        ["duplicate-a", "duplicate-b"],
    ]
    assert result["known_families"]["count"] == 1
    assert result["known_families"]["caught"] == 1
    assert result["known_families"]["missed"] == 0
    assert result["known_families"]["observations"] == [{
        "family_index": 0,
        "members": ["duplicate-a", "duplicate-b"],
        "co_clustered": True,
        "observed_components": [["duplicate-a", "duplicate-b"]],
    }]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"schema": "wrong"}, "schema mismatch"),
        ({"scheme": "affine64"}, "scheme must be"),
        ({"threshold": float("nan")}, "threshold must be finite"),
        ({"records": [["same", "a"], ["same", "b"]]}, "duplicate id"),
        ({"known_families": [["duplicate-a", "missing"]]}, "unknown ids"),
    ],
)
def test_request_validation_fails_closed(change, message):
    request = _request()
    request.update(change)
    with pytest.raises(ValueError, match=message):
        probe.evaluate_request(request)


def test_request_refuses_wrong_locked_environment_identity():
    request = _request()
    request["expected_environment"]["datasketch"] = "0.0.0"
    with pytest.raises(ValueError, match="environment mismatch"):
        probe.evaluate_request(request)
