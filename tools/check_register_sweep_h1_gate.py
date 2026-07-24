#!/usr/bin/env python3
"""Validate the immutable H1 register-classifier landing receipt.

Closeout mode validates one pinned GitHub Actions attempt.  Consumer mode is
strictly offline and additionally pins the raw receipt bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import ssl
import stat
import struct
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "setec-h1-landing-receipt/2"
REPOSITORY = "anotherpanacea-eng/setec-voiceprint"
SPEC37_MERGE = "e42b7e056a5309a90dbb120f02ecfff80fe6e59b"
LANDED_COMMIT = "7ffabd343066585de2a80c22b4aeba25d27d5450"
SPEC37_PATH = "specs/37-register-classifier-repair.md"
SPEC76_PATH = "specs/76-register-classifier-refusal-reasons.md"
CLASSIFIER_PATH = "plugins/setec-voiceprint/scripts/register_classifier.py"
WORKFLOW_PATH = ".github/workflows/tests.yml"
SPEC37_SHA256 = "7a2eb4c6c97662415bfbe707529947d93b83635a698404d1c591aafc2da056c1"
SPEC76_SHA256 = "5be5f74d74a8f9243d1cbeef4e24ed49ef1a14c932867ecb80cafcabfc734722"
BASE_CLASSIFIER_SHA256 = (
    "740556a87ab9fc08b0de743198ea67bd40038aa20223553500133c90320b163d"
)
FINAL_CLASSIFIER_SHA256 = (
    "808da9eb369fd3aad725d9e6a799a6151b2f751b0f8f2ca8332dc037fbaaf2d8"
)
MAPPING_SHA256 = "8866d6033ccb0254d7ff474a6daa7bc26fc0e887e294b283e58528dc5e9814ef"
REFUSAL_SHA256 = "f2255796634c1e1f2269029cc25afede25f4c033576b5dfba31f160c975a40c5"
TAXONOMY = "register_families/v2"
WORKFLOW_ALLOWLIST = frozenset(
    {
        "1003c42d078616a3188dc876588289a4f54e2e0ed67049c32eb9df367cb6ecfd",
        "2c8f8e9621039a051d9c23ae093b38a8b8320a14f6017ee8345cdb5f304ccf50",
    }
)
REQUIRED_JOBS = (
    "pytest",
    "macos-descriptor-confinement",
    "windows-descriptor-backend",
    "windows-owner-corrections",
    "windows-shingle-dedup",
    "windows-nonprose-sweep",
    "windows-private-writer-guards",
)
REFUSAL_REASONS = ("short_text", "all_weak", "exact_top_tie")
MAX_RECEIPT = 65_536
MAX_RUN_BODY = 1_048_576
MAX_JOBS_BODY = 4_194_304
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
MEDIA_TYPE = re.compile(r"\s*application/json(?:\s*;.*)?\Z", re.IGNORECASE)
LINK_REL = re.compile(r';\s*rel\s*=\s*(?:"([^"]*)"|([^;,\s]+))', re.I)
BIDI_CONTROLS = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)
TOP_KEYS = frozenset(
    {
        "schema_version",
        "landed_commit",
        "spec_review",
        "implementation_review",
        "refusal_spec_review",
        "refusal_implementation_review",
        "ci",
        "spec_sha256",
        "refusal_spec_path",
        "refusal_spec_sha256",
        "base_classifier_sha256",
        "classifier_sha256",
        "mapping_sha256",
        "refusal_contract_sha256",
        "taxonomy",
    }
)
REVIEW_KEYS = frozenset({"reviewed_head", "verdict"})
CI_KEYS = frozenset(
    {
        "attempt",
        "branch",
        "event",
        "head",
        "required_jobs",
        "result",
        "run_id",
        "workflow_name",
        "workflow_path",
        "workflow_sha256",
    }
)
FORBIDDEN_GIT_EXACT = frozenset(
    {
        "core.alternaterefscommand",
        "core.usereplacerefs",
        "extensions.partialclone",
    }
)
GIT_CONTROLS = {
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_PROTOCOL_FROM_USER": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "LC_ALL": "C",
    "LANG": "C",
}


class Refusal(Exception):
    """Closed controlled refusal."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _framed(domain: bytes, payload: bytes) -> str:
    return _sha256(domain + struct.pack(">Q", len(payload)) + payload)


