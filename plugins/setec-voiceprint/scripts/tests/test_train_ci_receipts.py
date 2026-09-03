from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tools"))
from check_train_ci_receipts import (  # noqa: E402
    EXPECTED_JOBS,
    ReceiptError,
    validate_evidence,
)


BASE = "a" * 40
HEAD = "b" * 40
MERGE = "c" * 40


def _binding(job: str, run_id: int = 200, attempt: int = 1) -> str:
    receipt = {
        "base_sha": BASE,
        "head_sha": HEAD,
        "job": job,
        "run_attempt": str(attempt),
        "run_id": str(run_id),
        "schema": "setec-pr-merge-binding/1",
        "synthetic_merge_sha": MERGE,
    }
    return "some log prefix\npr-merge-binding: " + json.dumps(
        receipt, sort_keys=True, separators=(",", ":"),
    ) + "\n"


def _run(
    run_id: int = 200, *, action: str = "ready_for_review", train: bool = True,
    ci_ready_event: bool = False, conclusion: str = "success",
) -> dict:
    return {
        "id": run_id,
        "attempt": 1,
        "event": "pull_request",
        "path": ".github/workflows/tests.yml",
        "head_sha": HEAD,
        "display_title": (
            f"setec-tests pr=9 action={action} train={str(train).lower()} "
            f"ci-ready-event={str(ci_ready_event).lower()}"
        ),
        "status": "completed",
        "conclusion": conclusion,
        "jobs": [
            {"name": name, "status": "completed", "conclusion": conclusion,
             "log": _binding(name, run_id)}
            for name in sorted(EXPECTED_JOBS)
        ],
    }


def _evidence() -> dict:
    return {
        "schema": "setec-train-ci-evidence/1",
        "repository": "owner/repo",
        "pr": 9,
        "base_ref": "main",
        "base_sha": BASE,
        "head_sha": HEAD,
        "current": {
            "draft": False,
            "head_repo": "owner/repo",
            "head_ref": "train/2026-09-03",
            "labels": [],
            "base_ref": "main",
            "base_sha": BASE,
            "head_sha": HEAD,
        },
        "runs": [_run()],
    }


def test_complete_newest_run_emits_aggregate_receipt():
    receipt = validate_evidence(_evidence())
    assert receipt["run_id"] == 200
    assert receipt["job_count"] == 7
    assert receipt["synthetic_merge_sha"] == MERGE


def test_train_label_noise_is_ignored_without_replacing_clearance():
    evidence = _evidence()
    noise = _run(201, action="labeled", train=True, ci_ready_event=True)
    noise["conclusion"] = "success"
    for job in noise["jobs"]:
        job["conclusion"] = "skipped"
        job["log"] = ""
    evidence["runs"].append(noise)
    assert validate_evidence(evidence)["run_id"] == 200


def test_failed_or_unproven_label_run_is_not_ignorable_noise():
    evidence = _evidence()
    noise = _run(201, action="labeled", train=True, ci_ready_event=True)
    noise["conclusion"] = "failure"
    noise["jobs"] = []
    evidence["runs"].append(noise)
    with pytest.raises(ReceiptError, match="all-skipped success"):
        validate_evidence(evidence)


def test_unrelated_standalone_label_noise_is_ignored():
    evidence = _evidence()
    evidence["current"].update({
        "head_ref": "feature", "labels": ["ci-ready"],
    })
    evidence["runs"] = [_run(train=False)]
    noise = _run(201, action="unlabeled", train=False, ci_ready_event=False)
    for job in noise["jobs"]:
        job["conclusion"] = "skipped"
        job["log"] = ""
    evidence["runs"].append(noise)
    assert validate_evidence(evidence)["run_id"] == 200


@pytest.mark.parametrize(
    "mutation",
    [
        "mixed_run", "stale_attempt", "missing_job", "duplicate_job",
        "later_failure", "duplicate_receipt", "wrong_merge", "wrong_repo",
        "wrong_pr", "wrong_base", "wrong_event", "wrong_path", "wrong_head",
        "bad_activity", "unarmed", "live_base_moved",
    ],
)
def test_receipt_evidence_mutations_fail_closed(mutation: str):
    evidence = _evidence()
    run = evidence["runs"][0]
    if mutation == "mixed_run":
        run["jobs"][0]["log"] = _binding(run["jobs"][0]["name"], 199)
    elif mutation == "stale_attempt":
        run["attempt"] = 2
    elif mutation == "missing_job":
        run["jobs"].pop()
    elif mutation == "duplicate_job":
        run["jobs"].append(copy.deepcopy(run["jobs"][0]))
    elif mutation == "later_failure":
        run["id"] = 199
        for job in run["jobs"]:
            job["log"] = _binding(job["name"], 199)
        evidence["runs"].append(_run(200, conclusion="failure"))
    elif mutation == "duplicate_receipt":
        run["jobs"][0]["log"] *= 2
    elif mutation == "wrong_merge":
        receipt = json.loads(run["jobs"][0]["log"].split("pr-merge-binding: ")[1])
        receipt["synthetic_merge_sha"] = "d" * 40
        run["jobs"][0]["log"] = "pr-merge-binding: " + json.dumps(receipt)
    elif mutation == "wrong_repo":
        evidence["repository"] = "other/repo"
    elif mutation == "wrong_pr":
        evidence["pr"] = 10
    elif mutation == "wrong_base":
        evidence["base_ref"] = "develop"
    elif mutation == "wrong_event":
        run["event"] = "workflow_dispatch"
    elif mutation == "wrong_path":
        run["path"] = ".github/workflows/other.yml"
    elif mutation == "wrong_head":
        run["head_sha"] = "d" * 40
    elif mutation == "bad_activity":
        run["display_title"] += " raw-label=attacker"
    elif mutation == "live_base_moved":
        evidence["current"]["base_sha"] = "d" * 40
    else:
        evidence["current"]["draft"] = True
    with pytest.raises(ReceiptError):
        validate_evidence(evidence)


def test_standalone_ci_ready_removal_is_relevant_not_noise():
    evidence = _evidence()
    evidence["current"].update({"head_ref": "feature", "labels": []})
    evidence["runs"] = [
        _run(action="unlabeled", train=False, ci_ready_event=True),
    ]
    for job in evidence["runs"][0]["jobs"]:
        job["conclusion"] = "skipped"
        job["log"] = ""
    with pytest.raises(ReceiptError, match="currently armed"):
        validate_evidence(evidence)
