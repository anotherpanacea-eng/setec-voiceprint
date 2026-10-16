"""Behavioral checks for the pinned Open Grants metadata resolver."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
from setec.acquisition_sources import build_opengrants_zenodo_source_list as resolver
FIXTURES = SCRIPTS / "test_data" / "opengrants_zenodo_source_list_fixture"


def git(repo, *args, env=None):
    return subprocess.check_output(["git", "-C", str(repo), *args], env=env).decode().strip()


def make_repo(tmp_path, rows=None):
    repo = tmp_path / "ogrants"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.test")
    git(repo, "remote", "add", "origin", "git@github.com:weecology/ogrants.git")
    for name, content in (rows or {"one.md": yaml_source()}).items():
        path = repo / "_grants" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    return repo, git(repo, "rev-parse", "HEAD")


def yaml_source(title="Source title", author="Source author", year=2013,
                link="https://zenodo.org/records/830239"):
    if isinstance(link, list):
        link_line = "link:\n" + "".join(f"  - {part}\n" for part in link)
    elif link is None:
        link_line = ""
    else:
        link_line = f"link: {link}\n"
    return f"---\ntitle: {title}\nauthor: {author}\nyear: {year}\n{link_line}---\nBody\n"


def prepare(tmp_path, rows=None):
    repo, commit = make_repo(tmp_path, rows)
    cache = tmp_path / "cache"
    cache.mkdir()
    shutil.copyfile(FIXTURES / "zenodo-record-830239.json",
                    cache / "zenodo-record-830239.json")
    return argparse.Namespace(catalogue_repo=str(repo), source_commit=commit,
        metadata_dir=str(cache), year_min="2013", year_max="2021",
        output=str(tmp_path / "feed.jsonl"), sidecar=str(tmp_path / "sidecar.json"),
        zenodo_mode="cached", allow_empty=False)


def feed(args):
    return [json.loads(line) for line in Path(args.output).read_text().splitlines()]


def sidecar(args):
    return json.loads(Path(args.sidecar).read_text())


def error_report(args):
    return json.loads(Path(args.sidecar + ".error.json").read_text())


def test_two_pdf_files_are_role_free_and_bound_to_pinned_git(tmp_path, monkeypatch):
    rows = {
        "one.md": yaml_source(),
        "two.md": yaml_source("Second source", "Another author", "2019",
                              ["https://zenodo.org/record/830239#evidence"]),
        "outside.md": yaml_source(year=2022),
        "other.md": yaml_source(link="https://example.org/record/830239"),
        "nolink.md": yaml_source(link=None),
        "many-other.md": yaml_source(link=["https://example.org/a", "https://foo.test/b"]),
        "bom.md": b"\xef\xbb\xbf" + yaml_source(link=None).encode(),
    }
    args = prepare(tmp_path, rows)
    monkeypatch.setattr(resolver.urllib.request, "build_opener",
                        lambda *a: pytest.fail("cached mode made a network request"))
    summary = resolver.run(args)
    rows_out = feed(args)
    side = sidecar(args)
    assert summary["catalogue_rows"] == 7
    assert summary["annual_selections"] == 2
    assert summary["non_zenodo_rows"] == 2
    assert summary["no_link_rows"] == 2
    assert summary["outside_year"] == 1
    assert len(rows_out) == len(side["candidates"]) == 4
    assert all(set(row) == {"candidate_id", "url", "title", "author"} for row in rows_out)
    assert [r["candidate_id"] for r in rows_out] == sorted(r["candidate_id"] for r in rows_out)
    assert side["feed_sha256"] == hashlib.sha256(Path(args.output).read_bytes()).hexdigest()
    assert side["source_commit"] == args.source_commit
    assert side["catalogue"] == "weecology/ogrants"
    raw_tree = subprocess.check_output(["git", "-C", args.catalogue_repo,
                                        "ls-tree", "-r", "-z", args.source_commit,
                                        "--", "_grants"])
    inventory = []
    for line in raw_tree.split(b"\0"):
        if not line:
            continue
        header, path = line.split(b"\t", 1)
        mode, kind, blob = header.decode("ascii").split(" ")
        if mode in {"100644", "100755"} and kind == "blob":
            inventory.append({"path": path.decode("utf-8"), "blob_sha": blob})
    inventory.sort(key=lambda row: row["path"])
    expected_inventory = json.dumps(inventory, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")
    assert side["source_inventory_sha256"] == hashlib.sha256(expected_inventory).hexdigest()
    assert {r["file_key"] for r in side["candidates"]} == {"PartA.pdf", "PartB.pdf"}
    assert any(isinstance(r["source_link"], list) for r in side["candidates"])
    assert all(r["metadata"] == {
        "access_right_present": True, "access_right": None,
        "license_present": True, "license": {"id": "cc-by-4.0"},
    } for r in side["candidates"])
    for item in side["candidates"]:
        identity = ["opengrants-zenodo-candidate/v1", args.source_commit,
                    item["source_path"], item["source_blob_sha"],
                    item["record_id"], item["file_key"]]
        assert item["candidate_id"] == "opengrants-zenodo-" + hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        assert len(item["candidate_id"].split("-")[-1]) == 64


def test_cached_rerender_ignores_worktree_edits_and_is_byte_stable(tmp_path):
    args = prepare(tmp_path)
    resolver.run(args)
    old = (Path(args.output).read_bytes(), Path(args.sidecar).read_bytes())
    (Path(args.catalogue_repo) / "_grants" / "one.md").write_text("not frontmatter")
    args.source_commit = args.source_commit.upper()
    resolver.run(args)
    assert (Path(args.output).read_bytes(), Path(args.sidecar).read_bytes()) == old


def test_source_failure_preserves_successful_pair(tmp_path):
    args = prepare(tmp_path)
    resolver.run(args)
    prior = (Path(args.output).read_bytes(), Path(args.sidecar).read_bytes())
    source = Path(args.catalogue_repo) / "_grants" / "bad.md"
    source.write_text("---\n: invalid: yaml\n---\n")
    git(args.catalogue_repo, "add", ".")
    git(args.catalogue_repo, "commit", "-qm", "bad source")
    args.source_commit = git(args.catalogue_repo, "rev-parse", "HEAD")
    args.allow_empty = True
    with pytest.raises(resolver.ResolverError):
        resolver.run(args)
    assert (Path(args.output).read_bytes(), Path(args.sidecar).read_bytes()) == prior
    assert error_report(args)["summary"]["source_insufficiencies"] == 1


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(id=True),
    lambda d: d.update(files={}),
    lambda d: d["files"][0].update(size=True),
    lambda d: d["files"][0].update(checksum="bad"),
    lambda d: d["files"][0].update(checksum=" : "),
    lambda d: d["files"].append(dict(d["files"][0])),
    lambda d: d["files"][0]["links"].update(self="https://evil.test/api/records/830239/files/PartA.pdf/content"),
    lambda d: d["files"][0]["links"].update(self="https://zenodo.org/api/records/830239/files/PartB.pdf/content"),
])
def test_bad_metadata_preserves_successful_pair(tmp_path, mutation):
    args = prepare(tmp_path)
    resolver.run(args)
    prior = (Path(args.output).read_bytes(), Path(args.sidecar).read_bytes())
    cache = Path(args.metadata_dir) / "zenodo-record-830239.json"
    data = json.loads(cache.read_text())
    mutation(data)
    cache.write_text(json.dumps(data))
    with pytest.raises(resolver.ResolverError):
        resolver.run(args)
    assert (Path(args.output).read_bytes(), Path(args.sidecar).read_bytes()) == prior
    assert error_report(args)["summary"]["metadata_failures"] == 1


def test_missing_metadata_and_valid_zero_pdf_are_different(tmp_path):
    args = prepare(tmp_path)
    Path(args.metadata_dir, "zenodo-record-830239.json").unlink()
    with pytest.raises(resolver.ResolverError):
        resolver.run(args)
    assert error_report(args)["summary"]["metadata_failures"] == 1
    shutil.copyfile(FIXTURES / "zenodo-record-7795521.json",
                    Path(args.metadata_dir) / "zenodo-record-7795521.json")
    (Path(args.catalogue_repo) / "_grants" / "one.md").write_text(
        yaml_source(link="https://zenodo.org/record/7795521"))
    git(args.catalogue_repo, "add", ".")
    git(args.catalogue_repo, "commit", "-qm", "empty record")
    args.source_commit = git(args.catalogue_repo, "rev-parse", "HEAD")
    with pytest.raises(resolver.ResolverError):
        resolver.run(args)
    args.allow_empty = True
    assert resolver.run(args)["emitted_candidates"] == 0
    assert Path(args.output).read_bytes() == b""


def test_live_mode_requests_record_metadata_once_and_keeps_exact_bytes(tmp_path, monkeypatch):
    args = prepare(tmp_path, {"one.md": yaml_source(),
                              "two.md": yaml_source("Second", "Other")})
    args.zenodo_mode = "live"
    raw = (FIXTURES / "zenodo-record-830239.json").read_bytes()
    calls = []
    def fake_fetch(record):
        calls.append(record)
        return raw
    monkeypatch.setattr(resolver, "fetch_metadata", fake_fetch)
    resolver.run(args)
    assert calls == [830239]
    assert (Path(args.metadata_dir) / "zenodo-record-830239.json").read_bytes() == raw
    assert len(feed(args)) == 4


def test_absent_and_present_null_access_and_license_are_distinct(tmp_path):
    args = prepare(tmp_path)
    cache = Path(args.metadata_dir) / "zenodo-record-830239.json"
    data = json.loads(cache.read_text())
    data["metadata"].pop("access_right")
    data["metadata"].pop("license")
    cache.write_text(json.dumps(data))
    resolver.run(args)
    for candidate in sidecar(args)["candidates"]:
        assert candidate["metadata"] == {
            "access_right_present": False, "access_right": None,
            "license_present": False, "license": None,
        }
    data["metadata"]["access_right"] = None
    data["metadata"]["license"] = None
    cache.write_text(json.dumps(data))
    resolver.run(args)
    for candidate in sidecar(args)["candidates"]:
        assert candidate["metadata"] == {
            "access_right_present": True, "access_right": None,
            "license_present": True, "license": None,
        }

