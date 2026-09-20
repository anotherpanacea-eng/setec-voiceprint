#!/usr/bin/env python3
"""Resolve pinned Open Grants Git objects into a Zenodo metadata candidate feed.

Only catalogue Git objects and Zenodo record JSON are read. Reported file
locators are validated and emitted, but this program never requests them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import yaml


TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "build_opengrants_zenodo_source_list"
SCRIPT_VERSION = "1.0"
CATALOGUE = "weecology/ogrants"
SHA_RE = re.compile(r"[0-9a-fA-F]{40}\Z")
YEAR_RE = re.compile(r"[0-9]{4}\Z")
RECORD_PATH_RE = re.compile(r"/records?/([0-9]+)/?\Z")
FILE_PATH_RE = re.compile(r"/api/records/([0-9]+)/files/([^/]+)/content\Z")


class ResolverError(RuntimeError):
    """A source, metadata, or destination contract failure."""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(",", ":")).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_source_strings(value: object) -> None:
    """Reject YAML strings that cannot survive exact UTF-8 provenance output."""
    pending = [value]
    seen = set()
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise ResolverError("frontmatter contains a non-UTF-8 string") from exc
        elif isinstance(item, (dict, list, tuple, set)):
            if id(item) in seen:
                continue
            seen.add(id(item))
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            else:
                pending.extend(item)


def git(repo: Path, *args: str) -> bytes:
    env = os.environ.copy()
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        env.pop(name, None)
    try:
        run = subprocess.run(["git", "-C", str(repo), *args], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResolverError(f"Git operation unavailable: {exc}") from exc
    if run.returncode:
        detail = run.stderr.decode("utf-8", "replace").strip()
        raise ResolverError(f"Git {' '.join(args[:2])} failed: {detail}")
    return run.stdout


def git_text(repo: Path, *args: str) -> str:
    return git(repo, *args).decode("utf-8", "strict").strip()


def worktree_info(path: Path) -> tuple[Path, Path, Path]:
    if git_text(path, "rev-parse", "--is-inside-work-tree") != "true" or \
            git_text(path, "rev-parse", "--is-bare-repository") != "false":
        raise ResolverError("catalogue must be a non-bare Git worktree")
    root = Path(git_text(path, "rev-parse", "--show-toplevel")).resolve()
    git_dir = Path(git_text(path, "rev-parse", "--absolute-git-dir")).resolve()
    common_raw = git_text(path, "rev-parse", "--git-common-dir")
    common = Path(common_raw)
    if not common.is_absolute():
        common = (path / common).resolve()
    else:
        common = common.resolve()
    return root, git_dir, common


def verified_commit(path: Path, commit_arg: str) -> str:
    if not SHA_RE.fullmatch(commit_arg):
        raise ResolverError("--source-commit must be one full 40-hex SHA-1")
    origin = git_text(path, "config", "--get", "remote.origin.url")
    if not re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"weecology/ogrants(?:\.git)?/?", origin, re.IGNORECASE
    ):
        raise ResolverError("remote.origin.url does not claim weecology/ogrants")
    commit = commit_arg.lower()
    if git_text(path, "cat-file", "-t", commit) != "commit":
        raise ResolverError("source SHA is not a commit object")
    if git_text(path, "rev-parse", "--verify", commit) != commit:
        raise ResolverError("source SHA did not resolve exactly")
    return commit


def source_objects(repo: Path, commit: str) -> tuple[list[dict], str]:
    entries = []
    raw = git(repo, "ls-tree", "-r", "-z", commit, "--", "_grants")
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            header, path_raw = item.split(b"\t", 1)
            mode, kind, blob_raw = header.decode("ascii").split(" ")
            path = path_raw.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise ResolverError("invalid Git tree entry") from exc
        if (mode not in {"100644", "100755"} or kind != "blob" or
                not re.fullmatch(r"_grants/[^/]+\.md", path)):
            continue
        blob = blob_raw
        entries.append({"path": path, "blob_sha": blob})
    entries.sort(key=lambda row: row["path"])
    return entries, digest(canonical_bytes(entries))


def frontmatter(blob: bytes) -> dict:
    try:
        text = blob.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ResolverError("frontmatter is not UTF-8") from exc
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ResolverError("missing YAML frontmatter opener")
    end = next((i for i, line in enumerate(lines[1:], 1)
                if line in {"---", "..."}), None)
    if end is None:
        raise ResolverError("missing YAML frontmatter closer")
    try:
        value = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise ResolverError(f"malformed YAML frontmatter: {exc}") from exc
    if not isinstance(value, dict):
        raise ResolverError("frontmatter must be a mapping")
    validate_source_strings(value)
    return value


def claimed_zenodo(link: str) -> bool:
    try:
        url = urllib.parse.urlsplit(link)
        port = url.port
    except ValueError as exc:
        raise ResolverError(f"malformed source URL: {exc}") from exc
    if (url.scheme not in {"http", "https"} or not url.hostname or
            url.username is not None or url.password is not None or
            (port is not None and port < 1)):
        raise ResolverError("source link must be a valid HTTP(S) URL")
    return url.hostname == "zenodo.org"


def record_id(link: str) -> int:
    try:
        url = urllib.parse.urlsplit(link)
        port = url.port
    except ValueError as exc:
        raise ResolverError(f"malformed Zenodo source URL: {exc}") from exc
    match = RECORD_PATH_RE.fullmatch(url.path)
    if (url.scheme != "https" or url.hostname != "zenodo.org" or
            url.username is not None or url.password is not None or
            port is not None or url.query or not match):
        raise ResolverError("Zenodo source URL must be an exact HTTPS record route")
    return int(match.group(1))


def source_link(value: object) -> tuple[str | None, str]:
    if value is None:
        return None, "no_link"
    if isinstance(value, str):
        if not value.strip():
            raise ResolverError("empty source link")
        return value, "selected" if claimed_zenodo(value) else "non_zenodo"
    if isinstance(value, list):
        if (not value or any(not isinstance(v, str) or not v.strip()
                             for v in value)):
            raise ResolverError("source link list has an invalid value")
        if len(value) == 1:
            return source_link(value[0])
        if any(claimed_zenodo(v) for v in value):
            raise ResolverError("multi-link source includes Zenodo; one link required")
        return None, "non_zenodo"
    raise ResolverError("source link must be a string or string list")


def source_identity(data: dict) -> tuple[str, str, int]:
    title, author, year = (data.get(k) for k in ("title", "author", "year"))
    if not isinstance(title, str) or not title.strip():
        raise ResolverError("Zenodo source title must be a nonempty string")
    if not isinstance(author, str) or not author.strip():
        raise ResolverError("Zenodo source author must be a nonempty string")
    if type(year) is int:
        parsed_year = year
    elif isinstance(year, str) and YEAR_RE.fullmatch(year):
        parsed_year = int(year)
    else:
        raise ResolverError("Zenodo source year must be an integer or four ASCII digits")
    return title, author, parsed_year


def valid_locator(locator: str, record: int, key: str) -> bool:
    try:
        url = urllib.parse.urlsplit(locator)
        port = url.port
        match = FILE_PATH_RE.fullmatch(url.path)
        decoded = urllib.parse.unquote(match.group(2), errors="strict") if match else None
    except (ValueError, UnicodeError):
        return False
    return bool(url.scheme == "https" and url.hostname == "zenodo.org" and
                url.username is None and url.password is None and port is None and
                not url.query and not url.fragment and match and
                match.group(1) == str(record) and decoded == key)


def metadata_candidates(raw: bytes, record: int) -> tuple[dict, list[dict]]:
    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite JSON constant {token}")

    try:
        data = json.loads(raw, parse_constant=reject_constant)
        # This also rejects exponent overflow and escaped lone surrogates,
        # including values and keys nested beyond the sidecar fields.
        canonical_bytes(data)
    except (ValueError, UnicodeError) as exc:
        raise ResolverError(f"record {record}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or type(data.get("id")) is not int or data["id"] != record:
        raise ResolverError(f"record {record}: top-level id mismatch")
    files = data.get("files")
    if not isinstance(files, list):
        raise ResolverError(f"record {record}: files must be a list")
    meta = data.get("metadata", {})
    if not isinstance(meta, dict):
        raise ResolverError(f"record {record}: metadata must be an object")
    found = []
    seen_keys = set()
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise ResolverError(f"record {record}: file {index} must be an object")
        key = entry.get("key")
        if not isinstance(key, str) or not key.endswith(".pdf"):
            continue
        size, checksum, links = (entry.get(k) for k in ("size", "checksum", "links"))
        locator = links.get("self") if isinstance(links, dict) else None
        algorithm, separator, value = checksum.partition(":") if isinstance(checksum, str) else ("", "", "")
        if (not key or key in seen_keys or type(size) is not int or size < 0 or
                not separator or not algorithm.strip() or not value.strip() or
                not isinstance(locator, str) or not locator or
                not valid_locator(locator, record, key)):
            raise ResolverError(f"record {record}: invalid PDF file {index} fields or locator")
        seen_keys.add(key)
        found.append({"key": key, "size": size, "checksum": checksum,
                      "locator": locator})
    return data, found


def is_within(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def safe_paths(repo: Path, git_dir: Path, common: Path,
               outputs: list[Path], caches: list[Path], live: bool) -> None:
    protected = [repo, git_dir, common]
    resolved_outputs = [p.resolve() for p in outputs]
    resolved_caches = [p.resolve() for p in caches]
    if len(set(resolved_outputs)) != len(resolved_outputs):
        raise ResolverError("output, sidecar, and error report alias each other")
    for path in resolved_outputs:
        if any(is_within(path, base) for base in protected):
            raise ResolverError(f"output destination is inside catalogue or Git metadata: {path}")
        if path in resolved_caches:
            raise ResolverError(f"output destination aliases a selected metadata cache: {path}")
    if live:
        if len(set(resolved_caches)) != len(resolved_caches):
            raise ResolverError("live metadata cache destinations alias each other")
        for path in resolved_caches:
            if any(is_within(path, base) for base in protected) or path in resolved_outputs:
                raise ResolverError(f"live metadata cache aliases protected path: {path}")


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp",
                                         dir=path.parent, delete=False)
    tmp = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def fetch_metadata(record: int) -> bytes:
    url = f"https://zenodo.org/api/records/{record}"
    request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": "SETEC-voiceprint OpenGrants metadata resolver/1.0"})
    try:
        with urllib.request.build_opener(NoRedirects).open(request, timeout=30) as response:
            if response.status != 200 or response.geturl() != url:
                raise ResolverError(f"record {record}: unexpected metadata HTTP response")
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ResolverError(f"record {record}: metadata request failed: {exc}") from exc


def run(args: argparse.Namespace) -> dict:
    output, sidecar = Path(args.output), Path(args.sidecar)
    error_path = Path(f"{args.sidecar}.error.json")
    repo_arg = Path(args.catalogue_repo).resolve()
    repo, git_dir, common = worktree_info(repo_arg)
    safe_paths(repo, git_dir, common, [output, sidecar, error_path], [], False)
    try:
        commit = verified_commit(repo_arg, args.source_commit)
        if not YEAR_RE.fullmatch(args.year_min) or not YEAR_RE.fullmatch(args.year_max):
            raise ResolverError("year bounds must each be four ASCII digits")
        lower, upper = int(args.year_min), int(args.year_max)
        if lower > upper:
            raise ResolverError("year-min must not exceed year-max")
        entries, inventory_hash = source_objects(repo_arg, commit)
    except ResolverError as exc:
        early_report = {"schema_version": "1.0", "catalogue": CATALOGUE,
                        "source_commit": args.source_commit,
                        "outcomes": [], "failures": [{"stage": "source", "error": str(exc)}]}
        atomic_write(error_path, canonical_bytes(early_report) + b"\n")
        raise
    summary = {name: 0 for name in ("catalogue_rows", "annual_selections",
        "outside_year", "non_zenodo_rows", "no_link_rows", "source_insufficiencies",
        "metadata_failures", "pdf_files", "emitted_candidates")}
    summary["catalogue_rows"] = len(entries)
    outcomes, failures, selected = [], [], []
    for entry in entries:
        path = entry["path"]
        try:
            source = frontmatter(git(repo_arg, "cat-file", "blob", entry["blob_sha"]))
            link, state = source_link(source.get("link"))
            if state == "no_link":
                summary["no_link_rows"] += 1
            elif state == "non_zenodo":
                summary["non_zenodo_rows"] += 1
            else:
                record = record_id(link)
                title, author, year = source_identity(source)
                if lower <= year <= upper:
                    summary["annual_selections"] += 1
                    selected.append({**entry, "link": source["link"], "title": title,
                                     "author": author, "source_year": year, "record_id": record})
                    state = "selected"
                else:
                    summary["outside_year"] += 1
                    state = "outside_year"
            outcomes.append({"path": path, "status": state})
        except ResolverError as exc:
            summary["source_insufficiencies"] += 1
            failures.append({"path": path, "stage": "source", "error": str(exc)})
            outcomes.append({"path": path, "status": "source_insufficiency"})
    records = sorted({row["record_id"] for row in selected})
    caches = [Path(args.metadata_dir) / f"zenodo-record-{n}.json" for n in records]
    safe_paths(repo, git_dir, common, [output, sidecar, error_path], caches,
               args.zenodo_mode == "live")
    resolved = {}
    for record, cache in zip(records, caches):
        try:
            if args.zenodo_mode == "live":
                raw = fetch_metadata(record)
                atomic_write(cache, raw)
            else:
                raw = cache.read_bytes()
            data, files = metadata_candidates(raw, record)
            resolved[record] = (data, files, digest(raw))
            summary["pdf_files"] += len(files)
            outcomes.append({"record_id": record, "status": "metadata_resolved",
                             "pdf_files": len(files)})
        except (ResolverError, OSError) as exc:
            summary["metadata_failures"] += 1
            failures.append({"record_id": record, "stage": "metadata", "error": str(exc)})
            outcomes.append({"record_id": record, "status": "metadata_failure"})
    report = {"schema_version": "1.0", "catalogue": CATALOGUE,
              "source_commit": commit, "source_inventory_sha256": inventory_hash,
              "year_min": lower, "year_max": upper, "summary": summary,
              "outcomes": outcomes, "failures": failures}
    if failures:
        atomic_write(error_path, canonical_bytes(report) + b"\n")
        raise ResolverError(f"{len(failures)} source/metadata insufficiency(s); see {error_path}")
    candidates = []
    for source in selected:
        record = source["record_id"]
        data, files, raw_hash = resolved[record]
        metadata = data.get("metadata", {})
        for file in files:
            identity = ["opengrants-zenodo-candidate/v1", commit, source["path"],
                        source["blob_sha"], record, file["key"]]
            candidate_id = "opengrants-zenodo-" + digest(canonical_bytes(identity))
            feed = {"candidate_id": candidate_id, "url": file["locator"],
                    "title": source["title"], "author": source["author"]}
            side = {"candidate_id": candidate_id, "source_path": source["path"],
                    "source_blob_sha": source["blob_sha"], "source_link": source["link"],
                    "source_title": source["title"], "source_author": source["author"],
                    "source_year": source["source_year"], "record_id": record,
                    "concept_id": data.get("conceptrecid"), "doi": data.get("doi"),
                    "record_title": metadata.get("title"),
                    "record_creators": metadata.get("creators"),
                    "publication_date": metadata.get("publication_date"),
                    "created": data.get("created"), "file_key": file["key"],
                    "reported_locator": file["locator"], "reported_size": file["size"],
                    "reported_checksum": file["checksum"], "raw_metadata_sha256": raw_hash,
                    "metadata": {"access_right_present": "access_right" in metadata,
                                 "access_right": metadata.get("access_right"),
                                 "license_present": "license" in metadata,
                                 "license": metadata.get("license")}}
            candidates.append((candidate_id, feed, side))
    candidates.sort(key=lambda row: row[0])
    summary["emitted_candidates"] = len(candidates)
    if not candidates and not args.allow_empty:
        report["failures"] = [{"stage": "render", "error": "zero candidates; pass --allow-empty"}]
        atomic_write(error_path, canonical_bytes(report) + b"\n")
        raise ResolverError(f"zero candidates; see {error_path}")
    feed_bytes = b"".join(canonical_bytes(feed) + b"\n" for _, feed, _ in candidates)
    sidecar_data = {"schema_version": "1.0", "catalogue": CATALOGUE,
        "source_commit": commit, "source_inventory_sha256": inventory_hash,
        "feed_sha256": digest(feed_bytes), "year_min": lower, "year_max": upper,
        "summary": summary, "candidates": [side for _, _, side in candidates]}
    sidecar_bytes = canonical_bytes(sidecar_data) + b"\n"
    # Stage both complete files before either named destination is replaced.
    def stage(path: Path, content: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=path.parent, delete=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            return Path(handle.name)
    feed_tmp = stage(output, feed_bytes)
    try:
        side_tmp = stage(sidecar, sidecar_bytes)
    except BaseException:
        feed_tmp.unlink(missing_ok=True)
        raise
    try:
        os.replace(feed_tmp, output)
        os.replace(side_tmp, sidecar)
    finally:
        feed_tmp.unlink(missing_ok=True)
        side_tmp.unlink(missing_ok=True)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue-repo", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--metadata-dir", required=True)
    parser.add_argument("--year-min", required=True)
    parser.add_argument("--year-max", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sidecar", required=True)
    parser.add_argument("--zenodo-mode", choices=("cached", "live"), default="cached")
    parser.add_argument("--allow-empty", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = run(args)
    except (ResolverError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    print(f"Inclusive Open Grants source years: {args.year_min}..{args.year_max}. "
          "Feed dates are omitted: acquire_pdf_urls --since/--until do not filter "
          "these undated rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
