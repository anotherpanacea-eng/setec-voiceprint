from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import urllib.error
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[4]
TOOL_PATH = ROOT / "tools" / "check_register_sweep_h1_gate.py"
RECEIPT_PATH = (
    ROOT
    / "plugins"
    / "setec-voiceprint"
    / "references"
    / "register-classifier-h1-receipt.json"
)
HEAD = subprocess.run(
    ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
IS_SHALLOW = (
    subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--is-shallow-repository"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    == "true\n"
)
RECEIPT_SHA256 = "626e32652d476ac88d7d0caf3c78de17dd93c0c81f175405502b83f563922839"


def _load_gate():
    spec = importlib.util.spec_from_file_location("register_sweep_h1_gate", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load_gate()


def _receipt() -> dict[str, Any]:
    return json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))


class _Headers:
    def __init__(
        self,
        *,
        link: str | None = None,
        content_type: str = "application/json; charset=utf-8",
        content_encoding: str = "",
    ) -> None:
        self.link = link
        self.content_type = content_type
        self.content_encoding = content_encoding

    def get(self, key: str, default: str = "") -> str:
        return {
            "Content-Type": self.content_type,
            "Content-Encoding": self.content_encoding,
        }.get(key, default)

    def get_all(self, key: str, default: list[str] | None = None) -> list[str]:
        if key == "Link" and self.link is not None:
            return [self.link]
        return [] if default is None else default


class _Response:
    def __init__(
        self,
        url: str,
        value: Any,
        *,
        link: str | None = None,
        status: int = 200,
        actual_url: str | None = None,
        content_type: str = "application/json; charset=utf-8",
        content_encoding: str = "",
        raw_body: bytes | None = None,
    ) -> None:
        self.status = status
        self._url = url if actual_url is None else actual_url
        self._body = (
            json.dumps(value, separators=(",", ":")).encode("utf-8")
            if raw_body is None
            else raw_body
        )
        self._position = 0
        self.headers = _Headers(
            link=link,
            content_type=content_type,
            content_encoding=content_encoding,
        )

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body) - self._position
        result = self._body[self._position : self._position + size]
        self._position += len(result)
        return result


class _Opener:
    def __init__(self, responses: list[_Response | BaseException]) -> None:
        self.responses = responses
        self.requests: list[tuple[Any, int]] = []

    def open(self, request: Any, *, timeout: int) -> _Response:
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    return result.stdout.strip()


def _new_repo(path: Path, *, commits: int = 2) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "H1 fixture")
    _git(path, "config", "user.email", "h1-fixture@example.invalid")
    for ordinal in range(commits):
        (path / "tracked.txt").write_text(f"{ordinal}\n", encoding="ascii")
        _git(path, "add", "tracked.txt")
        _git(path, "commit", "-q", "-m", f"fixture {ordinal}")
    return path


def _actions_urls(receipt: dict[str, Any]) -> tuple[str, str]:
    ci = receipt["ci"]
    base = (
        "https://api.github.com/repos/anotherpanacea-eng/setec-voiceprint/"
        f"actions/runs/{ci['run_id']}/attempts/{ci['attempt']}"
    )
    return base, base + "/jobs?per_page=100&page=1"


def _show_bytes(root: Path, commit: str, path: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{path}"],
        check=True,
        capture_output=True,
    ).stdout


