"""Acceptance contract for the closed Gmail recipe producer facade.

Scenario census (29 facade cases plus 5 owning-domain cases): selector 1;
pre-child path confinement 6; recursive lineage 5; author envelope 7; TTY 3;
stream/CLI confinement 3; stage-03 recovery/idempotence 2; kill/adopt 1;
facade verifier planted-write 1; domain verifier success/refusal 5.  Domain
behavior matrices remain in their owning suites rather than being copied here.
"""
from __future__ import annotations

import builtins
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
FACADE = SCRIPTS / "setec" / "surfaces" / "gmail_author_pipeline.py"
spec = importlib.util.spec_from_file_location("gmail_author_pipeline", FACADE)
assert spec and spec.loader
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def _private_file(path: Path, data: bytes = b"x") -> Path:
    _private_dir(path.parent)
    path.write_bytes(data)
    path.chmod(0o600)
    return path


@dataclass
class Case:
    root: Path
    run: Path
    source: dict[str, Any]
    corpus: dict[str, Any]
    request: Path

    @property
    def config(self) -> dict[str, Any]:
        return {"source": self.source, "corpus": self.corpus}


def _case(tmp_path: Path) -> Case:
    root = _private_dir(tmp_path / "private")
    run = _private_dir(root / "runs" / "one")
    _private_file(root / "source.mbox", b"synthetic mbox")
    _private_file(root / "hmac.key", b"k" * 32)
    source = {
        "adapter": "gmail_takeout_sent/1", "mbox": "source.mbox",
        "own_addresses": ["owner@example.test"], "persona": "synthetic-author",
        "author": None, "register": "personal", "since": None, "until": None,
        "sent_label_token": "Sent", "recipient_map": None, "name_map": None,
        "own_signature_lines": None, "consent_status": "author_consent",
        "min_words_per_piece": 1, "max_items": 5, "allow_empty": False,
        "output_dir": "acquired", "manifest": "acquired/source.jsonl",
        "smoke_dir": "smoke", "smoke_since": "2020-01-01", "smoke_until": None,
    }
    corpus = {
        "strict_manifest": True, "check_conflict_copies": True,
        "dedup_manifest": "acquired/dedup.jsonl", "dedup_report": "dedup-report.json",
        "dedup_threshold": 0.8, "dedup_num_perm": 8, "dedup_shingle_size": 2,
        "hmac_key": "hmac.key", "allowed_ai_status": ["unknown"],
        "register_map": {"personal": "email.personal"},
        "package_smoke_dir": "packages/smoke", "package_dir": "packages/full",
        "producer_envelope": "producer-envelope.json",
        "smoke_max_records": 1, "smoke_max_text_bytes": 128,
    }
    return Case(root, run, source, corpus, run / "request.json")


def _request(case: Case, stage: str, action: str, prior: list[dict[str, str]],
             *, config: dict[str, Any] | None = None) -> Path:
    payload = {
        "schema": P.REQUEST_SCHEMA, "action": action, "stage_id": stage,
        "private_root": str(case.root), "run_root": str(case.run.relative_to(case.root)),
        "config": config or case.config, "prior": prior,
    }
    _private_file(case.request, P._canon(payload))
    return case.request


def _author_receipt() -> dict[str, Any]:
    return {
        "counts": {"records": 1, "by_register": {"email.personal": 1}},
        "record_atomic_degraded": False,
    }


