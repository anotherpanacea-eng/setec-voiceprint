from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "tools"))
from check_merge_train import TrainError, load_inventory, verify_train  # noqa: E402


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=check, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, name: str, text: str, message: str | None = None) -> str:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message or f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


def _clean_train(tmp_path: Path) -> tuple[Path, dict, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Train Test")
    _git(repo, "config", "user.email", "train@example.invalid")
    base = _commit(repo, "base.txt", "base\n")
    _git(repo, "switch", "-c", "one", base)
    one = _commit(repo, "one.txt", "one\n", "old candidate [skip ci]")
    _git(repo, "switch", "-c", "two", base)
    two = _commit(repo, "two.txt", "two\n")
    _git(repo, "switch", "main")
    _git(repo, "merge", "--no-ff", "one", "-m", "merge one")
    merge_one = _git(repo, "rev-parse", "HEAD")
    integration = _commit(repo, "integration.txt", "integration\n")
    _git(repo, "merge", "--no-ff", "two", "-m", "merge two")
    merge_two = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    inventory = {
        "schema": "setec-merge-train/1",
        "base": base,
        "base_ref": "refs/remotes/origin/main",
        "head": merge_two,
        "steps": [
            {"kind": "constituent", "pr": 1, "head": one, "merge": merge_one,
             "tree_mode": "clean", "resolution": None},
            {"kind": "train", "label": "integration", "commit": integration},
            {"kind": "constituent", "pr": 2, "head": two, "merge": merge_two,
             "tree_mode": "clean", "resolution": None},
        ],
    }
    return repo, inventory, {
        "base": base, "one": one, "two": two, "merge_one": merge_one,
        "integration": integration, "head": merge_two,
    }


def test_valid_closed_ordered_clean_train(tmp_path: Path):
    repo, inventory, _ = _clean_train(tmp_path)
    receipt = verify_train(repo, inventory)
    assert receipt["constituent_count"] == 2
    assert receipt["train_commit_count"] == 1
    assert receipt["conflict_resolution_count"] == 0


def test_clean_merge_with_arbitrary_tree_edit_is_refused(tmp_path: Path):
    repo, inventory, ids = _clean_train(tmp_path)
    _git(repo, "reset", "--hard", ids["integration"])
    _git(repo, "merge", "--no-ff", "two", "--no-commit")
    (repo / "unreviewed.txt").write_text("hidden\n", encoding="utf-8")
    _git(repo, "add", "unreviewed.txt")
    _git(repo, "commit", "-m", "correct parents, wrong tree")
    wrong = _git(repo, "rev-parse", "HEAD")
    inventory["head"] = wrong
    inventory["steps"][2]["merge"] = wrong
    with pytest.raises(TrainError, match="tree differs"):
        verify_train(repo, inventory)


def test_real_conflict_requires_explicit_resolution_mode(tmp_path: Path):
    repo = tmp_path / "conflict"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Train Test")
    _git(repo, "config", "user.email", "train@example.invalid")
    base = _commit(repo, "shared.txt", "base\n")
    _git(repo, "switch", "-c", "candidate")
    candidate = _commit(repo, "shared.txt", "candidate\n")
    _git(repo, "switch", "main")
    prior = _commit(repo, "shared.txt", "train-side\n")
    _git(repo, "merge", "--no-ff", "candidate", "--no-commit", check=False)
    (repo / "shared.txt").write_text("reviewed resolution\n", encoding="utf-8")
    _git(repo, "add", "shared.txt")
    _git(repo, "commit", "-m", "resolve candidate conflict")
    merge = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    inventory = {
        "schema": "setec-merge-train/1", "base": base,
        "base_ref": "refs/remotes/origin/main", "head": merge,
        "steps": [
            {"kind": "train", "label": "prior integration", "commit": prior},
            {"kind": "constituent", "pr": 8, "head": candidate, "merge": merge,
             "tree_mode": "conflict-resolution",
             "resolution": "resolved shared.txt using combined behavior"},
        ],
    }
    assert verify_train(repo, inventory)["conflict_resolution_count"] == 1
    inventory["steps"][1]["tree_mode"] = "clean"
    inventory["steps"][1]["resolution"] = None
    with pytest.raises(TrainError, match="conflicting"):
        verify_train(repo, inventory)


@pytest.mark.parametrize("mutation", ["reorder", "missing", "moved", "extra", "skip"])
def test_closed_topology_refuses_mutations(tmp_path: Path, mutation: str):
    repo, inventory, ids = _clean_train(tmp_path)
    if mutation == "reorder":
        inventory["steps"][0], inventory["steps"][2] = (
            inventory["steps"][2], inventory["steps"][0],
        )
    elif mutation == "missing":
        del inventory["steps"][0]
    elif mutation == "moved":
        _git(repo, "update-ref", "refs/remotes/origin/main", ids["one"])
    elif mutation == "extra":
        inventory["extra"] = True
    else:
        _git(repo, "commit", "--amend", "-m", "train head [skip ci]")
        inventory["head"] = _git(repo, "rev-parse", "HEAD")
        inventory["steps"][2]["merge"] = inventory["head"]
    with pytest.raises(TrainError):
        verify_train(repo, inventory)


def test_duplicate_json_and_malformed_or_unresolvable_ids_fail(tmp_path: Path):
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        '{"schema":"setec-merge-train/1","schema":"again"}', encoding="utf-8",
    )
    with pytest.raises(TrainError, match="duplicate JSON"):
        load_inventory(duplicate)
    repo, inventory, _ = _clean_train(tmp_path / "train")
    inventory["steps"][0]["head"] = "f" * 40
    with pytest.raises(TrainError):
        verify_train(repo, inventory)


def test_exact_base_lease_rejects_movement_and_accepts_unchanged(tmp_path: Path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(tmp_path, "clone", str(remote), str(work))
    _git(work, "config", "user.name", "Lease Test")
    _git(work, "config", "user.email", "lease@example.invalid")
    base = _commit(work, "base.txt", "base\n")
    _git(work, "push", "origin", "HEAD:main")
    _git(work, "switch", "-c", "candidate")
    candidate = _commit(work, "candidate.txt", "candidate\n")
    _git(work, "switch", "main")
    _git(work, "merge", "--no-ff", "candidate", "-m", "landing")
    landing = _git(work, "rev-parse", "HEAD")
    tested_tree = _git(work, "rev-parse", "HEAD^{tree}")
    assert _git(work, "rev-list", "--parents", "-n", "1", landing).split()[1:] == [base, candidate]
    assert _git(work, "rev-parse", f"{landing}^{{tree}}") == tested_tree

    _git(work, "push", "origin", f"{candidate}:refs/heads/main")
    rejected = subprocess.run(
        ["git", "-C", str(work), "push", "--atomic",
         f"--force-with-lease=refs/heads/main:{base}", "origin",
         f"{landing}:refs/heads/main"], capture_output=True, text=True,
    )
    assert rejected.returncode != 0
    _git(work, "push", "origin", f"{base}:refs/heads/control")
    accepted = subprocess.run(
        ["git", "-C", str(work), "push", "--atomic",
         f"--force-with-lease=refs/heads/control:{base}", "origin",
         f"{landing}:refs/heads/control"], capture_output=True, text=True,
    )
    assert accepted.returncode == 0, accepted.stderr