def _write_fixture(root: Path, relative: str, data: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    _git(root, "add", relative)


def _topology_repo(
    path: Path, *, workflow_bytes: bytes | None = None
) -> tuple[Path, dict[str, Any], dict[str, str]]:
    source_receipt = _receipt()
    source_roles = {
        key: source_receipt[key]["reviewed_head"]
        for key in (
            "spec_review",
            "implementation_review",
            "refusal_spec_review",
            "refusal_implementation_review",
        )
    }
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "H1 topology fixture")
    _git(path, "config", "user.email", "h1-topology@example.invalid")
    if workflow_bytes is None:
        workflow_bytes = _show_bytes(
            ROOT, source_receipt["landed_commit"], GATE.WORKFLOW_PATH
        )
    _write_fixture(path, GATE.WORKFLOW_PATH, workflow_bytes)
    _git(path, "commit", "-q", "-m", "initial")
    initial = _git(path, "rev-parse", "HEAD")

    _git(path, "checkout", "-q", "-b", "spec-review")
    _write_fixture(
        path,
        GATE.SPEC37_PATH,
        _show_bytes(ROOT, source_roles["spec_review"], GATE.SPEC37_PATH),
    )
    _git(path, "commit", "-q", "-m", "spec review")
    spec_review = _git(path, "rev-parse", "HEAD")

    _git(path, "checkout", "-q", "-b", "implementation-review")
    _write_fixture(
        path,
        GATE.CLASSIFIER_PATH,
        _show_bytes(
            ROOT, source_roles["implementation_review"], GATE.CLASSIFIER_PATH
        ),
    )
    _git(path, "commit", "-q", "-m", "implementation review")
    implementation_review = _git(path, "rev-parse", "HEAD")

    _git(path, "checkout", "-q", "main")
    _git(path, "merge", "-q", "--no-ff", "implementation-review", "-m", "land spec 37")
    spec37_merge = _git(path, "rev-parse", "HEAD")

    _git(path, "checkout", "-q", "-b", "refusal-spec-review")
    _write_fixture(
        path,
        GATE.SPEC76_PATH,
        _show_bytes(ROOT, source_roles["refusal_spec_review"], GATE.SPEC76_PATH),
    )
    _git(path, "commit", "-q", "-m", "refusal spec review")
    refusal_spec_review = _git(path, "rev-parse", "HEAD")

    _git(path, "checkout", "-q", "-b", "refusal-implementation-review")
    _write_fixture(
        path,
        GATE.CLASSIFIER_PATH,
        _show_bytes(
            ROOT,
            source_roles["refusal_implementation_review"],
            GATE.CLASSIFIER_PATH,
        ),
    )
    _git(path, "commit", "-q", "-m", "refusal implementation review")
    refusal_implementation_review = _git(path, "rev-parse", "HEAD")

    _git(path, "checkout", "-q", "main")
    _git(
        path,
        "merge",
        "-q",
        "--no-ff",
        "refusal-implementation-review",
        "-m",
        "land refusal contract",
    )
    landed = _git(path, "rev-parse", "HEAD")
    _git(path, "commit", "-q", "--allow-empty", "-m", "consumer head")
    head = _git(path, "rev-parse", "HEAD")

    receipt = copy.deepcopy(source_receipt)
    receipt["landed_commit"] = landed
    receipt["ci"]["head"] = landed
    receipt["ci"]["workflow_sha256"] = hashlib.sha256(workflow_bytes).hexdigest()
    receipt["spec_review"]["reviewed_head"] = spec_review
    receipt["implementation_review"]["reviewed_head"] = implementation_review
    receipt["refusal_spec_review"]["reviewed_head"] = refusal_spec_review
    receipt["refusal_implementation_review"]["reviewed_head"] = (
        refusal_implementation_review
    )
    anchors = {
        "head": head,
        "initial": initial,
        "landed": landed,
        "spec37_merge": spec37_merge,
        "spec_review": spec_review,
        "implementation_review": implementation_review,
        "refusal_spec_review": refusal_spec_review,
        "refusal_implementation_review": refusal_implementation_review,
    }
    return path, receipt, anchors


def _actions_objects() -> tuple[dict[str, Any], dict[str, Any]]:
    receipt = _receipt()
    ci = receipt["ci"]
    run = {
        "id": ci["run_id"],
        "run_attempt": ci["attempt"],
        "name": "tests",
        "path": ".github/workflows/tests.yml",
        "event": "push",
        "head_branch": "main",
        "head_sha": ci["head"],
        "status": "completed",
        "conclusion": "success",
        "repository": {"full_name": "anotherpanacea-eng/setec-voiceprint"},
        "display_title": "A string with an escaped newline\nis legal API data",
    }
    jobs = {
        "total_count": 7,
        "jobs": [
            {
                "name": name,
                "run_id": ci["run_id"],
                "run_attempt": ci["attempt"],
                "head_sha": ci["head"],
                "workflow_name": "tests",
                "status": "completed",
                "conclusion": "success",
            }
            for name in reversed(ci["required_jobs"])
        ],
    }
    return run, jobs


def test_receipt_is_canonical_and_normative_vectors_match() -> None:
    receipt, raw = GATE._read_receipt(RECEIPT_PATH)
    GATE._validate_receipt(receipt)
    assert hashlib.sha256(raw).hexdigest() == RECEIPT_SHA256
    assert raw == GATE._canonical(receipt) + b"\n"

    classifier = (
        ROOT
        / "plugins"
        / "setec-voiceprint"
        / "scripts"
        / "register_classifier.py"
    ).read_bytes()
    namespace = GATE._load_classifier(classifier, GATE.FINAL_CLASSIFIER_SHA256)
    assert GATE._mapping_digest(namespace) == GATE.MAPPING_SHA256
    assert GATE._refusal_digest(namespace) == GATE.REFUSAL_SHA256