def _reject_constant(_: str) -> None:
    raise Refusal()


def _decode_json(data: bytes) -> Any:
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise Refusal() from exc

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in pairs:
            if key in out:
                raise Refusal()
            out[key] = value
        return out

    try:
        return json.loads(
            text,
            object_pairs_hook=pairs_hook,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError, TypeError) as exc:
        raise Refusal() from exc


def _valid_string(value: Any, *, max_bytes: int | None = None) -> str:
    if type(value) is not str or unicodedata.normalize("NFC", value) != value:
        raise Refusal()
    if max_bytes is not None and len(value.encode("utf-8")) > max_bytes:
        raise Refusal()
    for char in value:
        code = ord(char)
        if (
            code == 0
            or code < 0x20
            or 0x7F <= code <= 0x9F
            or 0xD800 <= code <= 0xDFFF
            or char in BIDI_CONTROLS
        ):
            raise Refusal()
    return value


def _guard_json_tree(root: Any, *, receipt_strings: bool = False) -> None:
    stack: list[tuple[Any, int]] = [(root, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > 100_000 or depth > 64:
            raise Refusal()
        if type(value) is str:
            if receipt_strings:
                _valid_string(value, max_bytes=131_072)
            else:
                try:
                    if len(value.encode("utf-8", errors="strict")) > 131_072:
                        raise Refusal()
                except UnicodeError as exc:
                    raise Refusal() from exc
        elif type(value) is dict:
            for key, child in value.items():
                if receipt_strings:
                    _valid_string(key, max_bytes=131_072)
                else:
                    try:
                        if len(key.encode("utf-8", errors="strict")) > 131_072:
                            raise Refusal()
                    except UnicodeError as exc:
                        raise Refusal() from exc
                nodes += 1
                if nodes > 100_000:
                    raise Refusal()
                stack.append((child, depth + 1))
        elif type(value) is list:
            stack.extend((child, depth + 1) for child in value)
        elif value is None or type(value) in (bool, int, float):
            if type(value) is float and not math.isfinite(value):
                raise Refusal()
        else:
            raise Refusal()


def _read_receipt(path: Path) -> tuple[dict[str, Any], bytes]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if os.name == "posix" and not nofollow:
        raise Refusal()
    descriptor = -1
    try:
        named = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode) or getattr(named, "st_reparse_tag", 0):
            raise Refusal()
        descriptor = os.open(path, flags | nofollow)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or (int(named.st_dev), int(named.st_ino))
            != (int(info.st_dev), int(info.st_ino))
        ):
            raise Refusal()
        data = os.read(descriptor, MAX_RECEIPT + 1)
        if len(data) > MAX_RECEIPT or os.read(descriptor, 1):
            raise Refusal()
    except (OSError, ValueError, MemoryError) as exc:
        raise Refusal() from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    value = _decode_json(data)
    _guard_json_tree(value, receipt_strings=True)
    if type(value) is not dict or data != _canonical(value) + b"\n":
        raise Refusal()
    return value, data


def _positive_int(value: Any) -> int:
    if type(value) is not int or not (1 <= value <= 2**63 - 1):
        raise Refusal()
    return value


def _hex(value: Any, regex: re.Pattern[str]) -> str:
    if type(value) is not str or regex.fullmatch(value) is None:
        raise Refusal()
    return value