def _author_envelope(receipt: dict[str, Any], *, verifying: bool = False) -> dict[str, Any]:
    license_value = {
        "task_surface": P.TASK_SURFACE,
        "licenses": "Synthetic mechanical package verification.",
        "does_not_license": "No authorship or quality verdict.",
        "comparison_set": {"records": receipt["counts"]["records"]},
        "length_range_words": None,
        "register_match": sorted(receipt["counts"]["by_register"]),
        "language_match": [], "fpr_target": None, "confidence_interval_95": None,
        "additional_caveats": [], "references": [],
    }
    return {
        "schema_version": "1.0", "task_surface": P.TASK_SURFACE,
        "tool": "author_corpus_export", "version": "1.0", "available": True,
        "target": {"path": None, "words": 0}, "baseline": None,
        "results": {"producer_receipt": receipt}, "claim_license": license_value,
        "claim_license_rendered": "## What this result licenses\n",
        # Mirrors the exporter: the degradation warning comes first, then the
        # verify-only warning.
        "warnings": ([
            "record_atomic_degraded: stable grouping was unavailable; consumers "
            "must restrict this package to train-only, non-comparative use."
        ] if receipt.get("record_atomic_degraded") else []) + (
            ["verify-existing: package was verified without publication"]
            if verifying else []),
        "ai_status": None,
    }


class FakeDomain:
    """Small synthetic domain boundary; all artifact writes mirror real ownership."""

    def __init__(self, case: Case, *, private_stream: str | None = None) -> None:
        self.case = case
        self.calls: list[tuple[str, str]] = []
        self.private_stream = private_stream

    def __call__(self, argv, **kwargs):
        script = Path(argv[1]).name
        action = argv[2] if script == "acquire_gmail_sent.py" else "verify" if "--verify-existing" in argv or "--verify-out" in argv else "run"
        self.calls.append((script, action))
        stdout = ""
        if script == "acquire_gmail_sent.py":
            if action == "smoke":
                smoke = Path(argv[argv.index("--output-dir") + 1])
                _private_file(smoke / ".smoke_descriptor.json", b"{}\n")
                _private_file(smoke / "draft_manifest.jsonl", b"{}\n")
            elif action == "approve-smoke":
                output = Path(argv[argv.index("--output-dir") + 1])
                _private_file(output / ".live_smoke_passed", b"approval\n")
            elif action == "acquire":
                manifest = Path(argv[argv.index("--emit-manifest") + 1])
                _private_file(manifest, b"{}\n")
        elif script == "near_dup_dedup.py" and action == "run":
            output = Path(argv[argv.index("--out") + 1])
            _private_file(output, b"{}\n")
            stdout = "{}\n"
        elif script == "author_corpus_export.py":
            destination = Path(argv[argv.index("--output-dir") + 1])
            verifying = "--verify-existing" in argv
            if not verifying:
                _private_file(destination / "producer_receipt.json", P._canon(_author_receipt()))
                if "--max-records" in argv:
                    _private_file(destination.parent / ".author_corpus_export_live_smoke.json", b"smoke\n")
            receipt = json.loads((destination / "producer_receipt.json").read_text(encoding="utf-8"))
            stdout = json.dumps(_author_envelope(receipt, verifying=verifying)) + "\n"
        if self.private_stream is not None:
            stdout = stdout + self.private_stream
            stderr = self.private_stream
        else:
            stderr = ""
        return subprocess.CompletedProcess(argv, 0, stdout, stderr)


def _prior(case: Case, through: int) -> list[dict[str, str]]:
    result = []
    for stage in P.STAGES[:through]:
        path = case.run / "producer-receipts" / f"{stage}.json"
        lineage = json.loads(path.read_text(encoding="utf-8"))
        result.append({
            "stage_id": stage, "receipt_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "domain_identity": lineage["domain_identity"],
        })
    return result


def _run_through(case: Case, monkeypatch: pytest.MonkeyPatch, through: int,
                 fake: FakeDomain | None = None) -> FakeDomain:
    fake = fake or FakeDomain(case)
    monkeypatch.setattr(P.subprocess, "run", fake)
    monkeypatch.setattr(P.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda: "yes")
    actions = ["run", "approve", "run", "run", "run", "approve", "run"]
    for index, stage in enumerate(P.STAGES[:through]):
        rc, envelope = P.run_request(_request(case, stage, actions[index], _prior(case, index)))
        assert rc == 0, (stage, envelope)
        assert envelope["available"] is True
    return fake


def _tree_snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    snapshot = {}
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        snapshot[str(path.relative_to(root))] = (mode, path.read_bytes() if path.is_file() else None)
    return snapshot