def test_actual_offline_git_proof_and_consumer_mode(capsys: pytest.CaptureFixture[str]) -> None:
    result = GATE.run(
        [
            "--mode",
            "consumer",
            "--receipt",
            str(RECEIPT_PATH),
            "--head",
            HEAD,
            "--expected-receipt-sha256",
            RECEIPT_SHA256,
        ]
    )
    captured = capsys.readouterr()
    if IS_SHALLOW:
        assert result == 1
        assert captured.out == ""
        assert captured.err == "register sweep H1 gate: REFUSED\n"
    else:
        assert result == 0
        assert captured.out == "register sweep H1 gate: PASS\n"
        assert captured.err == ""


def test_fail_before_on_receipt_schema_digest_and_noncanonical_bytes(tmp_path: Path) -> None:
    valid = _receipt()
    for mutate in (
        lambda value: value.update(extra=True),
        lambda value: value.__setitem__("classifier_sha256", "0" * 64),
        lambda value: value["spec_review"].__setitem__("verdict", "CLEAR"),
        lambda value: value["ci"].__setitem__("workflow_sha256", "0" * 64),
        lambda value: value["ci"].__setitem__("required_jobs", list(reversed(value["ci"]["required_jobs"]))),
    ):
        candidate = copy.deepcopy(valid)
        mutate(candidate)
        with pytest.raises(GATE.Refusal):
            GATE._validate_receipt(candidate)

    noncanonical = tmp_path / "receipt.json"
    noncanonical.write_text(json.dumps(valid, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(GATE.Refusal):
        GATE._read_receipt(noncanonical)

    duplicate = tmp_path / "duplicate.json"
    duplicate.write_bytes(b'{"schema_version":"x","schema_version":"y"}\n')
    with pytest.raises(GATE.Refusal):
        GATE._read_receipt(duplicate)


def test_git_child_environment_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "do-not-forward")
    for key, value in {
        "GIT_CONFIG_COUNT": "99",
        "GIT_OBJECT_DIRECTORY": "/sentinel/objects",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/sentinel/alternates",
        "GIT_SHALLOW_FILE": "/sentinel/shallow",
        "GIT_NAMESPACE": "sentinel",
        "GIT_REPLACE_REF_BASE": "refs/sentinel/",
        "GIT_DIR": "/sentinel/git-dir",
        "GIT_WORK_TREE": "/sentinel/work-tree",
        "GIT_NO_LAZY_FETCH": "0",
    }.items():
        monkeypatch.setenv(key, value)
    env = GATE._git_environment()
    assert "GITHUB_TOKEN" not in env
    assert not any(key.startswith("GIT_") for key in env if key not in GATE.GIT_CONTROLS)
    assert env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == GATE.os.devnull
    assert env["GIT_PROTOCOL_FROM_USER"] == "0"


def test_worktree_config_exit_one_empty_is_not_an_empty_config(
    tmp_path: Path,
) -> None:
    class ExitOneWorktreeConfig:
        def run(self, args: Any, **_: Any) -> tuple[int, bytes]:
            assert list(args) == [
                "config",
                "--worktree",
                "--no-includes",
                "--null",
                "--name-only",
                "--list",
            ]
            return 1, b""

    with pytest.raises(GATE.Refusal):
        GATE._config_keys(ExitOneWorktreeConfig(), "--worktree")

    repo = _new_repo(tmp_path / "worktree-config")
    _git(repo, "config", "extensions.worktreeConfig", "true")
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    (git_dir / "config.worktree").write_text(
        "[fsck]\n\tskipList = sentinel\n", encoding="ascii"
    )
    with pytest.raises(GATE.Refusal):
        GATE._preflight_git(GATE.Git(repo))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("include.path", "/private/sentinel"),
        ("fsck.skipList", "/private/sentinel"),
        ("fsck.missingEmail", "ignore"),
        ("extensions.partialClone", "origin"),
        ("core.useReplaceRefs", "true"),
        ("core.alternateRefsCommand", "printf sentinel"),
        ("remote.origin.promisor", "true"),
        ("remote.origin.partialCloneFilter", "blob:none"),
    ],
)
def test_forbidden_local_git_config_refuses(
    tmp_path: Path, key: str, value: str
) -> None:
    repo = _new_repo(tmp_path / "hostile-config")
    _git(repo, "config", "--local", key, value)
    with pytest.raises(GATE.Refusal):
        GATE._preflight_git(GATE.Git(repo))