def _validate_receipt(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != TOP_KEYS or value["schema_version"] != SCHEMA_VERSION:
        raise Refusal()
    for key in (
        "landed_commit",
        "spec_sha256",
        "refusal_spec_sha256",
        "base_classifier_sha256",
        "classifier_sha256",
        "mapping_sha256",
        "refusal_contract_sha256",
    ):
        _hex(value[key], HEX40 if key == "landed_commit" else HEX64)
    if value["landed_commit"] != LANDED_COMMIT:
        raise Refusal()
    if value["spec_sha256"] != SPEC37_SHA256:
        raise Refusal()
    if value["refusal_spec_path"] != SPEC76_PATH:
        raise Refusal()
    if value["refusal_spec_sha256"] != SPEC76_SHA256:
        raise Refusal()
    if value["base_classifier_sha256"] != BASE_CLASSIFIER_SHA256:
        raise Refusal()
    if value["classifier_sha256"] != FINAL_CLASSIFIER_SHA256:
        raise Refusal()
    if value["mapping_sha256"] != MAPPING_SHA256:
        raise Refusal()
    if value["refusal_contract_sha256"] != REFUSAL_SHA256:
        raise Refusal()
    if value["taxonomy"] != TAXONOMY:
        raise Refusal()

    for key in (
        "spec_review",
        "implementation_review",
        "refusal_spec_review",
        "refusal_implementation_review",
    ):
        review = value[key]
        if type(review) is not dict or set(review) != REVIEW_KEYS:
            raise Refusal()
        _hex(review["reviewed_head"], HEX40)
        if review["verdict"] != "READY":
            raise Refusal()

    ci = value["ci"]
    if type(ci) is not dict or set(ci) != CI_KEYS:
        raise Refusal()
    _positive_int(ci["run_id"])
    _positive_int(ci["attempt"])
    _hex(ci["head"], HEX40)
    _hex(ci["workflow_sha256"], HEX64)
    if (
        ci["branch"] != "main"
        or ci["event"] != "push"
        or ci["result"] != "PASS"
        or ci["workflow_name"] != "tests"
        or ci["workflow_path"] != WORKFLOW_PATH
        or type(ci["required_jobs"]) is not list
        or tuple(ci["required_jobs"]) != REQUIRED_JOBS
        or ci["workflow_sha256"] not in WORKFLOW_ALLOWLIST
        or ci["head"] != value["landed_commit"]
    ):
        raise Refusal()
    return value


def _git_environment() -> dict[str, str]:
    env = {
        key: val
        for key, val in os.environ.items()
        if key != "GITHUB_TOKEN" and not key.startswith("GIT_")
    }
    env.update(GIT_CONTROLS)
    return env


class Git:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.env = _git_environment()

    def run(
        self,
        args: Iterable[str],
        *,
        allowed_codes: frozenset[int] = frozenset({0}),
        max_bytes: int = 4 * 1024 * 1024,
    ) -> tuple[int, bytes]:
        argv = [
            "git",
            "--no-pager",
            "-c",
            "protocol.allow=never",
            "-C",
            os.fspath(self.root),
            *args,
        ]
        try:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self.env,
                shell=False,
                check=False,
            )
        except (OSError, ValueError, MemoryError) as exc:
            raise Refusal() from exc
        if (
            result.returncode not in allowed_codes
            or len(result.stdout) > max_bytes
            or len(result.stderr) > max_bytes
        ):
            raise Refusal()
        return result.returncode, result.stdout

    def text(self, args: Iterable[str]) -> str:
        _, raw = self.run(args, max_bytes=1_048_576)
        try:
            return raw.decode("ascii", errors="strict")
        except UnicodeError as exc:
            raise Refusal() from exc

    def show_file(self, commit: str, path: str, ceiling: int) -> bytes:
        _, raw = self.run(["show", f"{commit}:{path}"], max_bytes=ceiling + 1)
        if len(raw) > ceiling:
            raise Refusal()
        return raw

    def is_ancestor(self, older: str, newer: str) -> None:
        code, output = self.run(
            ["merge-base", "--is-ancestor", older, newer],
            allowed_codes=frozenset({0, 1}),
            max_bytes=1024,
        )
        if code != 0 or output:
            raise Refusal()


def _config_keys(git: Git, scope: str) -> set[str]:
    code, raw = git.run(
        ["config", scope, "--no-includes", "--null", "--name-only", "--list"],
        max_bytes=1_048_576,
    )
    if code != 0:
        raise Refusal()
    try:
        pieces = raw.decode("utf-8", errors="strict").split("\0")
    except UnicodeError as exc:
        raise Refusal() from exc
    if raw and pieces[-1:] != [""]:
        raise Refusal()
    if pieces[-1:] == [""]:
        pieces.pop()
    keys: set[str] = set()
    for raw_key in pieces:
        key = raw_key.casefold()
        if not key or key in keys:
            raise Refusal()
        keys.add(key)
    return keys


