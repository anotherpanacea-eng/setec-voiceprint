"""Synthetic, model-free contract tests for reconstructibility_probe_set."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "reconstructibility_probe_set.py"

import originality_audit as oa  # noqa: E402
import reconstructibility_probe_set as probe  # noqa: E402


class _FakeCFunction:
    def __init__(self, implementation):
        self._implementation = implementation
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._implementation(*args)


class _DarwinGrantingAclLib:
    def __init__(self, *, validation_result: int, tag: int) -> None:
        self.validation_calls = 0
        self.entry_calls = 0
        self.validation_result = validation_result
        self.tag = tag
        self.acl_get_fd_np = _FakeCFunction(lambda _fd, _kind: 0x1000)
        self.acl_valid_fd_np = _FakeCFunction(self._validate)
        self.acl_get_entry = _FakeCFunction(self._get_entry)
        self.acl_get_tag_type = _FakeCFunction(self._get_tag_type)
        self.acl_get_flagset_np = _FakeCFunction(lambda _entry, _flags: 0)
        self.acl_get_flag_np = _FakeCFunction(lambda _flags, _flag: 0)
        self.acl_free = _FakeCFunction(lambda _acl: 0)

    def _validate(self, *_args) -> int:
        self.validation_calls += 1
        return self.validation_result

    def _get_entry(self, _acl, which, entry_pointer) -> int:
        self.entry_calls += 1
        if self.entry_calls > 1:
            assert which == probe._DarwinPrivateTree.ACL_NEXT_ENTRY
            probe.ctypes.set_errno(probe.errno.EINVAL)
            return -1
        assert which == probe._DarwinPrivateTree.ACL_FIRST_ENTRY
        entry_pointer._obj.value = 0x2000
        return 0

    def _get_tag_type(self, _entry, tag_pointer) -> int:
        tag_pointer._obj.value = self.tag
        return 0


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("ascii")).hexdigest()


def _row(
    label: str,
    partition: str,
    *,
    source: str | None = None,
    family: str | None = None,
    component: str | None = None,
    masks: list[list[int]] | None = None,
) -> dict[str, object]:
    return {
        "schema": probe.SCHEMA_POPULATION,
        "unit_id": _digest(f"id-{label}"),
        "text_path": f"texts/{label}.txt",
        "content_sha256": _digest(f"content-{label}"),
        "corpus_split": "train",
        "evaluation_partition": partition,
        "source_group": source or _digest(f"source-{label}"),
        "document_family": family or _digest(f"family-{label}"),
        "duplicate_component": component or _digest(f"component-{label}"),
        "loss_mask_intervals": masks or [],
    }


def _plan(
    *,
    prompt_words: int = 2,
    suffix_words: int = 2,
    tail: int = 2,
    probes: int = 1,
    source_cap: int | None = None,
    family_cap: int | None = None,
) -> dict[str, object]:
    return {
        "schema": probe.SCHEMA_PLAN,
        "policy": probe.POLICY,
        "seed": _digest("seed"),
        "min_ngram": oa.DEFAULT_MIN_NGRAM,
        "max_span": oa._MAX_SPAN,
        "population_token_projection_sha256": _digest("tokens"),
        "tail_count_by_partition": {
            "qualification": tail,
            "sealed_confirmation": tail,
        },
        "probe_count_by_partition": {
            "qualification": probes,
            "sealed_confirmation": probes,
        },
        "prompt_words": prompt_words,
        "minimum_suffix_words": suffix_words,
        "max_probes_per_duplicate_component": 1,
        "max_probes_per_source_group": source_cap,
        "max_probes_per_document_family": family_cap,
        "mask_policy": "exclude_prompt_or_continuation_intersection",
        "selection_frozen_before": "2026-07-24T12:00:00Z",
        "purpose": "matched_memorization_safety_evaluation",
    }


def _score(row: dict[str, object], coverage: float, longest: int) -> dict[str, object]:
    return {
        "unit_id": row["unit_id"],
        "coverage": coverage,
        "originality": round(1.0 - coverage, 6),
        "longest_match_tokens": longest,
        "longest_match_capped": longest == oa._MAX_SPAN,
    }


def _anchor() -> dict[str, object]:
    return {
        "anchor_sha256": _digest("anchor"),
        "start_token": 0,
        "prompt_char_start": 0,
        "prompt_char_end": 4,
        "minimum_continuation_char_start": 4,
        "minimum_continuation_char_end": 8,
        "prompt_text": "one ",
        "minimum_continuation_text": "two ",
    }


def test_module_is_model_network_and_trainer_import_free() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert not imported & {
        "torch", "transformers", "tokenizers", "spacy", "numpy",
        "requests", "httpx", "urllib", "socket",
    }
    option_strings = {
        option
        for action in probe._build_parser()._actions
        for option in action.option_strings
    }
    for forbidden in (
        "--model", "--tokenizer", "--checkpoint", "--generate",
        "--upload", "--activate", "--train",
    ):
        assert forbidden not in option_strings


def test_darwin_acl_validation_failure_with_allow_prefers_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lib = _DarwinGrantingAclLib(
        validation_result=-1,
        tag=probe._DarwinPrivateTree.ACL_EXTENDED_ALLOW,
    )
    monkeypatch.setattr(probe.ctypes, "CDLL", lambda *_args, **_kwargs: lib)

    with pytest.raises(probe.ProbeSetError, match="^private_acl_refused$"):
        probe._DarwinPrivateTree._acl_check(7, inside=True)

    assert lib.validation_calls == 1


@pytest.mark.parametrize(
    ("validation_result", "tag", "error"),
    [
        (-1, probe._DarwinPrivateTree.ACL_EXTENDED_DENY,
         "private_acl_inspection_unavailable"),
        (-1, 999, "private_acl_inspection_unavailable"),
        (0, probe._DarwinPrivateTree.ACL_EXTENDED_DENY, None),
    ],
)
def test_darwin_acl_validation_precedence_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    validation_result: int,
    tag: int,
    error: str | None,
) -> None:
    lib = _DarwinGrantingAclLib(validation_result=validation_result, tag=tag)
    monkeypatch.setattr(probe.ctypes, "CDLL", lambda *_args, **_kwargs: lib)

    if error is None:
        probe._DarwinPrivateTree._acl_check(7, inside=True)
    else:
        with pytest.raises(probe.ProbeSetError, match=f"^{error}$"):
            probe._DarwinPrivateTree._acl_check(7, inside=True)

    assert lib.validation_calls == 1


def test_cli_help_states_private_model_free_non_activation() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0
    help_text = completed.stdout.lower()
    assert "private" in help_text
    assert "model-free" in help_text
    assert "trainer" in help_text or "training" in help_text


@pytest.mark.parametrize(
    ("platform_name", "os_name", "code"),
    [
        ("linux", "posix", "linux_acl_backend_unsupported"),
        ("win32", "nt", "windows_publication_unsupported"),
        ("freebsd14", "posix", "platform_unsupported"),
    ],
)
def test_unsupported_platform_refuses_before_private_access_or_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform_name: str,
    os_name: str,
    code: str,
) -> None:
    private = tmp_path / "must-not-be-opened"
    checkpoint = tmp_path / "must-not-be-created-checkpoint"
    output = tmp_path / "must-not-be-created-output"
    monkeypatch.setattr(probe.sys, "platform", platform_name)
    monkeypatch.setattr(probe.os, "name", os_name)
    args = probe._build_parser().parse_args(
        [
            "--population-manifest", "meta/population.jsonl",
            "--population-attestation", "meta/attestation.json",
            "--plan", "meta/plan.json",
            "--private-root", str(private),
            "--checkpoint-dir", checkpoint.name,
            "--output-dir", output.name,
        ]
    )
    with pytest.raises(probe.ProbeSetError, match=f"^{code}$") as raised:
        probe.run(args)
    assert raised.value.exit_code == 4
    assert not private.exists()
    assert not checkpoint.exists()
    assert not output.exists()


@pytest.mark.skipif(sys.platform == "darwin", reason="Darwin is the supported M1 host")
def test_native_unsupported_cli_refuses_without_private_access_or_mutation(
    tmp_path: Path,
) -> None:
    private = tmp_path / "must-not-be-opened"
    checkpoint = tmp_path / "must-not-be-created-checkpoint"
    output = tmp_path / "must-not-be-created-output"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--population-manifest", "meta/population.jsonl",
            "--population-attestation", "meta/attestation.json",
            "--plan", "meta/plan.json",
            "--private-root", str(private),
            "--checkpoint-dir", checkpoint.name,
            "--output-dir", output.name,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    expected = (
        "windows_publication_unsupported"
        if os.name == "nt"
        else "linux_acl_backend_unsupported"
        if sys.platform.startswith("linux")
        else "platform_unsupported"
    )
    assert completed.returncode == 4
    assert completed.stdout == ""
    assert completed.stderr == expected + "\n"
    assert not private.exists()
    assert not checkpoint.exists()
    assert not output.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin contract")
def test_native_darwin_descriptor_exact_alias_and_noreplace(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    exact = "ExactName"
    (root / exact).mkdir(mode=0o700)
    os.chmod(root / exact, 0o700)
    with probe._DarwinPrivateTree(str(root)) as tree:
        assert tree._exact_entry(tree.root_fd, exact)
        with pytest.raises(probe.ProbeSetError, match="^portable_private_path_refused$"):
            tree._exact_entry(tree.root_fd, exact.swapcase())
        stage_fd = tree.mkdir_exclusive(tree.root_fd, "stage")
        os.close(stage_fd)
        tree.rename_exclusive(tree.root_fd, "stage", tree.root_fd, "winner")
        assert tree._exact_entry(tree.root_fd, "winner")
        second_fd = tree.mkdir_exclusive(tree.root_fd, "second")
        os.close(second_fd)
        with pytest.raises(probe.ProbeSetError, match="^publication_collision_refused$"):
            tree.rename_exclusive(tree.root_fd, "second", tree.root_fd, "winner")
        assert tree._exact_entry(tree.root_fd, "second")
        assert tree._exact_entry(tree.root_fd, "winner")


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin ACL contract")
def test_native_darwin_granting_acl_refuses_before_payload_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    target = root / "payload.txt"
    target.write_bytes(b"synthetic-public-fixture")
    os.chmod(target, 0o600)
    added = subprocess.run(
        ["chmod", "+a", "everyone allow read", str(target)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert added.returncode == 0, added.stderr
    try:
        with probe._DarwinPrivateTree(str(root)) as tree:
            with pytest.raises(probe.ProbeSetError, match="^private_acl_refused$"):
                tree.read_file(("payload.txt",), 1)
    finally:
        removed = subprocess.run(
            ["chmod", "-N", str(target)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert removed.returncode == 0, removed.stderr


@pytest.mark.parametrize(
    "value",
    [
        "ok",
        "A-1/b_2/c.3",
        "x" * 128,
        "/".join(["a"] * 64),
        "/".join(["a" * 128] * 31 + ["z" * 97]),
    ],
)
def test_portable_path_boundary_values_pass(value: str) -> None:
    assert "/".join(probe.portable_private_relative_path_v1(value)) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/absolute",
        "trailing/",
        "a//b",
        r"a\b",
        "a/../b",
        ".hidden",
        "trailing.",
        "has space",
        "drive:c",
        "é",
        "\U0001f600",
        "CON",
        "com1.txt",
        "LpT9.log",
        "x" * 129,
        "/".join(["a"] * 65),
        "/".join(["a" * 128] * 31 + ["z" * 98]),
    ],
)
def test_portable_path_forbidden_and_one_over_values_refuse(value: str) -> None:
    with pytest.raises(probe.ProbeSetError, match="^portable_private_path_refused$"):
        probe.portable_private_relative_path_v1(value)


def test_portable_collision_key_is_ascii_case_only() -> None:
    exact = probe.portable_private_relative_path_v1("Text/A-1.txt")
    alias = probe.portable_private_relative_path_v1("text/a-1.TXT")
    assert exact != alias
    assert probe.portable_collision_key(exact) == probe.portable_collision_key(alias)


class _Entry:
    def __init__(self, name: str):
        self.name = name


class _StreamingEntries:
    def __init__(self, names: list[str]):
        self._entries = iter(_Entry(name) for name in names)
        self.next_calls = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def __iter__(self):
        return self

    def __next__(self):
        self.next_calls += 1
        return next(self._entries)


def _bare_private_tree():
    tree = object.__new__(probe._DarwinPrivateTree)
    tree._entry_count = 0
    tree._name_bytes = 0
    return tree


def test_descriptor_enumeration_exhausts_stream_at_exact_equality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _StreamingEntries(["target", "other"])
    monkeypatch.setattr(probe.os, "scandir", lambda _fd: entries)
    monkeypatch.setattr(probe, "MAX_UNITS", 2)
    assert _bare_private_tree()._exact_entry(17, "target") is True
    # Two members plus the iterator's StopIteration probe: finding the target
    # early does not waive accounting for the rest of the parent.
    assert entries.next_calls == 3
    assert entries.closed


def test_descriptor_enumeration_one_over_closes_without_next_entry_or_child_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = _StreamingEntries(["target", "one-over", "must-not-be-requested"])
    monkeypatch.setattr(probe.os, "scandir", lambda _fd: entries)
    monkeypatch.setattr(probe, "MAX_UNITS", 1)
    with pytest.raises(
        probe.ProbeSetError,
        match="^private_directory_enumeration_limit_refused$",
    ):
        _bare_private_tree()._exact_entry(17, "target")
    assert entries.next_calls == 2
    assert entries.closed


def test_descriptor_enumeration_refuses_case_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(probe, "_DarwinPrivateTree"):
        pytest.fail("reviewed Darwin descriptor seam is missing")
    tree = _bare_private_tree()
    entries = _StreamingEntries(["Target"])
    monkeypatch.setattr(probe.os, "scandir", lambda _fd: entries)
    with pytest.raises(probe.ProbeSetError, match="^portable_private_path_refused$"):
        tree._exact_entry(17, "target")


def _synthetic_output_artifacts() -> dict[str, bytes]:
    return {
        "qualification/probes.jsonl": b'{"partition":"q","type":"probe"}\n',
        "qualification/probe_index.jsonl": b'{"partition":"q","type":"index"}\n',
        "sealed_confirmation/probes.jsonl": b'{"partition":"s","type":"probe"}\n',
        "sealed_confirmation/probe_index.jsonl": b'{"partition":"s","type":"index"}\n',
        "probe_receipt.json": b'{"receipt":"synthetic"}\n',
    }


def test_complete_output_replay_fake_has_no_ambient_or_stray_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _synthetic_output_artifacts()
    entries = {
        10: [
            "qualification", "sealed_confirmation",
            "probe_receipt.json", ".setec-committed-v1",
        ],
        11: ["probes.jsonl", "probe_index.jsonl"],
        12: ["probes.jsonl", "probe_index.jsonl"],
    }
    monkeypatch.setattr(
        probe.os, "scandir",
        lambda fd: _StreamingEntries(entries[fd]),
    )
    monkeypatch.setattr(probe.os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(probe.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(
        probe.os, "open",
        lambda name, *_args, dir_fd=None, **_kwargs:
            11 if name == "qualification" else 12,
    )
    monkeypatch.setattr(probe.os, "close", lambda _fd: None)
    monkeypatch.setattr(probe.os, "fsync", lambda _fd: None)
    tree = _bare_private_tree()
    tree._exact_entry = lambda *_args, **_kwargs: True
    tree._assert_named_identity = lambda *_args, **_kwargs: None
    tree._validate_directory = lambda *_args, **_kwargs: None

    def read_named(parent_fd, name, _cap):
        if parent_fd == 10:
            raw = artifacts["probe_receipt.json"] if name == "probe_receipt.json" else b""
        else:
            partition = "qualification" if parent_fd == 11 else "sealed_confirmation"
            raw = artifacts[f"{partition}/{name}"]
        return raw, None

    tree.read_named_file = read_named
    tree.replay_output_tree(10, artifacts)


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin contract")
def test_native_darwin_complete_output_tree_replay(tmp_path: Path) -> None:
    artifacts = _synthetic_output_artifacts()
    root = tmp_path / "private"
    stage = root / "stage"
    root.mkdir(mode=0o700)
    stage.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    os.chmod(stage, 0o700)
    for partition in probe.PARTITIONS:
        directory = stage / partition
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
        for basename in ("probes.jsonl", "probe_index.jsonl"):
            path = directory / basename
            path.write_bytes(artifacts[f"{partition}/{basename}"])
            os.chmod(path, 0o600)
    receipt = stage / "probe_receipt.json"
    receipt.write_bytes(artifacts["probe_receipt.json"])
    os.chmod(receipt, 0o600)
    marker = stage / ".setec-committed-v1"
    marker.write_bytes(b"")
    os.chmod(marker, 0o600)
    with probe._DarwinPrivateTree(str(root)) as tree:
        stage_fd, _ = tree.open_dir(("stage",))
        try:
            tree.replay_output_tree(stage_fd, artifacts)
        finally:
            os.close(stage_fd)


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin contract")
def test_native_darwin_checkpoint_intent_fault_resumes_exact_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    raw = b'{"synthetic":"checkpoint-member"}\n'
    with probe._DarwinPrivateTree(str(root)) as tree:
        checkpoint_fd = tree.mkdir_exclusive(tree.root_fd, "checkpoint")
        stage_fd = tree.mkdir_exclusive(tree.root_fd, "checkpoint-stage")
        try:
            def crash(label: str) -> None:
                if label == "checkpoint_intent_parent_flushed":
                    raise RuntimeError("synthetic crash")

            monkeypatch.setattr(probe, "_FAULT_HOOK", crash)
            with pytest.raises(RuntimeError, match="synthetic crash"):
                tree.publish_checkpoint_member(
                    stage_fd, checkpoint_fd, "binding.json",
                    raw, probe.MAX_BINDING_BYTES,
                )
            monkeypatch.setattr(probe, "_FAULT_HOOK", None)
            assert tree.recover_checkpoint_member(
                stage_fd, checkpoint_fd, "binding.json",
                raw, probe.MAX_BINDING_BYTES,
            )
            stored, _ = tree.read_named_file(
                checkpoint_fd, "binding.json", probe.MAX_BINDING_BYTES
            )
            assert stored == raw
            tree.require_empty_directory(stage_fd)
        finally:
            os.close(stage_fd)
            os.close(checkpoint_fd)


@pytest.mark.skipif(sys.platform != "darwin", reason="native Darwin contract")
def test_native_darwin_output_postrename_fault_recovers_target_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = _synthetic_output_artifacts()
    receipt_sha = _digest("synthetic-receipt")
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    with probe._DarwinPrivateTree(str(root)) as tree:
        def crash(label: str) -> None:
            if label == "output_renamed":
                raise RuntimeError("synthetic crash")

        monkeypatch.setattr(probe, "_FAULT_HOOK", crash)
        with pytest.raises(RuntimeError, match="synthetic crash"):
            tree.recover_or_publish_output(
                tree.root_fd, "output", ".output.stage", ".output.intent",
                artifacts, receipt_sha, resume=False,
            )
        monkeypatch.setattr(probe, "_FAULT_HOOK", None)
        tree.recover_or_publish_output(
            tree.root_fd, "output", ".output.stage", ".output.intent",
            artifacts, receipt_sha, resume=True,
        )
        assert tree.name_present(tree.root_fd, "output")
        assert not tree.name_present(tree.root_fd, ".output.intent")


def test_producer_identity_drift_refuses_before_publication_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(probe, "_git_identity", lambda: ("b" * 40, _digest("changed")))
    with pytest.raises(probe.ProbeSetError, match="^producer_identity_refused$"):
        probe._publish_after_identity_gate(
            ("a" * 40, _digest("expected")),
            lambda: called.append("published"),
        )
    assert called == []


@pytest.mark.parametrize(
    "value",
    [
        {"text": "literal é / quote\" slash\\ control\n"},
        {"supplementary": "\U0001f642"},
        {"ints": [-1, 0, 1], "floats": [0.0, 1.5]},
    ],
)
def test_canonical_json_exact_roundtrip(value: dict[str, object]) -> None:
    encoded = probe.canonical_json_line_v1(value)
    assert encoded.endswith(b"\n") and not encoded.endswith(b"\n\n")
    assert probe.strict_json_line_v1(encoded) == value
    assert b"\\/" not in encoded


def test_public_synthetic_canonical_frame_and_unicode_offset_goldens() -> None:
    value = {"a": [1, "x"], "b": False}
    assert probe.semantic_sha256(
        b"example-domain-v1\n", value
    ) == "sha256:8cda7e48e32aad447eee2623b810154b3d4f208450a98f7e2f60dfb73ce7824a"
    assert probe.plain_sha256(
        probe.canonical_json_line_v1(value)
    ) == "sha256:0d0d8b48648e4cc3384d1a000d0ce66f920248f048a8ff5850a6f00260b0cfa4"
    lowered, coordinates = probe.lower_to_source_matches("A\u0130B alpha\r\nbeta")
    assert lowered == "ai\u0307b alpha\r\nbeta"
    assert [
        (
            item.value,
            item.lowered_start,
            item.lowered_end,
            item.source_start,
            item.source_end,
        )
        for item in coordinates
    ] == [
        ("ai", 0, 2, 0, 2),
        ("b", 3, 4, 2, 3),
        ("alpha", 5, 10, 4, 9),
        ("beta", 12, 16, 11, 15),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"a":1,"a":1}\n',
        b'{"a":NaN}\n',
        b'{"a":-0.0}\n',
        b'{"a":1}',
        b'{ "a":1}\n',
        b'\xef\xbb\xbf{"a":1}\n',
        b'{"a":"\\ud800"}\n',
        b'{"a":"\\udfff"}\n',
    ],
)
def test_strict_json_refuses_duplicate_noncanonical_and_invalid_scalar(
    raw: bytes,
) -> None:
    with pytest.raises(probe.ProbeSetError):
        probe.strict_json_line_v1(raw)


def test_scalar_tree_depth_equality_and_one_over() -> None:
    at_limit: object = "leaf"
    for _ in range(16):
        at_limit = [at_limit]
    probe._walk_scalar_tree(at_limit)
    one_over: object = [at_limit]
    with pytest.raises(probe.ProbeSetError, match="^json_tree_limit_refused$"):
        probe._walk_scalar_tree(one_over)


def test_population_rejects_cross_partition_grouping_and_unknown_split() -> None:
    same_source = _digest("same-source")
    rows = [
        _row("a", "qualification", source=same_source),
        _row("b", "sealed_confirmation", source=same_source),
    ]
    with pytest.raises(probe.ProbeSetError, match="^cross_partition_grouping_refused$"):
        probe.validate_population(rows)
    rows[1]["source_group"] = "other"
    rows[1]["corpus_split"] = "validation"
    with pytest.raises(probe.ProbeSetError, match="^population_schema_refused$"):
        probe.validate_population(rows)


def test_membership_grouping_and_token_projections_are_order_stable() -> None:
    rows = [_row("b", "qualification"), _row("a", "qualification")]
    texts = {
        rows[0]["unit_id"]: "One TWO",
        rows[1]["unit_id"]: "Three four",
    }
    assert probe.membership_projection(rows) == probe.membership_projection(rows[::-1])
    assert probe.grouping_projection(rows) == probe.grouping_projection(rows[::-1])
    assert probe.population_token_projection(rows, texts) == probe.population_token_projection(
        rows[::-1], texts
    )
    changed = dict(texts)
    changed[rows[0]["unit_id"]] = "One changed"
    assert probe.population_token_projection(rows, texts) != probe.population_token_projection(
        rows, changed
    )


@pytest.mark.parametrize(
    "text",
    [
        "İSTANBUL alpha beta gamma delta",
        "Straße alpha beta gamma delta",
        "ΟΣ alpha beta gamma delta",
        "A\u0301 alpha\r\nbeta\rgamma\tdelta",
    ],
)
def test_unicode_lowering_offsets_replay_exact_source_slices(text: str) -> None:
    lowered, matches = probe.lower_to_source_matches(text)
    assert lowered == text.lower()
    assert [item.value for item in matches] == list(oa._tokens(text))
    assert all(
        text[item.source_start:item.source_end].lower().find(item.value) >= 0
        for item in matches
    )


def test_anchor_offsets_masks_and_rejoin_exact_source_slice() -> None:
    text = "One,\r\ntwo THREE! four five."
    row = _row("anchor", "qualification")
    plan = _plan(prompt_words=2, suffix_words=2)
    anchors = probe.valid_anchors(row, text, plan, plan_sha256=_digest("plan"))
    assert anchors
    for anchor in anchors:
        joined = anchor["prompt_text"] + anchor["minimum_continuation_text"]
        assert joined == text[
            anchor["prompt_char_start"]:anchor["minimum_continuation_char_end"]
        ]
    masked = dict(row)
    masked["loss_mask_intervals"] = [[0, len(text)]]
    assert probe.valid_anchors(masked, text, plan, plan_sha256=_digest("plan")) == []


def test_score_population_matches_direct_leave_one_out() -> None:
    shared = "one two three four five six seven eight nine ten"
    rows = [
        _row("q1", "qualification"),
        _row("q2", "qualification"),
        _row("s1", "sealed_confirmation"),
        _row("s2", "sealed_confirmation"),
    ]
    texts = {
        rows[0]["unit_id"]: shared + " alpha",
        rows[1]["unit_id"]: shared + " beta",
        rows[2]["unit_id"]: shared + " gamma",
        rows[3]["unit_id"]: "wholly distinct vocabulary remains outside all shared sequences now",
    }
    shards = probe.score_population(rows, texts, _digest("binding"))
    by_id = {row["unit_id"]: row for row in shards}
    target = rows[2]["unit_id"]
    direct = oa.audit_originality(
        texts[target],
        [(unit, text) for unit, text in texts.items() if unit != target],
        min_ngram=oa.DEFAULT_MIN_NGRAM,
        max_span=oa._MAX_SPAN,
    )
    assert by_id[target]["coverage"] == direct["coverage"]
    assert by_id[target]["originality"] == direct["originality"]
    assert by_id[target]["longest_match_tokens"] == direct["longest_match_tokens"]
    assert "top_source" not in by_id[target]


def test_selection_first_failure_precedence_skips_anchor_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows: list[dict[str, object]] = []
    texts: dict[str, str] = {}
    shards: list[dict[str, object]] = []
    for partition in probe.PARTITIONS:
        source = _digest(f"{partition}-source")
        family = _digest(f"{partition}-family")
        accepted = _row(f"{partition}-accepted", partition, source=source, family=family)
        rejected = _row(
            f"{partition}-rejected",
            partition,
            source=source,
            family=family,
            component=accepted["duplicate_component"],
        )
        final = _row(f"{partition}-final", partition)
        rows.extend([accepted, rejected, final])
        for row in (accepted, rejected, final):
            texts[row["unit_id"]] = "one two three four"
        shards.extend(
            [
                _score(accepted, 1.0, 16),
                _score(rejected, 0.9, 15),
                _score(final, 0.8, 14),
            ]
        )
    called: list[str] = []

    def anchors(row, *_args, **_kwargs):
        called.append(row["unit_id"])
        return [_anchor()]

    monkeypatch.setattr(probe, "valid_anchors", anchors)
    selected, rejected = probe.select_probes(
        rows,
        texts,
        shards,
        _plan(tail=3, probes=2, source_cap=1, family_cap=1),
        _digest("plan"),
    )
    assert all(len(selected[p]) == 2 for p in probe.PARTITIONS)
    assert rejected["rejected_duplicate_component_cap"] == 2
    assert len(called) == 4


def test_changed_seed_changes_anchor_choice_not_scores() -> None:
    row = _row("seed", "qualification")
    text = "one two three four five six seven eight"
    first = _plan(prompt_words=2, suffix_words=2)
    second = dict(first)
    second["seed"] = _digest("different-seed")
    anchors_a = probe.valid_anchors(row, text, first, plan_sha256=_digest("plan-a"))
    anchors_b = probe.valid_anchors(row, text, second, plan_sha256=_digest("plan-b"))
    assert [a["start_token"] for a in anchors_a] == [a["start_token"] for a in anchors_b]
    assert [a["anchor_sha256"] for a in anchors_a] != [a["anchor_sha256"] for a in anchors_b]


def test_preflight_resource_equality_passes_and_one_over_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        _row("q1", "qualification"),
        _row("q2", "qualification"),
        _row("q3", "qualification"),
        _row("s1", "sealed_confirmation"),
        _row("s2", "sealed_confirmation"),
        _row("s3", "sealed_confirmation"),
    ]
    texts = {row["unit_id"]: "one two three four" for row in rows}
    sizes = {row["unit_id"]: len(texts[row["unit_id"]].encode()) for row in rows}
    plan = _plan(tail=3, probes=1)
    total = sum(sizes.values())
    monkeypatch.setattr(probe, "MAX_TOTAL_DOCUMENT_BYTES", total)
    probe.preflight_resources(rows, texts, sizes, plan)
    monkeypatch.setattr(probe, "MAX_TOTAL_DOCUMENT_BYTES", total - 1)
    with pytest.raises(probe.ProbeSetError, match="^population_resource_limit_refused$"):
        probe.preflight_resources(rows, texts, sizes, plan)


def test_claim_license_is_closed_and_non_authorizing() -> None:
    combined = " ".join(probe.CLAIM_LICENSE.values()).lower()
    for refusal in (
        "memorized", "unsafe", "ai/human", "authorship", "plagiarism",
        "checkpoint", "activation", "training", "deployment", "promotion",
    ):
        assert refusal in combined
    assert set(probe.CLAIM_LICENSE) == {"licenses", "does_not_license"}