@pytest.mark.parametrize(
    ("kind", "relative_path", "content"),
    [
        ("alternates", "objects/info/alternates", b"/private/sentinel\n"),
        ("grafts", "info/grafts", b"0" * 40 + b"\n"),
        ("promisor", "objects/pack/fixture.promisor", b""),
    ],
)
def test_hostile_object_database_markers_refuse(
    tmp_path: Path, kind: str, relative_path: str, content: bytes
) -> None:
    repo = _new_repo(tmp_path / kind)
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    marker = git_dir / relative_path
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(content)
    with pytest.raises(GATE.Refusal):
        GATE._preflight_git(GATE.Git(repo))


def test_shallow_replace_and_missing_object_repositories_refuse(
    tmp_path: Path,
) -> None:
    origin = _new_repo(tmp_path / "origin", commits=3)
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", f"file://{origin}", str(shallow)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(GATE.Refusal):
        GATE._preflight_git(GATE.Git(shallow))

    replaced = tmp_path / "replaced"
    subprocess.run(
        ["git", "clone", "-q", "--no-local", str(origin), str(replaced)],
        check=True,
        capture_output=True,
    )
    head = _git(replaced, "rev-parse", "HEAD")
    parent = _git(replaced, "rev-parse", "HEAD^")
    _git(replaced, "replace", parent, head)
    with pytest.raises(GATE.Refusal):
        GATE._preflight_git(GATE.Git(replaced))

    missing = _new_repo(tmp_path / "missing")
    tree = _git(missing, "rev-parse", "HEAD^{tree}")
    git_dir = Path(_git(missing, "rev-parse", "--absolute-git-dir"))
    object_path = git_dir / "objects" / tree[:2] / tree[2:]
    assert object_path.is_file()
    object_path.unlink()
    with pytest.raises(GATE.Refusal):
        GATE._preflight_git(GATE.Git(missing))


def test_config_and_fsck_stdout_shapes_refuse() -> None:
    class ScriptedGit:
        root = ROOT

        def __init__(self, output: bytes) -> None:
            self.output = output

        def run(self, *_: Any, **__: Any) -> tuple[int, bytes]:
            return 0, self.output

    with pytest.raises(GATE.Refusal):
        GATE._config_keys(ScriptedGit(b"fsck.skipList"), "--local")
    with pytest.raises(GATE.Refusal):
        GATE._config_keys(ScriptedGit(b"core.pager\0core.pager\0"), "--local")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("include.path", "/private/sentinel"),
        ("fsck.skipList", "/private/sentinel"),
        ("fsck.missingEmail", "ignore"),
        ("extensions.partialClone", "origin"),
        ("core.useReplaceRefs", "true"),
        ("core.alternateRefsCommand", "printf sentinel"),
    ],
)
def test_forbidden_worktree_git_config_refuses(
    tmp_path: Path, key: str, value: str
) -> None:
    repo = _new_repo(tmp_path / "hostile-worktree")
    _git(repo, "config", "extensions.worktreeConfig", "true")
    _git(repo, "config", "--worktree", key, value)
    with pytest.raises(GATE.Refusal):
        GATE._preflight_git(GATE.Git(repo))


def test_role_artifact_lookups_are_exact_and_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if IS_SHALLOW:
        pytest.skip("historical role graph is intentionally unavailable in shallow clones")

    calls: list[tuple[str, ...]] = []
    lookups: list[tuple[str, str]] = []
    original = GATE.Git

    class RecordingGit(original):
        def run(self, args: Any, **kwargs: Any) -> tuple[int, bytes]:
            frozen = tuple(args)
            calls.append(frozen)
            return super().run(frozen, **kwargs)

        def show_file(self, commit: str, path: str, ceiling: int) -> bytes:
            lookups.append((commit, path))
            return super().show_file(commit, path, ceiling)

    monkeypatch.setattr(GATE, "Git", RecordingGit)
    receipt = _receipt()
    GATE._verify_git(receipt, HEAD, ROOT)

    role_sequence = [
        (receipt["spec_review"]["reviewed_head"], GATE.SPEC37_PATH),
        (
            receipt["implementation_review"]["reviewed_head"],
            GATE.CLASSIFIER_PATH,
        ),
        (receipt["implementation_review"]["reviewed_head"], GATE.SPEC37_PATH),
        (receipt["refusal_spec_review"]["reviewed_head"], GATE.SPEC76_PATH),
        (
            receipt["refusal_implementation_review"]["reviewed_head"],
            GATE.SPEC37_PATH,
        ),
        (
            receipt["refusal_implementation_review"]["reviewed_head"],
            GATE.SPEC76_PATH,
        ),
        (
            receipt["refusal_implementation_review"]["reviewed_head"],
            GATE.CLASSIFIER_PATH,
        ),
    ]
    assert lookups[: len(role_sequence)] == role_sequence
    assert all(
        command[0]
        in {
            "cat-file",
            "config",
            "for-each-ref",
            "fsck",
            "merge-base",
            "rev-parse",
            "show",
        }
        for command in calls
    )
    assert not any(
        command[0] in {"fetch", "ls-remote", "remote", "replace"} for command in calls
    )