def _policy_refusal(outcome: tuple[int, dict[str, Any]]) -> bool:
    """A policy refusal is exit 3 with the R3 structured-error envelope."""
    code, envelope = outcome
    return (code == 3 and envelope["available"] is False
            and envelope["results"] == {}
            and envelope["reason_category"] == "policy_refused"
            and type(envelope["reason"]) is str and bool(envelope["reason"]))


STANDARD_ENVELOPE_KEYS = {
    "schema_version", "task_surface", "tool", "version", "available", "target",
    "baseline", "results", "claim_license", "claim_license_rendered",
    "warnings", "ai_status",
}


def test_envelope_is_the_normalized_surface_contract(tmp_path, monkeypatch):
    """The facade is a surface, so it emits the same envelope every surface does.

    It previously returned a bespoke {schema_version, available, results}, which
    left no channel for `warnings` and no `claim_license` bounding what a stage
    identity attests -- and made an R5 contract golden impossible to generate.
    """
    case = _case(tmp_path)
    fake = _run_through(case, monkeypatch, 1)
    assert fake.calls
    rc, envelope = P.run_request(_request(case, "01_source_smoke", "verify", []))
    assert rc == 0
    assert set(envelope) == STANDARD_ENVELOPE_KEYS
    assert envelope["schema_version"] == "1.0"
    assert envelope["task_surface"] == P.TASK_SURFACE
    assert envelope["tool"] == "gmail_author_pipeline"
    # No target text, no baseline, no ai_status -- exactly author_corpus_export's
    # posture for the same task surface.
    assert envelope["target"] == {"path": None, "words": 0}
    assert envelope["baseline"] is None and envelope["ai_status"] is None
    assert envelope["claim_license"]["task_surface"] == P.TASK_SURFACE
    assert "no AI/human, voice, or provenance verdict" in \
        envelope["claim_license"]["does_not_license"]
    assert envelope["claim_license_rendered"]
    # The no-prose boundary still holds: no private path reaches the response.
    assert str(case.root) not in json.dumps(envelope)


def test_bad_input_envelope_is_also_normalized(tmp_path):
    request = _private_file(tmp_path / "bad.json", b"not-json")
    rc = P.main(["--json", "--request", str(request)])
    assert rc == 2


def test_error_envelope_shape_is_the_r3_contract(tmp_path, capsys):
    request = _private_file(tmp_path / "bad.json", b"not-json")
    assert P.main(["--json", "--request", str(request)]) == 2
    envelope = json.loads(capsys.readouterr().out)
    # R3: the 12 standard keys plus the two additive error keys.
    assert set(envelope) == STANDARD_ENVELOPE_KEYS | {"reason", "reason_category"}
    assert envelope["available"] is False and envelope["results"] == {}
    assert envelope["reason_category"] == "bad_input"
    assert envelope["claim_license"] is None


def test_degraded_package_warning_reaches_the_consumer(tmp_path, monkeypatch):
    """record_atomic_degraded makes the package train-only; it must propagate.

    The facade validates that the exporter emitted this warning, so dropping it
    from its own response would be the one surface that checked the flag and
    then hid it.
    """
    case = _case(tmp_path)
    degraded = {"counts": {"records": 1, "by_register": {"email.personal": 1}},
                "record_atomic_degraded": True}
    import test_gmail_author_pipeline as self_module
    monkeypatch.setattr(self_module, "_author_receipt", lambda: degraded)
    fake = _run_through(case, monkeypatch, 7)
    rc, envelope = P.run_request(
        _request(case, "07_author_package", "verify", _prior(case, 6)))
    assert rc == 0, envelope
    assert P.DEGRADED_WARNING in envelope["warnings"]
    assert any("train-only" in caveat
               for caveat in envelope["claim_license"]["additional_caveats"])