def _forbidden_config(keys: Iterable[str]) -> bool:
    for key in keys:
        if (
            key.startswith("include.")
            or key.startswith("includeif.")
            or key.startswith("fsck.")
            or key in FORBIDDEN_GIT_EXACT
            or (
                key.startswith("remote.")
                and (key.endswith(".promisor") or key.endswith(".partialclonefilter"))
            )
        ):
            return True
    return False


def _resolve_git_path(root: Path, raw: str) -> Path:
    value = raw.rstrip("\n")
    if not value or "\n" in value or "\r" in value:
        raise Refusal()
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    try:
        return path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise Refusal() from exc


def _preflight_git(git: Git) -> None:
    if git.text(["rev-parse", "--show-object-format"]) != "sha1\n":
        raise Refusal()
    if git.text(["rev-parse", "--is-shallow-repository"]) != "false\n":
        raise Refusal()
    git_dir = _resolve_git_path(git.root, git.text(["rev-parse", "--absolute-git-dir"]))
    common = _resolve_git_path(git.root, git.text(["rev-parse", "--git-common-dir"]))
    for base in {git_dir, common}:
        shallow = base / "shallow"
        if shallow.exists():
            raise Refusal()
        alternates = base / "objects" / "info" / "alternates"
        if alternates.exists():
            try:
                if alternates.read_bytes().strip():
                    raise Refusal()
            except OSError as exc:
                raise Refusal() from exc
        grafts = base / "info" / "grafts"
        if grafts.exists():
            try:
                if grafts.read_bytes():
                    raise Refusal()
            except OSError as exc:
                raise Refusal() from exc
        try:
            if any((base / "objects" / "pack").glob("*.promisor")):
                raise Refusal()
        except OSError as exc:
            raise Refusal() from exc

    local_keys = _config_keys(git, "--local")
    if _forbidden_config(local_keys):
        raise Refusal()
    if "extensions.worktreeconfig" in local_keys:
        _, enabled_raw = git.run(
            [
                "config",
                "--local",
                "--no-includes",
                "--type=bool",
                "--get",
                "extensions.worktreeConfig",
            ],
            max_bytes=64,
        )
        if enabled_raw != b"true\n":
            raise Refusal()
        if (git_dir / "config.worktree").exists() and _forbidden_config(
            _config_keys(git, "--worktree")
        ):
            raise Refusal()
    if git.text(["for-each-ref", "--format=%(refname)", "refs/replace"]) != "":
        raise Refusal()
    code, output = git.run(
        ["fsck", "--full", "--strict", "--no-dangling"],
        max_bytes=16 * 1024 * 1024,
    )
    if code or output:
        raise Refusal()


def _load_classifier(data: bytes, expected_sha: str) -> dict[str, Any]:
    if _sha256(data) != expected_sha:
        raise Refusal()
    try:
        source = data.decode("utf-8", errors="strict")
        namespace: dict[str, Any] = {"__name__": "_h1_receipt_classifier"}
        exec(compile(source, CLASSIFIER_PATH, "exec"), namespace, namespace)
    except Exception as exc:
        raise Refusal() from exc
    return namespace


def _mapping_digest(namespace: dict[str, Any]) -> str:
    families = namespace.get("REGISTER_FAMILIES")
    canonical = namespace.get("CANONICAL_REGISTER_TO_FAMILY")
    legacy = namespace.get("LEGACY_REGISTER_TO_FAMILY")
    if (
        namespace.get("REGISTER_TAXONOMY") != TAXONOMY
        or type(families) is not tuple
        or type(canonical) is not dict
        or type(legacy) is not dict
        or namespace.get("KNOWN_REGISTERS") != families
        or any(type(item) is not str for item in families)
        or any(type(key) is not str or type(val) is not str for key, val in canonical.items())
        or any(type(key) is not str or type(val) is not str for key, val in legacy.items())
        or any(val not in families for val in (*canonical.values(), *legacy.values()))
    ):
        raise Refusal()
    payload = _canonical(
        {
            "canonical_register_to_family": canonical,
            "legacy_register_to_family": legacy,
            "register_families": list(families),
            "taxonomy": TAXONOMY,
        }
    )
    if len(payload) != 1_147:
        raise Refusal()
    return _framed(b"setec-register-family-mapping-v2\n", payload)