def test_fresh_clone_without_remote_source_refs_still_verifies(
    tmp_path: Path,
) -> None:
    if IS_SHALLOW:
        pytest.skip("historical role graph is intentionally unavailable in shallow clones")
    clone = tmp_path / "fresh"
    subprocess.run(
        ["git", "clone", "-q", "--no-local", str(ROOT), str(clone)],
        check=True,
        capture_output=True,
    )
    _git(clone, "remote", "remove", "origin")
    _git(clone, "reflog", "expire", "--expire=now", "--all")
    _git(clone, "gc", "--prune=now")
    cloned_head = _git(clone, "rev-parse", "HEAD")
    GATE._verify_git(_receipt(), cloned_head, clone)


def test_synthetic_two_parent_topology_passes_without_source_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, receipt, anchors = _topology_repo(tmp_path / "topology")
    monkeypatch.setattr(GATE, "SPEC37_MERGE", anchors["spec37_merge"])
    monkeypatch.setattr(GATE, "LANDED_COMMIT", anchors["landed"])
    for branch in (
        "spec-review",
        "implementation-review",
        "refusal-spec-review",
        "refusal-implementation-review",
    ):
        _git(repo, "branch", "-D", branch)
    GATE._verify_git(receipt, anchors["head"], repo)


def test_fake_transport_credential_helper_and_pager_remain_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, receipt, anchors = _topology_repo(tmp_path / "offline-recording")
    monkeypatch.setattr(GATE, "SPEC37_MERGE", anchors["spec37_merge"])
    monkeypatch.setattr(GATE, "LANDED_COMMIT", anchors["landed"])
    marker = tmp_path / "unexpected-helper-invocation"
    helper = tmp_path / "hostile-helper"
    helper.write_text(
        f"#!/bin/sh\nprintf invoked > {marker}\nexit 97\n",
        encoding="ascii",
    )
    helper.chmod(0o700)
    _git(repo, "config", "credential.helper", f"!{helper}")
    _git(repo, "config", "core.pager", str(helper))
    _git(repo, "config", "remote.origin.url", f"ext::{helper}")

    GATE._verify_git(receipt, anchors["head"], repo)
    assert not marker.exists()


@pytest.mark.parametrize("shape", ["fast-forward", "squash", "rebase"])
def test_one_parent_substitutes_for_required_merge_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    repo, receipt, anchors = _topology_repo(tmp_path / shape)
    if shape == "fast-forward":
        _git(repo, "checkout", "-q", "-b", "invalid-fast-forward", anchors["initial"])
        _git(repo, "merge", "--ff-only", "implementation-review")
    elif shape == "squash":
        _git(repo, "checkout", "-q", "-b", "invalid-squash", anchors["initial"])
        _git(repo, "merge", "--squash", "implementation-review")
        _git(repo, "commit", "-q", "-m", "squash substitute")
    else:
        _git(repo, "checkout", "-q", "-b", "invalid-rebase", anchors["initial"])
        _git(repo, "cherry-pick", anchors["spec_review"])
        _git(repo, "cherry-pick", anchors["implementation_review"])
    substitute = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")

    monkeypatch.setattr(GATE, "SPEC37_MERGE", substitute)
    monkeypatch.setattr(GATE, "LANDED_COMMIT", anchors["landed"])
    with pytest.raises(GATE.Refusal):
        GATE._verify_git(receipt, anchors["head"], repo)


