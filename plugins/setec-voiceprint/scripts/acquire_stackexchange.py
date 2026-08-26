#!/usr/bin/env python3
"""acquire_stackexchange.py — read a Stack Exchange data dump into JSONL.

Reads an official Stack Exchange data-dump archive for one site and emits
one JSON record per post. Unlike the other acquirers in this directory
this script performs **no network access at all**: the Stack Exchange
dumps are distributed as 7-zipped XML, so acquisition is a local file
read. Nothing here fetches, crawls, or touches robots.txt.

Why JSONL rather than one ``.txt`` + ``.meta.json`` per piece: a single
site dump holds tens of thousands of short posts (philosophy.stackexchange
is ~74k), so the per-piece file layout used by ``acquire_blog.py`` and
friends is the wrong shape. Records stream to one JSONL file instead.
``compute_content_hash`` is still shared with ``acquisition_core`` so
hashes are comparable across acquirers.

Source shape (see the dump's own ``readme.txt``):

  Posts.xml    one <row> per post. PostTypeId 1 = question, 2 = answer;
               3+ are tag wikis, moderator nominations and other
               non-prose rows, excluded by default.
  Users.xml    one <row> per user; supplies display names and the
               profile URL that CC BY-SA attribution requires.

Licensing, and why it is read per post rather than assumed:

  Every Posts.xml row carries a ``ContentLicense`` attribute naming the
  CC BY-SA version that post was contributed under. A single site mixes
  versions — philosophy.stackexchange's 74,199 posts split 41,736 BY-SA
  4.0 / 32,462 BY-SA 3.0 / 1 BY-SA 2.5 — because the network relicensed
  in 2011 and 2018 and contributions keep the version in force when they
  were made. The ``license.txt`` bundled with the dump states a flat
  "CC BY-SA 3.0" and is therefore wrong for a majority of rows; this
  script always reads the per-row attribute and never infers a license
  from a date or from the bundled file.

  CC BY-SA also requires attribution. ``OwnerUserId`` is absent on posts
  whose author was deleted or anonymous (6,474 of philosophy's rows), but
  ``OwnerDisplayName`` is populated on every one of them, so a name is
  always available and only the profile link is not. Records carry
  ``author_profile_url: null`` and ``author_deleted: true`` in that case
  rather than dropping the author.

Mutable-source dating: a post's text is the *current* text as of the dump
date, so a post created before some cutoff may carry an edit made after
it. Each record reports ``creation_date`` and ``last_edit_date``
separately plus ``effective_date`` = max(creation, last_edit), which is
the date the emitted bytes actually correspond to. Consumers that need a
strict "written before X" subset should filter on ``effective_date``.

Usage:

  # inspect without writing
  py -3.12 acquire_stackexchange.py --dump philosophy.stackexchange.com.7z \\
      --site philosophy.stackexchange.com --dry-run

  # emit JSONL
  py -3.12 acquire_stackexchange.py --dump philosophy.stackexchange.com.7z \\
      --site philosophy.stackexchange.com --out posts.jsonl

Note the ``py -3.12`` invocation: this file's shebang would otherwise
route a bare ``py acquire_stackexchange.py`` to a different interpreter on
Windows.

Reading a ``.7z`` directly requires ``py7zr``. Alternatively extract the
archive yourself and pass ``--dump <dir>``, which needs no extra
dependency.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

# The shared content-identity and private-path helpers live in L1
# `setec.core.acquisition_primitives`, not in the `acquisition_core`
# SURFACE: an acquirer importing that surface is an l2_to_l2 edge, and the
# layering ratchet refuses new ones ("a genuinely new violation in new code
# must be fixed, never added here"). Same functions, same single source.
from setec.core import acquisition_primitives as ac

TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "acquire_stackexchange"
SCRIPT_VERSION = "1.0.0"

# PostTypeId values that carry authored prose. 3+ are tag wikis, tag wiki
# excerpts, moderator nominations and similar; they are not posts a person
# wrote as an argument and are excluded unless asked for.
PROSE_POST_TYPES = ("1", "2")

DUMP_MEMBERS = ("Posts.xml", "Users.xml")
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


# --------------------------------------------------------------- text ----


class _FragmentTextParser(HTMLParser):
    """Stdlib fallback extractor for a Stack Exchange body fragment.

    Bodies are small, well-formed HTML fragments (``p``, ``ul``/``li``,
    ``pre``/``code``, ``blockquote``, ``a``, ``em``, ``strong``), so a
    tag-aware stdlib pass is sufficient and keeps this script usable
    without BeautifulSoup installed.
    """

    _BLOCK = {
        "p", "div", "li", "ul", "ol", "pre", "blockquote", "h1", "h2",
        "h3", "h4", "h5", "h6", "br", "hr", "table", "tr",
    }
    _DROP = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._suppress = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._DROP:
            self._suppress += 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._DROP and self._suppress:
            self._suppress -= 1
        elif tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._suppress:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _normalize_ws(text: str) -> str:
    """Collapse runs of blank lines and strip trailing spaces, LF only."""
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            blank = 0
            out.append(ln.strip())
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip()


def body_to_text(body_html: str) -> str:
    """Convert a post body to plain text.

    Prefers BeautifulSoup for parity with the other acquirers, and falls
    back to the stdlib parser when it is not installed.
    """
    if not body_html:
        return ""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(body_html, "html.parser")
        for bad in soup(["script", "style"]):
            bad.decompose()
        return _normalize_ws(soup.get_text(separator="\n"))
    except ImportError:
        parser = _FragmentTextParser()
        parser.feed(html.unescape(body_html) if "&lt;" in body_html else body_html)
        parser.close()
        return _normalize_ws(parser.text())


# --------------------------------------------------------------- dump ----


_EXTRACT_CACHE: dict = {}


def _dump_dir(dump: Path) -> Path:
    """Return a directory holding the dump's XML members.

    A directory is used as-is. An archive is extracted once into a
    process-lifetime temp directory -- py7zr 1.x exposes no in-memory read,
    and both Posts.xml and Users.xml are needed, so extracting twice would
    double the cost of the most expensive step.
    """
    if dump.is_dir():
        return dump
    key = str(dump.resolve())
    if key in _EXTRACT_CACHE:
        return _EXTRACT_CACHE[key]
    try:
        import py7zr  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "reading a .7z dump needs py7zr (pip install py7zr); or extract "
            "the archive and pass the directory to --dump"
        ) from exc
    import atexit
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="se-dump-"))
    atexit.register(shutil.rmtree, tmp, True)
    with py7zr.SevenZipFile(dump, "r") as archive:
        archive.extract(path=str(tmp), targets=list(DUMP_MEMBERS))
    _EXTRACT_CACHE[key] = tmp
    return tmp


def _open_member(dump: Path, member: str):
    """Open one member of a dump dir or .7z archive as a binary stream."""
    path = _dump_dir(dump) / member
    if not path.exists():
        raise FileNotFoundError(f"{member} not found in {dump}")
    return path.open("rb")


def load_users(dump: Path, site: str) -> dict:
    """Map user id -> {display_name, profile_url}."""
    users: dict[str, dict] = {}
    with _open_member(dump, "Users.xml") as stream:
        for _event, el in ET.iterparse(stream, events=("end",)):
            if el.tag != "row":
                el.clear()
                continue
            uid = el.attrib.get("Id")
            if uid:
                users[uid] = {
                    "display_name": el.attrib.get("DisplayName") or None,
                    "profile_url": f"https://{site}/users/{uid}",
                }
            el.clear()
    return users


def iter_posts(
    dump: Path,
    site: str,
    users: dict,
    *,
    post_types,
    keep_body_html: bool,
    scan_counts: dict | None = None,
):
    """Yield one neutral record per post row."""
    stream = _open_member(dump, "Posts.xml")
    try:
      for _event, el in ET.iterparse(stream, events=("end",)):
        if el.tag != "row":
            el.clear()
            continue
        a = el.attrib
        if scan_counts is not None:
            scan_counts["rows"] = scan_counts.get("rows", 0) + 1
        post_type = a.get("PostTypeId", "")
        if post_types and post_type not in post_types:
            if scan_counts is not None:
                scan_counts["post_type"] = scan_counts.get("post_type", 0) + 1
            el.clear()
            continue

        post_id = a.get("Id", "")
        parent_id = a.get("ParentId")
        # A question is its own thread root; an answer belongs to its parent.
        thread_id = parent_id or post_id

        owner_id = a.get("OwnerUserId")
        user = users.get(owner_id or "", {})
        display = user.get("display_name") or a.get("OwnerDisplayName")
        if not display:
            raise ValueError(f"post {post_id or '<missing-id>'} has no attributable author")
        created = a.get("CreationDate") or None
        edited = a.get("LastEditDate") or None
        content_license = a.get("ContentLicense") or None
        if not created:
            raise ValueError(f"post {post_id or '<missing-id>'} has no CreationDate")
        if not content_license:
            raise ValueError(f"post {post_id or '<missing-id>'} has no ContentLicense")

        text = body_to_text(a.get("Body", ""))
        tags = [t for t in (a.get("Tags") or "").split("|") if t]

        record = {
            "site": site,
            "post_id": post_id,
            "post_type": {"1": "question", "2": "answer"}.get(post_type, post_type),
            "parent_id": parent_id,
            "thread_id": thread_id,
            "source_url": f"https://{site}/questions/{thread_id}#{post_id}",
            "title": a.get("Title") or None,
            "tags": tags,
            "creation_date": created,
            "last_edit_date": edited,
            # The date the emitted bytes actually correspond to.
            "effective_date": max([d for d in (created, edited) if d], default=None),
            # Read from the row, never inferred from a date or license.txt.
            "content_license": content_license,
            "author_user_id": owner_id,
            "author_display_name": display,
            "author_profile_url": user.get("profile_url"),
            "author_deleted": owner_id is None or owner_id not in users,
            "score": int(a.get("Score", 0) or 0),
            "text": text,
            "content_hash": ac.compute_content_hash(text),
            "word_count": len(text.split()),
        }
        if keep_body_html:
            record["body_html"] = a.get("Body", "")
        el.clear()
        yield record
    finally:
        stream.close()


# ---------------------------------------------------------------- cli ----


def _site_host(value: str) -> str:
    host = value.strip().lower().rstrip(".")
    labels = host.split(".")
    if (len(host) > 253 or len(labels) < 2
            or any(_DNS_LABEL_RE.fullmatch(label) is None for label in labels)):
        raise argparse.ArgumentTypeError(
            "must be a bare DNS host such as philosophy.stackexchange.com"
        )
    return host


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _iso_cutoff(value: str) -> str:
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO date in YYYY-MM-DD form") from exc
    return value


def _source_identity(dump: Path) -> dict:
    root = _dump_dir(dump) if dump.is_dir() else None
    if root is None:
        item = dump.resolve().stat()
        return {
            "kind": "archive",
            "path": str(dump.resolve()),
            "size": item.st_size,
            "mtime_ns": item.st_mtime_ns,
        }
    members = {}
    for name in DUMP_MEMBERS:
        item = (root / name).stat()
        members[name] = {"size": item.st_size, "mtime_ns": item.st_mtime_ns}
    return {"kind": "directory", "path": str(root.resolve()), "members": members}


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _load_existing_records(path: Path, site: str) -> dict[str, dict]:
    """Validate resume output and discard only an incomplete final write."""
    seen: dict[str, dict] = {}
    with path.open("r+b") as fh:
        line_no = 0
        while True:
            start = fh.tell()
            raw = fh.readline()
            if not raw:
                break
            line_no += 1
            try:
                record = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                # Only a non-newline-terminated tail can be an interrupted
                # append. A complete malformed line is corruption and must
                # never be silently discarded during resume.
                if fh.read(1) == b"" and not raw.endswith(b"\n"):
                    fh.truncate(start)
                    print(
                        f"resume: discarded incomplete final JSONL record at line {line_no}",
                        file=sys.stderr,
                    )
                    break
                raise ValueError(f"invalid JSONL at line {line_no}: {exc}") from exc
            if not isinstance(record, dict) or record.get("site") != site:
                raise ValueError(f"resume record {line_no} is not bound to site {site}")
            text = record.get("text")
            content_hash = record.get("content_hash")
            if (not isinstance(text, str)
                    or not isinstance(content_hash, str)
                    or ac.compute_content_hash(text) != content_hash):
                raise ValueError(f"resume record {line_no} has an invalid content_hash")
            post_id = record.get("post_id")
            if not isinstance(post_id, str) or not post_id or post_id in seen:
                raise ValueError(f"resume record {line_no} has an invalid/duplicate post_id")
            seen[post_id] = record
    return seen


def _validate_existing_records(
    dump: Path,
    site: str,
    users: dict,
    post_types: tuple[str, ...],
    keep_body_html: bool,
    existing: dict[str, dict],
) -> None:
    """Bind every saved row, including attribution, to its canonical dump row."""
    pending = set(existing)
    for regenerated in iter_posts(
        dump, site, users, post_types=post_types,
        keep_body_html=keep_body_html,
    ):
        post_id = regenerated["post_id"]
        if post_id not in pending:
            continue
        if existing[post_id] != regenerated:
            raise ValueError(
                f"resume record for post_id {post_id} differs from the bound dump"
            )
        pending.remove(post_id)
        if not pending:
            return
    if pending:
        raise ValueError(
            "resume output contains post_ids absent from the bound dump: "
            + ", ".join(sorted(pending))
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dump", required=True, type=Path,
                   help="Path to a site .7z dump, or a directory of its extracted XML.")
    p.add_argument("--site", required=True, type=_site_host,
                   help="Site host, e.g. philosophy.stackexchange.com. Used to build "
                        "source and author-profile URLs for attribution.")
    p.add_argument("--out", type=Path,
                   help="Write JSONL here. Omit with --dry-run to only report counts.")
    p.add_argument("--post-types", nargs="+", choices=PROSE_POST_TYPES,
                   default=list(PROSE_POST_TYPES),
                   help="Authored prose PostTypeId values to keep: 1=questions, "
                        "2=answers (default: both).")
    p.add_argument("--min-words", type=int, default=0,
                   help="Drop posts under this many words (default 0 = keep all).")
    p.add_argument("--created-before", type=_iso_cutoff,
                   help="Keep posts whose creation_date is before this ISO date.")
    p.add_argument("--effective-before", type=_iso_cutoff,
                   help="Keep posts whose effective_date is before this ISO date. This "
                        "is the mutable-source-safe filter: it excludes a pre-cutoff post "
                        "that carries a post-cutoff edit.")
    p.add_argument("--max-items", type=_positive_int, help="Stop after this many newly admitted posts.")
    p.add_argument("--keep-body-html", action="store_true",
                   help="Also store the raw HTML body on each record.")
    p.add_argument("--summary", type=Path, help="Write a JSON run summary here.")
    p.add_argument("--resume", action="store_true",
                   help="Continue an interrupted output after validating its source/filters and existing JSONL.")
    p.add_argument("--progress-every", type=_positive_int, default=1000,
                   help="Emit aggregate progress and fsync after this many new records (default 1000).")
    p.add_argument("--allow-empty", action="store_true",
                   help="Allow a new non-dry run that admits zero records.")
    p.add_argument("--allow-public-output", action="store_true",
                   help="Permit output outside ai-prose-baselines-private for this public licensed corpus.")
    p.add_argument("--dry-run", action="store_true",
                   help="Count and report without writing JSONL.")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.dump.exists():
        print(f"ERROR: dump not found: {args.dump}", file=sys.stderr)
        return 2
    if not args.dry_run and not args.out:
        print("ERROR: pass --out, or use --dry-run", file=sys.stderr)
        return 2
    if args.resume and (args.dry_run or not args.out):
        print("ERROR: --resume requires a non-dry --out run", file=sys.stderr)
        return 2

    output_paths = [p for p in (args.out, args.summary) if p is not None]
    if output_paths:
        ac.check_output_privacy(
            output_paths,
            allow_public=args.allow_public_output,
            tool=TOOL_NAME,
        )

    post_types = tuple(args.post_types)
    users = load_users(args.dump, args.site)

    scan_counts = {"rows": 0, "post_type": 0}
    admitted = 0
    already_present: set[str] = set()
    by_license: dict = {}
    by_type: dict = {}
    deleted_authors = 0
    dropped = {"post_type": 0, "min_words": 0, "created_before": 0, "effective_before": 0,
               "resume_duplicate": 0}

    out_fh = None
    state_path = args.out.with_name(args.out.name + ".resume.json") if args.out else None
    contract = {
        "schema": "setec-stackexchange-resume/1",
        "tool_version": SCRIPT_VERSION,
        "source": _source_identity(args.dump),
        "site": args.site,
        "post_types": list(post_types),
        "min_words": args.min_words,
        "created_before": args.created_before,
        "effective_before": args.effective_before,
        "keep_body_html": args.keep_body_html,
    }
    if args.out and not args.dry_run:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        if args.resume:
            if not args.out.exists() or not state_path.exists():
                print("ERROR: --resume needs both the existing --out and its .resume.json sidecar", file=sys.stderr)
                return 2
            try:
                saved = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    raise ValueError("resume sidecar must be a JSON object")
                if saved.get("contract") != contract:
                    raise ValueError("source or filter contract differs from the interrupted run")
                existing_records = _load_existing_records(args.out, args.site)
                _validate_existing_records(
                    args.dump, args.site, users, post_types,
                    args.keep_body_html, existing_records,
                )
                already_present = set(existing_records)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(f"ERROR: refusing resume: {exc}", file=sys.stderr)
                return 2
            out_fh = args.out.open("a", encoding="utf-8", newline="\n")
        else:
            if args.out.exists() or state_path.exists():
                print("ERROR: output or resume sidecar already exists; use --resume or a new --out", file=sys.stderr)
                return 2
            _atomic_write_json(state_path, {"contract": contract, "complete": False, "records": 0})
            out_fh = args.out.open("x", encoding="utf-8", newline="\n")

    stopped_early = False
    try:
        for rec in iter_posts(args.dump, args.site, users,
                              post_types=post_types,
                              keep_body_html=args.keep_body_html,
                              scan_counts=scan_counts):
            if rec["post_id"] in already_present:
                dropped["resume_duplicate"] += 1
                continue
            if args.min_words and rec["word_count"] < args.min_words:
                dropped["min_words"] += 1
                continue
            if args.created_before and (rec["creation_date"] or "") >= args.created_before:
                dropped["created_before"] += 1
                continue
            if args.effective_before and (rec["effective_date"] or "") >= args.effective_before:
                dropped["effective_before"] += 1
                continue

            admitted += 1
            by_license[rec["content_license"] or "<absent>"] = \
                by_license.get(rec["content_license"] or "<absent>", 0) + 1
            by_type[rec["post_type"]] = by_type.get(rec["post_type"], 0) + 1
            if rec["author_deleted"]:
                deleted_authors += 1
            if out_fh:
                out_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                if admitted % args.progress_every == 0:
                    out_fh.flush()
                    os.fsync(out_fh.fileno())
                    _atomic_write_json(
                        state_path,
                        {"contract": contract, "complete": False,
                         "records": len(already_present) + admitted},
                    )
                    print(json.dumps({"posts_admitted": admitted,
                                      "records_total": len(already_present) + admitted}),
                          file=sys.stderr, flush=True)
            if args.max_items and admitted >= args.max_items:
                stopped_early = True
                break
    finally:
        if out_fh:
            out_fh.flush()
            os.fsync(out_fh.fileno())
            out_fh.close()

    dropped["post_type"] = scan_counts["post_type"]

    summary = {
        "site": args.site,
        "dump": str(args.dump),
        "users_indexed": len(users),
        "posts_scanned": scan_counts["rows"],
        "posts_admitted": admitted,
        "records_already_present": len(already_present),
        "records_total": len(already_present) + admitted,
        "dropped": dropped,
        "by_post_type": by_type,
        "by_content_license": by_license,
        "authors_deleted_profile_unavailable": deleted_authors,
        "out": str(args.out) if (args.out and not args.dry_run) else None,
    }
    print(json.dumps(summary, indent=2))
    if args.summary:
        _atomic_write_json(args.summary, summary)
    empty_refusal = (
        admitted == 0 and not already_present and not args.allow_empty
    )
    if state_path:
        _atomic_write_json(
            state_path,
            {"contract": contract, "complete": not stopped_early and not empty_refusal,
             "records": len(already_present) + admitted, "summary": summary},
        )
    if empty_refusal:
        print("ERROR: zero records admitted; inspect filters or pass --allow-empty", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