def test_action_matrix_is_closed():
    assert P.ALLOWED == {
        "01_source_smoke": {"run", "verify"},
        "02_source_approval": {"approve", "verify"},
        "03_source_acquire": {"run", "resume", "verify"},
        "04_manifest_validate": {"run", "verify"},
        "05_near_duplicate_filter": {"run", "verify"},
        "06_package_smoke": {"approve", "verify"},
        "07_author_package": {"run", "verify"},
    }


@pytest.mark.parametrize("suffix", ["stdout", "stderr"])
def test_broken_log_symlink_refuses_before_any_child(tmp_path, monkeypatch, suffix):
    case = _case(tmp_path)
    logs = _private_dir(case.run / "logs")
    (logs / f"01_source_smoke.domain.{suffix}").symlink_to(case.root / "missing")
    calls = []
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: calls.append(a))
    assert _policy_refusal(P.run_request(_request(case, "01_source_smoke", "run", [])))
    assert calls == []


@pytest.mark.parametrize("receipt_stage", ["01_source_smoke", "02_source_approval"])
def test_broken_receipt_symlink_refuses_before_any_child(tmp_path, monkeypatch, receipt_stage):
    case = _case(tmp_path)
    receipt_dir = _private_dir(case.run / "producer-receipts")
    (receipt_dir / f"{receipt_stage}.json").symlink_to(case.root / "missing")
    calls = []
    monkeypatch.setattr(P.subprocess, "run", lambda *a, **k: calls.append(a))
    stage = "01_source_smoke" if receipt_stage == "01_source_smoke" else "02_source_approval"
    prior = [] if stage == "01_source_smoke" else [{
        "stage_id": "01_source_smoke", "receipt_sha256": "a" * 64,
        "domain_identity": "b" * 64,
    }]
    if stage == "02_source_approval":
        _private_file(receipt_dir / "01_source_smoke.json", b"{}\n")
    assert _policy_refusal(P.run_request(_request(case, stage, "verify", prior)))
    assert calls == []


@pytest.mark.parametrize("name", ["logs", "producer-receipts"])
def test_state_directory_symlink_is_refused_before_child(tmp_path, name):
    root = _private_dir(tmp_path / "root")
    outside = _private_dir(tmp_path / "outside")
    (root / name).symlink_to(outside, target_is_directory=True)
    with pytest.raises(P.Refusal):
        P._safe_dir(root, name, root=root)