def test_unrelated_second_parent_reordered_and_dangling_roles_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, receipt, anchors = _topology_repo(tmp_path / "invalid-topology")
    monkeypatch.setattr(GATE, "LANDED_COMMIT", anchors["landed"])

    unrelated = _git(
        repo,
        "commit-tree",
        _git(repo, "rev-parse", f"{anchors['spec_review']}^{{tree}}"),
        "-m",
        "unrelated",
    )
    _git(repo, "update-ref", "refs/heads/unrelated-fixture", unrelated)
    bogus_merge = _git(
        repo,
        "commit-tree",
        _git(repo, "rev-parse", f"{anchors['spec37_merge']}^{{tree}}"),
        "-p",
        anchors["initial"],
        "-p",
        unrelated,
        "-m",
        "bogus merge",
    )
    _git(repo, "update-ref", "refs/heads/bogus-merge-fixture", bogus_merge)
    monkeypatch.setattr(GATE, "SPEC37_MERGE", bogus_merge)
    with pytest.raises(GATE.Refusal):
        GATE._verify_git(receipt, anchors["head"], repo)

    monkeypatch.setattr(GATE, "SPEC37_MERGE", anchors["spec37_merge"])
    reordered = copy.deepcopy(receipt)
    reordered["spec_review"]["reviewed_head"], reordered["implementation_review"][
        "reviewed_head"
    ] = (
        reordered["implementation_review"]["reviewed_head"],
        reordered["spec_review"]["reviewed_head"],
    )
    with pytest.raises(GATE.Refusal):
        GATE._verify_git(reordered, anchors["head"], repo)

    dangling = _git(
        repo,
        "commit-tree",
        _git(repo, "rev-parse", f"{anchors['spec_review']}^{{tree}}"),
        "-p",
        anchors["initial"],
        "-m",
        "locally present but dangling",
    )
    dangling_receipt = copy.deepcopy(receipt)
    dangling_receipt["spec_review"]["reviewed_head"] = dangling
    with pytest.raises(GATE.Refusal):
        GATE._verify_git(dangling_receipt, anchors["head"], repo)


@pytest.mark.parametrize(
    ("commit", "expected"),
    [
        (
            "7ffabd343066585de2a80c22b4aeba25d27d5450",
            "1003c42d078616a3188dc876588289a4f54e2e0ed67049c32eb9df367cb6ecfd",
        ),
        (
            "a3a5c7b44d9eafaf7e9869e5abacde8c9dbcff47",
            "2c8f8e9621039a051d9c23ae093b38a8b8320a14f6017ee8345cdb5f304ccf50",
        ),
    ],
)
def test_each_allowlisted_workflow_drives_full_receipt_and_git_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    commit: str,
    expected: str,
) -> None:
    raw = GATE.Git(ROOT).show_file(commit, GATE.WORKFLOW_PATH, 1_048_576)
    assert hashlib.sha256(raw).hexdigest() == expected
    repo, receipt, anchors = _topology_repo(tmp_path / expected[:8], workflow_bytes=raw)
    monkeypatch.setattr(GATE, "SPEC37_MERGE", anchors["spec37_merge"])
    monkeypatch.setattr(GATE, "LANDED_COMMIT", anchors["landed"])
    GATE._validate_receipt(receipt)
    GATE._verify_git(receipt, anchors["head"], repo)


@pytest.mark.parametrize("mutation", ["unknown", "reordered", "future"])
def test_unknown_reordered_and_future_workflow_bytes_refuse_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    raw = GATE.Git(ROOT).show_file(
        "7ffabd343066585de2a80c22b4aeba25d27d5450",
        GATE.WORKFLOW_PATH,
        1_048_576,
    )
    if mutation == "unknown":
        candidate = raw.replace(b"name: tests", b"name: future-tests", 1)
    elif mutation == "reordered":
        lines = raw.splitlines(keepends=True)
        candidate = b"".join(lines[:1] + list(reversed(lines[1:])))
    else:
        candidate = raw + b"\n# future workflow revision\n"
    assert candidate != raw
    assert hashlib.sha256(candidate).hexdigest() not in GATE.WORKFLOW_ALLOWLIST

    repo, receipt, anchors = _topology_repo(
        tmp_path / mutation, workflow_bytes=candidate
    )
    monkeypatch.setattr(GATE, "SPEC37_MERGE", anchors["spec37_merge"])
    monkeypatch.setattr(GATE, "LANDED_COMMIT", anchors["landed"])
    with pytest.raises(GATE.Refusal):
        GATE._validate_receipt(receipt)
    with pytest.raises(GATE.Refusal):
        GATE._verify_git(receipt, anchors["head"], repo)


def test_exact_two_request_actions_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = _receipt()
    run, jobs = _actions_objects()
    base, jobs_url = _actions_urls(receipt)
    opener = _Opener(
        [
            _Response(base, run),
            _Response(jobs_url, jobs),
        ]
    )
    monkeypatch.setattr(GATE, "_make_opener", lambda: opener)

    GATE._verify_actions(receipt, "synthetic-token")
    assert [request.full_url for request, _ in opener.requests] == [
        base,
        jobs_url,
    ]
    assert [timeout for _, timeout in opener.requests] == [10, 10]
    for request, _ in opener.requests:
        headers = {key.casefold(): value for key, value in request.header_items()}
        assert request.get_method() == "GET"
        assert headers == {
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
            "accept-encoding": "identity",
            "user-agent": "setec-register-sweep-h1-closeout/1",
            "authorization": "Bearer synthetic-token",
        }


