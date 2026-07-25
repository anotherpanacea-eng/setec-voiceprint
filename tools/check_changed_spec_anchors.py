#!/usr/bin/env python3
"""Run spec-anchor lint over the dependency-safe PR diff.

Git pathnames are read from a NUL-delimited ``--name-status`` stream so valid
whitespace, non-ASCII, and newline-bearing names cannot evade the ratchet.
Deleted and renamed specs receive a separate dependency check: a surviving
spec may not retain the old path, basename, or a now-unallocated ``spec NN``
reference.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


EXCLUDED_LINT_TARGETS = frozenset({"specs/README.md", "specs/_TEMPLATE.md"})
SPEC_NUMBER = re.compile(r"^(\d+)[-_]")


class Refusal(Exception):
    """A controlled, fail-closed discovery or dependency refusal."""


@dataclass(frozen=True)
class Change:
    status: str
    old_path: str | None
    new_path: str | None


def _display(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _decode_path(raw: bytes) -> str:
    if not raw:
        raise Refusal("Git returned an empty path")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise Refusal("Git returned a non-UTF-8 path") from exc


def _parse_name_status(payload: bytes) -> list[Change]:
    if payload and not payload.endswith(b"\0"):
        raise Refusal("Git name-status stream lacks its terminal NUL")
    fields = payload.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    changes: list[Change] = []
    index = 0
    while index < len(fields):
        try:
            status = fields[index].decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise Refusal("Git returned a non-ASCII change status") from exc
        index += 1
        scored = re.fullmatch(r"([RC])([0-9]{3})", status)
        if not (
            re.fullmatch(r"[ADMTUXB]", status)
            or (scored is not None and int(scored.group(2)) <= 100)
        ):
            raise Refusal(f"Unsupported Git change status: {_display(status)}")
        if status[0] in "RC":
            if index + 1 >= len(fields):
                raise Refusal("Truncated Git rename/copy record")
            old_path = _decode_path(fields[index])
            new_path = _decode_path(fields[index + 1])
            index += 2
        else:
            if index >= len(fields):
                raise Refusal("Truncated Git change record")
            path = _decode_path(fields[index])
            index += 1
            old_path = path if status[0] == "D" else None
            new_path = None if status[0] == "D" else path
        changes.append(Change(status=status, old_path=old_path, new_path=new_path))
    return changes


def _discover(repo: Path, base: str) -> list[Change]:
    command = [
        "git",
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        f"{base}...HEAD",
        "--",
        "specs/",
    ]
    completed = subprocess.run(command, cwd=repo, capture_output=True)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise Refusal(
            "Unable to enumerate changed specs: "
            f"{_display(detail) if detail else 'git diff failed'}"
        )
    return _parse_name_status(completed.stdout)


def _is_spec_markdown(path: str | None) -> bool:
    return bool(path and path.startswith("specs/") and path.endswith(".md"))


def _surviving_specs(repo: Path) -> list[Path]:
    surviving: list[Path] = []
    for path in sorted((repo / "specs").rglob("*.md")):
        try:
            info = path.lstat()
        except OSError as exc:
            raise Refusal(f"Unable to inspect surviving spec {_display(str(path))}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise Refusal(
                f"Surviving spec is not a direct regular file: {_display(str(path))}"
            )
        surviving.append(path)
    return surviving


def _remaining_spec_numbers(paths: list[Path]) -> set[int]:
    numbers: set[int] = set()
    for path in paths:
        match = SPEC_NUMBER.match(path.name)
        if match:
            numbers.add(int(match.group(1)))
    return numbers


def _dependency_violations(repo: Path, removed: set[str]) -> list[str]:
    """Return surviving specs that retain anchors to removed identities."""
    if not removed:
        return []
    paths = _surviving_specs(repo)
    current_numbers = _remaining_spec_numbers(paths)
    surviving: list[tuple[str, str]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError) as exc:
            raise Refusal(f"Unable to inspect surviving spec {_display(str(path))}") from exc
        surviving.append((path.relative_to(repo).as_posix(), text))

    violations: set[str] = set()
    for old_path in sorted(removed):
        old_name = Path(old_path).name
        match = SPEC_NUMBER.match(Path(old_path).name)
        old_number = int(match.group(1)) if match else None
        number_pattern = (
            re.compile(rf"\bspec\s+{old_number}\b", re.IGNORECASE)
            if old_number is not None and old_number not in current_numbers
            else None
        )
        for current_path, text in surviving:
            if (
                old_path in text
                or old_name in text
                or (number_pattern is not None and number_pattern.search(text))
            ):
                violations.add(
                    f"{_display(current_path)} retains an anchor to removed "
                    f"{_display(old_path)}"
                )
    return sorted(violations)


def check(
    *,
    repo: Path,
    base: str,
    linter: Path,
) -> int:
    changes = _discover(repo, base)
    targets: set[str] = set()
    removed: set[str] = set()
    for change in changes:
        code = change.status[0]
        if code in "DR" and _is_spec_markdown(change.old_path):
            assert change.old_path is not None
            removed.add(change.old_path)
        if code != "D" and _is_spec_markdown(change.new_path):
            assert change.new_path is not None
            if change.new_path not in EXCLUDED_LINT_TARGETS:
                targets.add(change.new_path)

    violations = _dependency_violations(repo, removed)
    if violations:
        for violation in violations:
            print(f"::error::{violation}", file=sys.stderr)
        return 1

    rc = 0
    for relative in sorted(targets):
        target = repo / relative
        try:
            target_info = target.lstat()
        except OSError:
            target_info = None
        if target_info is None or not stat.S_ISREG(target_info.st_mode):
            print(
                f"::error::Changed spec is not a direct regular file: "
                f"{_display(relative)}",
                file=sys.stderr,
            )
            rc = 1
            continue
        label = _display(relative)
        print(f"::group::spec_anchor_lint {label}")
        completed = subprocess.run(
            [
                sys.executable,
                str(linter),
                "--spec",
                str(target),
                "--repo",
                str(repo),
            ],
            cwd=repo,
        )
        print("::endgroup::")
        if completed.returncode != 0:
            rc = 1

    if not targets and not removed:
        print("No spec changes in this PR - spec-anchor lint skipped.")
    elif not targets:
        print("No live spec files changed; removed-spec dependency checks passed.")
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--base", required=True)
    parser.add_argument("--linter", type=Path)
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    linter = (
        args.linter.resolve()
        if args.linter is not None
        else repo / "tools" / "spec_anchor_lint.py"
    )
    try:
        return check(repo=repo, base=args.base, linter=linter)
    except Refusal as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