def _refusal_digest(namespace: dict[str, Any]) -> str:
    reasons = namespace.get("REGISTER_REFUSAL_REASONS")
    if reasons != REFUSAL_REASONS:
        raise Refusal()
    payload = _canonical(
        {
            "field": "refusal_reason",
            "null_when": "scored_family",
            "reasons": list(reasons),
            "taxonomy": TAXONOMY,
        }
    )
    if payload != (
        b'{"field":"refusal_reason","null_when":"scored_family","reasons":'
        b'["short_text","all_weak","exact_top_tie"],"taxonomy":"register_families/v2"}'
    ) or len(payload) != 140:
        raise Refusal()
    return _framed(b"setec-register-classifier-refusal-contract-v1\n", payload)


def _parents(git: Git, commit: str) -> tuple[str, str]:
    raw = git.text(["show", "-s", "--format=%P", commit])
    fields = raw.rstrip("\n").split(" ")
    if len(fields) != 2 or any(HEX40.fullmatch(item) is None for item in fields):
        raise Refusal()
    return fields[0], fields[1]


def _require_commit(git: Git, commit: str) -> None:
    _, output = git.run(["cat-file", "-e", f"{commit}^{{commit}}"], max_bytes=1024)
    if output:
        raise Refusal()


def _verify_artifact(
    git: Git, commit: str, path: str, expected_sha: str, ceiling: int
) -> bytes:
    raw = git.show_file(commit, path, ceiling)
    if _sha256(raw) != expected_sha:
        raise Refusal()
    return raw


def _verify_git(receipt: dict[str, Any], head_arg: str, root: Path) -> None:
    git = Git(root)
    _preflight_git(git)
    resolved_head = git.text(["rev-parse", "--verify", "HEAD^{commit}"]).strip()
    resolved_arg = git.text(["rev-parse", "--verify", f"{head_arg}^{{commit}}"]).strip()
    if (
        HEX40.fullmatch(head_arg) is None
        or resolved_head != head_arg
        or resolved_arg != head_arg
    ):
        raise Refusal()

    landed = receipt["landed_commit"]
    role_heads = {
        name: receipt[name]["reviewed_head"]
        for name in (
            "spec_review",
            "implementation_review",
            "refusal_spec_review",
            "refusal_implementation_review",
        )
    }
    for commit in {head_arg, landed, SPEC37_MERGE, *role_heads.values()}:
        _require_commit(git, commit)

    _, spec37_second = _parents(git, SPEC37_MERGE)
    _, landed_second = _parents(git, landed)
    git.is_ancestor(role_heads["spec_review"], role_heads["implementation_review"])
    git.is_ancestor(
        role_heads["refusal_spec_review"],
        role_heads["refusal_implementation_review"],
    )
    for name in ("spec_review", "implementation_review"):
        git.is_ancestor(role_heads[name], spec37_second)
        git.is_ancestor(role_heads[name], SPEC37_MERGE)
        git.is_ancestor(role_heads[name], landed)
    for name in ("refusal_spec_review", "refusal_implementation_review"):
        git.is_ancestor(role_heads[name], landed_second)
        git.is_ancestor(role_heads[name], landed)
    git.is_ancestor(SPEC37_MERGE, landed)
    git.is_ancestor(landed, head_arg)

    _verify_artifact(
        git, role_heads["spec_review"], SPEC37_PATH, SPEC37_SHA256, MAX_RECEIPT
    )
    base_classifier = _verify_artifact(
        git,
        role_heads["implementation_review"],
        CLASSIFIER_PATH,
        BASE_CLASSIFIER_SHA256,
        1_048_576,
    )
    _verify_artifact(
        git,
        role_heads["implementation_review"],
        SPEC37_PATH,
        SPEC37_SHA256,
        MAX_RECEIPT,
    )
    if _mapping_digest(_load_classifier(base_classifier, BASE_CLASSIFIER_SHA256)) != MAPPING_SHA256:
        raise Refusal()

    _verify_artifact(
        git,
        role_heads["refusal_spec_review"],
        SPEC76_PATH,
        SPEC76_SHA256,
        MAX_RECEIPT,
    )
    refusal_impl = role_heads["refusal_implementation_review"]
    _verify_artifact(git, refusal_impl, SPEC37_PATH, SPEC37_SHA256, MAX_RECEIPT)
    _verify_artifact(git, refusal_impl, SPEC76_PATH, SPEC76_SHA256, MAX_RECEIPT)
    final_classifier = _verify_artifact(
        git, refusal_impl, CLASSIFIER_PATH, FINAL_CLASSIFIER_SHA256, 1_048_576
    )
    final_namespace = _load_classifier(final_classifier, FINAL_CLASSIFIER_SHA256)
    if (
        _mapping_digest(final_namespace) != MAPPING_SHA256
        or _refusal_digest(final_namespace) != REFUSAL_SHA256
    ):
        raise Refusal()

    for commit in (landed, head_arg):
        _verify_artifact(git, commit, SPEC37_PATH, SPEC37_SHA256, MAX_RECEIPT)
        _verify_artifact(git, commit, SPEC76_PATH, SPEC76_SHA256, MAX_RECEIPT)
        raw_classifier = _verify_artifact(
            git, commit, CLASSIFIER_PATH, FINAL_CLASSIFIER_SHA256, 1_048_576
        )
        namespace = _load_classifier(raw_classifier, FINAL_CLASSIFIER_SHA256)
        if (
            _mapping_digest(namespace) != MAPPING_SHA256
            or _refusal_digest(namespace) != REFUSAL_SHA256
        ):
            raise Refusal()

    workflow = _verify_artifact(
        git,
        receipt["ci"]["head"],
        WORKFLOW_PATH,
        receipt["ci"]["workflow_sha256"],
        1_048_576,
    )
    if _sha256(workflow) not in WORKFLOW_ALLOWLIST:
        raise Refusal()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise Refusal()