@pytest.mark.parametrize(
    "mutation",
    [
        "total_bool",
        "total_contradiction",
        "job_failure",
        "job_incomplete",
        "duplicate_job",
        "missing_job",
        "extra_job",
        "wrong_job_run",
        "wrong_job_attempt",
        "wrong_job_head",
        "wrong_workflow_name",
        "wrong_head",
        "wrong_run_id",
        "wrong_attempt",
        "wrong_name",
        "wrong_path",
        "wrong_repository",
        "pull_request",
        "release",
        "schedule",
        "workflow_dispatch",
        "wrong_branch",
        "run_incomplete",
        "run_failure",
        "next_link",
    ],
)
def test_actions_attempt_fail_closed(
    monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    receipt = _receipt()
    run, jobs = _actions_objects()
    link = None
    if mutation == "total_bool":
        jobs["total_count"] = True
    elif mutation == "total_contradiction":
        jobs["total_count"] = 6
    elif mutation == "job_failure":
        jobs["jobs"][0]["conclusion"] = "failure"
    elif mutation == "job_incomplete":
        jobs["jobs"][0]["status"] = "in_progress"
    elif mutation == "duplicate_job":
        jobs["jobs"][0]["name"] = jobs["jobs"][1]["name"]
    elif mutation == "missing_job":
        jobs["jobs"].pop()
        jobs["total_count"] = 6
    elif mutation == "extra_job":
        jobs["jobs"].append(copy.deepcopy(jobs["jobs"][0]))
        jobs["jobs"][-1]["name"] = "future-job"
        jobs["total_count"] = 8
    elif mutation == "wrong_job_run":
        jobs["jobs"][0]["run_id"] += 1
    elif mutation == "wrong_job_attempt":
        jobs["jobs"][0]["run_attempt"] += 1
    elif mutation == "wrong_job_head":
        jobs["jobs"][0]["head_sha"] = "0" * 40
    elif mutation == "wrong_workflow_name":
        jobs["jobs"][0]["workflow_name"] = "another-successful-workflow"
    elif mutation == "wrong_head":
        run["head_sha"] = "0" * 40
    elif mutation == "wrong_run_id":
        run["id"] += 1
    elif mutation == "wrong_attempt":
        run["run_attempt"] += 1
    elif mutation == "wrong_name":
        run["name"] = "another-successful-workflow"
    elif mutation == "wrong_path":
        run["path"] = ".github/workflows/future.yml"
    elif mutation == "wrong_repository":
        run["repository"]["full_name"] = "another/repository"
    elif mutation in {"pull_request", "release", "schedule", "workflow_dispatch"}:
        run["event"] = mutation
    elif mutation == "wrong_branch":
        run["head_branch"] = "release"
    elif mutation == "run_incomplete":
        run["status"] = "in_progress"
    elif mutation == "run_failure":
        run["conclusion"] = "failure"
    elif mutation == "next_link":
        link = '<https://api.github.com/next>; rel="next"'
    base, jobs_url = _actions_urls(receipt)
    opener = _Opener(
        [
            _Response(base, run),
            _Response(jobs_url, jobs, link=link),
        ]
    )
    monkeypatch.setattr(GATE, "_make_opener", lambda: opener)
    with pytest.raises(GATE.Refusal):
        GATE._verify_actions(receipt, "synthetic-token")
    assert len(opener.requests) == 2


@pytest.mark.parametrize(
    ("kind", "response"),
    [
        (
            "redirect",
            lambda url, value: _Response(
                url, value, actual_url="https://example.invalid/off-host"
            ),
        ),
        ("auth", lambda url, value: _Response(url, value, status=401)),
        (
            "compression",
            lambda url, value: _Response(url, value, content_encoding="gzip"),
        ),
        (
            "content_type",
            lambda url, value: _Response(url, value, content_type="text/plain"),
        ),
        (
            "duplicate_json",
            lambda url, value: _Response(url, value, raw_body=b'{"id":1,"id":1}'),
        ),
        (
            "nonfinite_json",
            lambda url, value: _Response(url, value, raw_body=b'{"id":NaN}'),
        ),
        (
            "oversize",
            lambda url, value: _Response(
                url, value, raw_body=b"{" + b" " * GATE.MAX_RUN_BODY + b"}"
            ),
        ),
    ],
)
def test_actions_transport_hostility_refuses_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    response: Any,
) -> None:
    receipt = _receipt()
    run, _ = _actions_objects()
    base, _ = _actions_urls(receipt)
    opener = _Opener([response(base, run)])
    monkeypatch.setattr(GATE, "_make_opener", lambda: opener)
    with pytest.raises(GATE.Refusal):
        GATE._verify_actions(receipt, "synthetic-token")
    assert len(opener.requests) == 1, kind


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("sentinel timeout"),
        urllib.error.URLError("sentinel transport"),
        OSError("sentinel socket"),
    ],
)
def test_actions_transport_exception_has_no_retry(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    receipt = _receipt()
    opener = _Opener([failure])
    monkeypatch.setattr(GATE, "_make_opener", lambda: opener)
    with pytest.raises(GATE.Refusal):
        GATE._verify_actions(receipt, "synthetic-token")
    assert len(opener.requests) == 1


def test_json_decoder_and_tree_limits_refuse() -> None:
    with pytest.raises(GATE.Refusal):
        GATE._decode_json(b'{"x":1,"x":2}')
    with pytest.raises(GATE.Refusal):
        GATE._decode_json(b'{"x":NaN}')
    with pytest.raises(GATE.Refusal):
        GATE._guard_json_tree({"x": "a" * (131_072 + 1)})
    deep: Any = None
    for _ in range(65):
        deep = [deep]
    with pytest.raises(GATE.Refusal):
        GATE._guard_json_tree(deep)


@pytest.mark.parametrize(
    "token",
    ["", "contains space", "line\nbreak", "\x7f", "é", "x" * 8_193],
)
def test_token_domain_refuses_without_disclosure(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", token)
    with pytest.raises(GATE.Refusal):
        GATE._token()


@pytest.mark.parametrize("key", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_custom_ca_environment_refuses_before_context(
    monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    monkeypatch.setenv(key, "/private/sentinel")
    with pytest.raises(GATE.Refusal):
        GATE._make_opener()


def test_tls_opener_disables_proxies_and_freezes_tls12(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Context:
        check_hostname = False
        verify_mode = None
        minimum_version = None

    context = Context()
    captured: list[object] = []
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:9")
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.invalid:9")
    monkeypatch.setattr(GATE.ssl, "create_default_context", lambda: context)
    monkeypatch.setattr(
        GATE.urllib.request,
        "build_opener",
        lambda *handlers: captured.extend(handlers) or "opener",
    )
    assert GATE._make_opener() == "opener"
    assert context.check_hostname is True
    assert context.verify_mode == GATE.ssl.CERT_REQUIRED
    assert context.minimum_version == GATE.ssl.TLSVersion.TLSv1_2
    proxy = next(item for item in captured if isinstance(item, GATE.urllib.request.ProxyHandler))
    assert proxy.proxies == {}
    assert any(isinstance(item, GATE._NoRedirect) for item in captured)


def test_consumer_never_builds_network_transport(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(GATE, "_verify_git", lambda *_: None)
    monkeypatch.setattr(
        GATE,
        "_make_opener",
        lambda: pytest.fail("consumer attempted to create a network transport"),
    )
    assert GATE.run(
        [
            "--mode",
            "consumer",
            "--receipt",
            str(RECEIPT_PATH),
            "--head",
            HEAD,
            "--expected-receipt-sha256",
            RECEIPT_SHA256,
        ]
    ) == 0
    captured = capsys.readouterr()
    assert captured.out == "register sweep H1 gate: PASS\n"
    assert captured.err == ""


def test_controlled_closeout_failure_has_one_non_disclosing_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "secret-sentinel-token"
    monkeypatch.setenv("GITHUB_TOKEN", secret)

    def refuse(*_: Any) -> None:
        raise GATE.Refusal()

    monkeypatch.setattr(GATE, "_verify_git", refuse)
    assert GATE.run(
        [
            "--mode",
            "closeout",
            "--receipt",
            str(RECEIPT_PATH),
            "--head",
            HEAD,
        ]
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "register sweep H1 gate: REFUSED\n"
    assert secret not in captured.err


def test_cli_misuse_has_fixed_non_disclosing_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert GATE.run(
        [
            "--mode",
            "consumer",
            "--mode",
            "consumer",
            "--receipt",
            "sentinel-private-path",
            "--head",
            HEAD,
            "--expected-receipt-sha256",
            RECEIPT_SHA256,
        ]
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "register sweep H1 gate: REFUSED\n"
    assert "sentinel" not in captured.err

    assert GATE.run(
        [
            "--mode",
            "consumer",
            "--mode=consumer",
            "--receipt=sentinel-private-path",
            f"--head={HEAD}",
            f"--expected-receipt-sha256={RECEIPT_SHA256}",
        ]
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "register sweep H1 gate: REFUSED\n"