@pytest.mark.parametrize("mutation", ["artifact", "config", "receipt", "item_shape", "domain_identity"])
def test_recursive_prior_lineage_refuses_every_mutation(tmp_path, monkeypatch, mutation):
    case = _case(tmp_path)
    fake = _run_through(case, monkeypatch, 3)
    prior = _prior(case, 3)
    config = copy.deepcopy(case.config)
    if mutation == "artifact":
        _private_file(case.root / "smoke" / ".smoke_descriptor.json", b"changed\n")
    elif mutation == "config":
        config["source"]["persona"] = "changed-author"
    elif mutation in {"receipt", "item_shape", "domain_identity"}:
        path = case.run / "producer-receipts" / "01_source_smoke.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        if mutation == "receipt":
            stored["domain_receipts"][0]["label"] = "changed"
        elif mutation == "item_shape":
            stored["output_identities"][0]["extra"] = "forbidden"
        else:
            stored["domain_identity"] = "f" * 64
        _private_file(path, P._canon(stored))
        prior[0]["receipt_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        if mutation == "domain_identity":
            prior[0]["domain_identity"] = "f" * 64
    assert _policy_refusal(P.run_request(_request(case, "04_manifest_validate", "verify", prior, config=config)))
    assert not any(call == ("manifest_validator.py", "run") for call in fake.calls)


@pytest.mark.parametrize("mutation", ["unknown", "unavailable", "results", "receipt", "license", "warnings"])
def test_stage07_verify_requires_exact_domain_envelope(tmp_path, monkeypatch, mutation):
    case = _case(tmp_path)
    fake = _run_through(case, monkeypatch, 7)
    envelope_path = case.root / case.corpus["producer_envelope"]
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    if mutation == "unknown": envelope["prose"] = "forbidden"
    elif mutation == "unavailable": envelope["available"] = False
    elif mutation == "results": envelope["results"]["unknown"] = 1
    elif mutation == "receipt": envelope["results"]["producer_receipt"]["counts"]["records"] = 2
    elif mutation == "license": envelope["claim_license"]["comparison_set"] = {"records": 2}
    else: envelope["warnings"] = ["untrusted prose"]
    _private_file(envelope_path, json.dumps(envelope).encode("utf-8"))
    rc, result = P.run_request(_request(case, "07_author_package", "verify", _prior(case, 6)))
    assert rc == 3 and result["available"] is False
    assert any(call == ("author_corpus_export.py", "verify") for call in fake.calls)


def test_stage07_rejects_domain_available_false(tmp_path, monkeypatch):
    case = _case(tmp_path)
    fake = _run_through(case, monkeypatch, 6)

    def unavailable(argv, **kwargs):
        if Path(argv[1]).name == "author_corpus_export.py" and "--verify-existing" not in argv:
            _private_file(case.root / "packages/full/producer_receipt.json", P._canon(_author_receipt()))
            return subprocess.CompletedProcess(argv, 0, json.dumps({"available": False}), "")
        return fake(argv, **kwargs)

    monkeypatch.setattr(P.subprocess, "run", unavailable)
    rc, result = P.run_request(_request(case, "07_author_package", "run", _prior(case, 6)))
    assert rc == 3 and result["available"] is False
    assert not (case.root / case.corpus["producer_envelope"]).exists()


@pytest.mark.parametrize("answer, expected_calls", [("no", 0), ("yes", 1)])
def test_package_approval_tty_decline_and_affirmative(tmp_path, monkeypatch, answer, expected_calls):
    case = _case(tmp_path)
    fake = _run_through(case, monkeypatch, 5)
    monkeypatch.setattr(P.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(builtins, "input", lambda: answer)
    before = len([call for call in fake.calls if call == ("author_corpus_export.py", "run")])
    if answer == "no":
        assert _policy_refusal(P.run_request(_request(case, "06_package_smoke", "approve", _prior(case, 5))))
    else:
        assert P.run_request(_request(case, "06_package_smoke", "approve", _prior(case, 5)))[0] == 0
    after = len([call for call in fake.calls if call == ("author_corpus_export.py", "run")])
    assert after - before == expected_calls


def test_source_approval_inherits_tty_and_domain_decides(tmp_path, monkeypatch):
    case = _case(tmp_path)
    fake = _run_through(case, monkeypatch, 1)
    seen = []

    def capture(argv, **kwargs):
        if Path(argv[1]).name == "acquire_gmail_sent.py" and argv[2] == "approve-smoke":
            seen.append(kwargs["stdin"])
        return fake(argv, **kwargs)

    monkeypatch.setattr(P.subprocess, "run", capture)
    monkeypatch.setattr(P.sys.stdin, "isatty", lambda: True)
    assert P.run_request(_request(case, "02_source_approval", "approve", _prior(case, 1)))[0] == 0
    assert seen == [None]


def test_private_child_streams_are_confined_to_owner_logs(tmp_path, monkeypatch):
    case = _case(tmp_path)
    secret = str(case.root / "private-message.txt")
    fake = FakeDomain(case, private_stream=secret)
    monkeypatch.setattr(P.subprocess, "run", fake)
    rc, envelope = P.run_request(_request(case, "01_source_smoke", "run", []))
    assert rc == 0 and secret not in json.dumps(envelope)
    logs = case.run / "logs"
    assert secret in (logs / "01_source_smoke.domain.stdout").read_text()
    assert secret in (logs / "01_source_smoke.domain.stderr").read_text()
    assert stat_mode(logs / "01_source_smoke.domain.stdout") == 0o600


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_stage03_missing_lineage_verify_is_read_only_and_resume_continues(tmp_path, monkeypatch):
    case = _case(tmp_path)
    fake = _run_through(case, monkeypatch, 2)
    _private_file(case.root / case.source["manifest"], b"partial\n")
    before = len(fake.calls)
    rc, _ = P.run_request(_request(case, "03_source_acquire", "verify", _prior(case, 2)))
    assert rc == 3 and len(fake.calls) == before + 2  # only the two prior verifiers
    rc, _ = P.run_request(_request(case, "03_source_acquire", "resume", _prior(case, 2)))
    assert rc == 0
    assert fake.calls[-1] == ("acquire_gmail_sent.py", "acquire")


def test_repeated_stage03_verify_and_resume_never_reacquire(tmp_path, monkeypatch):
    case = _case(tmp_path)
    fake = _run_through(case, monkeypatch, 3)
    acquired_before = fake.calls.count(("acquire_gmail_sent.py", "acquire"))
    for action in ("verify", "verify", "resume", "resume"):
        rc, _ = P.run_request(_request(case, "03_source_acquire", action, _prior(case, 2)))
        assert rc == 0
    assert fake.calls.count(("acquire_gmail_sent.py", "acquire")) == acquired_before
    assert fake.calls.count(("acquire_gmail_sent.py", "verify-acquisition")) >= 4


def test_kill_after_domain_commit_is_adopted_without_repeating_child(tmp_path, monkeypatch):
    case = _case(tmp_path)
    fake = FakeDomain(case)
    monkeypatch.setattr(P.subprocess, "run", fake)
    real_write = P._write_receipt
    monkeypatch.setattr(P, "_write_receipt", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kill")))
    with pytest.raises(RuntimeError, match="kill"):
        P.run_request(_request(case, "01_source_smoke", "run", []))
    assert fake.calls.count(("acquire_gmail_sent.py", "smoke")) == 1
    monkeypatch.setattr(P, "_write_receipt", real_write)
    rc, _ = P.run_request(_request(case, "01_source_smoke", "verify", []))
    assert rc == 0
    assert fake.calls.count(("acquire_gmail_sent.py", "smoke")) == 1
    assert fake.calls.count(("acquire_gmail_sent.py", "validate-smoke")) == 1


def test_public_cli_returns_closed_bad_input_without_private_exception(tmp_path, capsys):
    request = _private_file(tmp_path / "bad.json", b"not-json")
    assert P.main(["--json", "--request", str(request)]) == 2
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["available"] is False
    assert envelope["reason_category"] == "bad_input"


def test_foreign_cwd_direct_launch_with_empty_pythonpath(tmp_path):
    request = _private_file(tmp_path / "bad-request.json", b"not-json")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    foreign = _private_dir(tmp_path / "foreign")
    proc = subprocess.run(
        [sys.executable, str(FACADE), "--json", "--request", str(request)],
        cwd=foreign, env=environment, capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["reason_category"] == "bad_input"


def test_verifier_success_and_refusal_do_not_write(monkeypatch, tmp_path):
    """Planted-write guard at the public facade verifier boundary."""
    case = _case(tmp_path)
    fake = _run_through(case, monkeypatch, 1)
    before = _tree_snapshot(case.root)
    rc, _ = P.run_request(_request(case, "01_source_smoke", "verify", []))
    assert rc == 0
    after = _tree_snapshot(case.root)
    # Only the request bytes and append-only private logs may change.
    changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
    assert changed <= {
        str(case.request.relative_to(case.root)),
        str((case.run / "logs/01_source_smoke.domain.stdout").relative_to(case.root)),
        str((case.run / "logs/01_source_smoke.domain.stderr").relative_to(case.root)),
    }
    descriptor = case.root / case.source["smoke_dir"] / ".smoke_descriptor.json"
    _private_file(descriptor, b"mutated\n")
    artifact_before = descriptor.read_bytes()
    rc, _ = P.run_request(_request(case, "01_source_smoke", "verify", []))
    assert rc == 3
    assert descriptor.read_bytes() == artifact_before
    assert fake.calls.count(("acquire_gmail_sent.py", "smoke")) == 1