def _make_opener() -> urllib.request.OpenerDirector:
    for key in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        value = os.environ.get(key)
        if value:
            raise Refusal()
        os.environ.pop(key, None)
    try:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    except (OSError, ValueError, ssl.SSLError) as exc:
        raise Refusal() from exc
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        _NoRedirect(),
    )


def _token() -> str:
    raw = os.environ.get("GITHUB_TOKEN")
    if raw is None:
        raise Refusal()
    try:
        encoded = raw.encode("ascii", errors="strict")
    except UnicodeError as exc:
        raise Refusal() from exc
    if not (1 <= len(encoded) <= 8_192) or any(not (0x21 <= byte <= 0x7E) for byte in encoded):
        raise Refusal()
    return raw


def _response_json(
    opener: urllib.request.OpenerDirector,
    url: str,
    token: str,
    ceiling: int,
) -> tuple[dict[str, Any], Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Accept-Encoding": "identity",
        "User-Agent": "setec-register-sweep-h1-closeout/1",
        "Authorization": f"Bearer {token}",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        response = opener.open(request, timeout=10)
        with response:
            if (
                response.status != 200
                or response.geturl() != url
                or MEDIA_TYPE.fullmatch(response.headers.get("Content-Type", "")) is None
                or response.headers.get("Content-Encoding", "").casefold()
                not in ("", "identity")
            ):
                raise Refusal()
            body = response.read(ceiling + 1)
            if len(body) > ceiling or response.read(1):
                raise Refusal()
            link_headers = tuple(response.headers.get_all("Link", []))
    except Refusal:
        raise
    except (
        OSError,
        TimeoutError,
        ValueError,
        urllib.error.URLError,
        urllib.error.HTTPError,
        ssl.SSLError,
        MemoryError,
    ) as exc:
        raise Refusal() from exc
    value = _decode_json(body)
    _guard_json_tree(value)
    if type(value) is not dict:
        raise Refusal()
    return value, link_headers


def _verify_actions(receipt: dict[str, Any], token: str) -> None:
    ci = receipt["ci"]
    run_id = _positive_int(ci["run_id"])
    attempt = _positive_int(ci["attempt"])
    opener = _make_opener()
    base = (
        f"https://api.github.com/repos/{REPOSITORY}/actions/runs/"
        f"{run_id}/attempts/{attempt}"
    )
    run, _ = _response_json(opener, base, token, MAX_RUN_BODY)
    jobs, links = _response_json(
        opener, base + "/jobs?per_page=100&page=1", token, MAX_JOBS_BODY
    )
    if any(
        "next" in (match.group(1) or match.group(2) or "").casefold().split()
        for value in links
        for match in LINK_REL.finditer(value)
    ):
        raise Refusal()
    repository = run.get("repository")
    if (
        type(repository) is not dict
        or repository.get("full_name") != REPOSITORY
        or type(run.get("id")) is not int
        or run.get("id") != run_id
        or type(run.get("run_attempt")) is not int
        or run.get("run_attempt") != attempt
        or run.get("name") != "tests"
        or run.get("path") != WORKFLOW_PATH
        or run.get("event") != "push"
        or run.get("head_branch") != "main"
        or run.get("head_sha") != ci["head"]
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
    ):
        raise Refusal()
    total = jobs.get("total_count")
    rows = jobs.get("jobs")
    if type(total) is not int or total != 7 or type(rows) is not list or len(rows) != 7:
        raise Refusal()
    found: set[str] = set()
    for job in rows:
        if type(job) is not dict:
            raise Refusal()
        name = job.get("name")
        if type(name) is not str or name in found:
            raise Refusal()
        found.add(name)
        if (
            type(job.get("run_id")) is not int
            or job.get("run_id") != run_id
            or type(job.get("run_attempt")) is not int
            or job.get("run_attempt") != attempt
            or job.get("head_sha") != ci["head"]
            or job.get("workflow_name") != "tests"
            or job.get("status") != "completed"
            or job.get("conclusion") != "success"
        ):
            raise Refusal()
    if found != set(REQUIRED_JOBS):
        raise Refusal()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        add_help=False, allow_abbrev=False, exit_on_error=False
    )
    parser.add_argument("--mode", choices=("closeout", "consumer"), required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--expected-receipt-sha256")
    try:
        args, unknown = parser.parse_known_args(argv)
    except (argparse.ArgumentError, SystemExit, TypeError, ValueError) as exc:
        raise Refusal() from exc
    known_options = (
        "--mode",
        "--receipt",
        "--head",
        "--expected-receipt-sha256",
    )
    counts = {
        option: sum(
            token == option or token.startswith(option + "=") for token in argv
        )
        for option in known_options
    }
    if unknown or any(counts[option] != 1 for option in known_options[:3]):
        raise Refusal()
    if counts["--expected-receipt-sha256"] > 1:
        raise Refusal()
    if args.mode == "consumer":
        _hex(args.expected_receipt_sha256, HEX64)
    elif args.expected_receipt_sha256 is not None:
        raise Refusal()
    _hex(args.head, HEX40)
    return args


def _root() -> Path:
    root = Path(__file__).resolve().parent.parent
    if not (root / "tools" / "check_register_sweep_h1_gate.py").is_file():
        raise Refusal()
    return root


def run(argv: list[str]) -> int:
    try:
        args = _parse_args(argv)
    except Refusal:
        sys.stderr.write("register sweep H1 gate: REFUSED\n")
        return 2
    try:
        receipt, raw = _read_receipt(Path(args.receipt))
        _validate_receipt(receipt)
        if args.mode == "consumer" and _sha256(raw) != args.expected_receipt_sha256:
            raise Refusal()
        token = _token() if args.mode == "closeout" else None
        root = _root()
        _verify_git(receipt, args.head, root)
        if args.mode == "closeout":
            if token is None:
                raise Refusal()
            _verify_actions(receipt, token)
    except (Refusal, OSError, ValueError, TypeError, MemoryError):
        sys.stderr.write("register sweep H1 gate: REFUSED\n")
        return 1
    sys.stdout.write("register sweep H1 gate: PASS\n")
    return 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
