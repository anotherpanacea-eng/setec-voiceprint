#!/usr/bin/env python3
"""Build an ``acquire_pdf_urls`` feed from the Tanner lecture archive.

The tool fetches the official lecture sitemap, visits each lecture page at a
minimum ten-second cadence, and records direct PDF links plus the page's title,
speaker, and lecture date.  It does *not* fetch or validate the PDFs.  Its
canonical JSONL output is public metadata; the later ``acquire_pdf_urls`` run
is what downloads prose into the private, fair-use-research impostor pool.

A separate atomic state file checkpoints every attempted page.  Ordinary
``--resume`` skips every recorded outcome.  ``--retry-errors`` is the only way
to revisit prior fetch/parse errors; pages already classified as
``pdf_link_found`` or ``no_pdf`` always remain terminal.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any, Protocol
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET


TASK_SURFACE = "voice_coherence_acquisition"
TOOL_NAME = "build_tanner_source_list"
SCRIPT_VERSION = "1.0"

DEFAULT_SITEMAP_URL = (
    "https://tannerlectures.org/wp-sitemap-posts-lectures-1.xml"
)
DEFAULT_USER_AGENT = (
    "SETEC-voiceprint/{version} Tanner public-metadata list builder"
)
MIN_RATE_LIMIT_SECONDS = 10.0
STATE_SCHEMA_VERSION = "1.0"
TERMINAL_STATUSES = {
    "pdf_link_found", "no_pdf", "fetch_error", "parse_error",
}
RETRYABLE_STATUSES = {"fetch_error", "parse_error"}
HTML_VOID_ELEMENTS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


class TannerListError(RuntimeError):
    """A bounded input, state, or source-contract failure."""


class FetchResult:
    def __init__(self, url: str, status: int, text: str, final_url: str = ""):
        self.url = url
        self.status = status
        self.text = text
        self.final_url = final_url or url

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Keep redirect destinations behind the same robots/cadence boundary."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PoliteHttpFetcher:
    """Small stdlib fetcher with robots enforcement and a hard delay floor."""

    def __init__(
        self, *, rate_limit_seconds: float, user_agent: str, timeout: float,
    ) -> None:
        if (
            not math.isfinite(rate_limit_seconds)
            or rate_limit_seconds < MIN_RATE_LIMIT_SECONDS
        ):
            raise TannerListError(
                f"rate limit must be at least {MIN_RATE_LIMIT_SECONDS:g} seconds"
            )
        if not math.isfinite(timeout) or timeout <= 0:
            raise TannerListError("timeout must be a finite positive number")
        if not user_agent.strip() or any(
            ord(char) < 32 or ord(char) == 127 for char in user_agent
        ):
            raise TannerListError("user agent must be nonempty and contain no controls")
        self.rate_limit_seconds = rate_limit_seconds
        self.user_agent = user_agent
        self.timeout = timeout
        self._last_fetch_per_host: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._opener = urllib.request.build_opener(_RefuseRedirects())

    def _request(self, url: str) -> FetchResult:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent},
            )
            with self._opener.open(req, timeout=self.timeout) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                text = body.decode(charset, errors="replace")
                return FetchResult(
                    url, int(response.status), text, response.geturl(),
                )
        except urllib.error.HTTPError as exc:
            return FetchResult(url, int(exc.code), "", exc.geturl())
        except (OSError, ValueError, urllib.error.URLError):
            return FetchResult(url, 0, "", url)

    def _wait(self, url: str) -> None:
        host = urllib.parse.urlparse(url).netloc.lower()
        last = self._last_fetch_per_host.get(host)
        if last is not None:
            remaining = self.rate_limit_seconds - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)

    def _record(self, url: str) -> None:
        self._last_fetch_per_host[
            urllib.parse.urlparse(url).netloc.lower()
        ] = time.monotonic()

    def _load_robots(
        self, host_root: str,
    ) -> urllib.robotparser.RobotFileParser | None:
        robots_url = host_root + "/robots.txt"
        self._wait(robots_url)
        result = self._request(robots_url)
        self._record(robots_url)
        if result.status in {404, 410}:
            return None
        if not result.ok:
            raise TannerListError(
                f"robots policy fetch failed with status {result.status}"
            )
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(result.text.splitlines())
        crawl_delay = parser.crawl_delay(self.user_agent)
        if crawl_delay is None:
            crawl_delay = parser.crawl_delay("*")
        if crawl_delay is not None:
            self.rate_limit_seconds = max(
                self.rate_limit_seconds, float(crawl_delay),
            )
        return parser

    def _allowed(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root not in self._robots:
            self._robots[root] = self._load_robots(root)
        parser = self._robots[root]
        return parser is None or parser.can_fetch(self.user_agent, url)

    def fetch(self, url: str) -> FetchResult:
        try:
            allowed = self._allowed(url)
        except TannerListError:
            return FetchResult(url, 0, "", url)
        if not allowed:
            return FetchResult(url, 403, "", url)
        self._wait(url)
        result = self._request(url)
        self._record(url)
        return result


def _class_tokens(attrs: list[tuple[str, str | None]]) -> set[str]:
    raw = dict(attrs).get("class") or ""
    return set(raw.split())


class _LecturePageParser(HTMLParser):
    """Extract the four fields published by one Tanner lecture page."""

    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self._stack: list[str] = []
        self._captures: dict[str, tuple[int, list[str]]] = {}
        self._captured: dict[str, list[str]] = {
            "title": [], "speaker": [], "date": [],
        }
        self._transcript_depth: int | None = None
        self.pdf_urls: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag in HTML_VOID_ELEMENTS:
            return
        self._stack.append(tag)
        depth = len(self._stack)
        classes = _class_tokens(attrs)
        if tag == "h1" and "page-title" in classes:
            self._captures.setdefault("title", (depth, []))
        elif tag == "div" and "speaker-name" in classes:
            self._captures.setdefault("speaker", (depth, []))
        elif (
            tag == "div" and "lecture-date" in classes
            and not self._captured["date"] and "date" not in self._captures
        ):
            self._captures["date"] = (depth, [])
        if tag == "div" and "lecture-transcript" in classes:
            self._transcript_depth = depth
        if tag == "a" and self._transcript_depth is not None:
            href = dict(attrs).get("href") or ""
            try:
                resolved = urllib.parse.urljoin(
                    self.page_url, html.unescape(href),
                )
            except ValueError:
                return
            if _is_pdf_url(resolved):
                self.pdf_urls.append(resolved)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in HTML_VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        for _field, (_depth, parts) in self._captures.items():
            parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        try:
            match_index = len(self._stack) - 1 - self._stack[::-1].index(tag)
        except ValueError:
            return
        closing_depth = match_index + 1
        for field, (start_depth, parts) in list(self._captures.items()):
            if start_depth >= closing_depth:
                value = " ".join("".join(parts).split())
                self._captured[field].append(value)
                del self._captures[field]
        if (
            self._transcript_depth is not None
            and self._transcript_depth >= closing_depth
        ):
            self._transcript_depth = None
        del self._stack[match_index:]


_DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)


def _iso_date(text: str) -> str | None:
    match = _DATE_RE.search(text)
    if not match:
        return None
    raw = match.group(0)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_lecture_page(page_url: str, source: str) -> list[dict[str, str]]:
    parser = _LecturePageParser(page_url)
    parser.feed(source)
    parser.close()
    pdf_urls = sorted(set(parser.pdf_urls))
    if not pdf_urls:
        return []
    title = parser._captured["title"][0] if parser._captured["title"] else ""
    speaker = (
        parser._captured["speaker"][0]
        if parser._captured["speaker"] else ""
    )
    speaker = re.sub(r"^Speaker\s*", "", speaker, flags=re.IGNORECASE).strip()
    date_text = parser._captured["date"][0] if parser._captured["date"] else ""
    date = _iso_date(date_text)
    missing = [
        name for name, value in (
            ("title", title), ("speaker", speaker), ("date", date),
        ) if not value
    ]
    if missing:
        raise TannerListError(
            "PDF-bearing lecture page missing metadata: " + ", ".join(missing)
        )
    return [
        {
            "url": url,
            "title": title,
            "author": speaker,
            "date": date,
            "artifact_profile": "tanner",
        }
        for url in pdf_urls
    ]


def parse_sitemap(sitemap_url: str, source: str) -> list[str]:
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise TannerListError(f"invalid sitemap XML: {exc}") from exc
    expected = urllib.parse.urlparse(sitemap_url)
    urls: set[str] = set()
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] != "loc" or not (elem.text or "").strip():
            continue
        url = (elem.text or "").strip()
        parsed = urllib.parse.urlparse(url)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.netloc.lower() == expected.netloc.lower()
            and parsed.path.startswith("/lectures/")
        ):
            urls.add(url)
    if not urls:
        raise TannerListError("sitemap contains no Tanner lecture URLs")
    return sorted(urls)


def _inventory_sha256(urls: list[str]) -> str:
    # JSON framing is injective for arbitrary URL strings; newline-joining is
    # not, because a single malformed <loc> may itself contain a newline.
    payload = json.dumps(
        urls, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_pdf_url(value: str) -> bool:
    if (
        value != value.strip()
        or any(
            char.isspace() or unicodedata.category(char) == "Cc"
            for char in value
        )
    ):
        return False
    try:
        parsed = urllib.parse.urlparse(value)
        hostname = parsed.hostname
        # Access validates a textual or out-of-range port instead of leaving it
        # latent in netloc for a later urllib traceback.
        parsed.port
    except ValueError:
        return False
    if not hostname or parsed.username is not None or parsed.password is not None:
        return False
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    labels = ascii_hostname.rstrip(".").split(".")
    if not labels or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in labels
    ):
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.path.lower().endswith(".pdf")
    )


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", delete=False,
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp",
        ) as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name:
            try:
                Path(tmp_name).unlink()
            except OSError:
                pass


def _render_output(state: dict[str, Any]) -> str:
    by_url: dict[str, dict[str, str]] = {}
    for page_url in sorted(state["pages"]):
        result = state["pages"][page_url]
        if result.get("status") != "pdf_link_found":
            continue
        for row in result.get("rows") or []:
            exact = {
                key: row[key]
                for key in (
                    "url", "title", "author", "date", "artifact_profile",
                )
            }
            previous = by_url.get(exact["url"])
            if previous is not None and previous != exact:
                raise TannerListError(
                    f"conflicting metadata for PDF URL {exact['url']}"
                )
            by_url[exact["url"]] = exact
    return "".join(
        json.dumps(by_url[url], sort_keys=True, ensure_ascii=False) + "\n"
        for url in sorted(by_url)
    )


def _save(state_path: Path, output_path: Path, state: dict[str, Any]) -> None:
    _atomic_write(
        state_path,
        json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )
    _atomic_write(output_path, _render_output(state))


def _load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TannerListError(f"state is unreadable or corrupt: {path}: {exc}") from exc
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("contract"), dict)
        or not isinstance(value.get("pages"), dict)
    ):
        raise TannerListError(f"state has invalid shape: {path}")
    for page_url, result in value["pages"].items():
        if not isinstance(page_url, str) or not isinstance(result, dict):
            raise TannerListError(f"state has invalid page outcome: {page_url}")
        status = result.get("status")
        rows = result.get("rows")
        if status not in TERMINAL_STATUSES or not isinstance(rows, list):
            raise TannerListError(f"state has invalid page outcome: {page_url}")
        if status == "pdf_link_found" and not rows:
            raise TannerListError(f"state has empty PDF outcome: {page_url}")
        if status != "pdf_link_found" and rows:
            raise TannerListError(f"state has rows for non-PDF outcome: {page_url}")
        for row in rows:
            if not isinstance(row, dict):
                raise TannerListError(f"state has invalid PDF row: {page_url}")
            try:
                parsed_date = dt.date.fromisoformat(row.get("date", ""))
            except (TypeError, ValueError):
                parsed_date = None
            if (
                set(row) != {
                    "url", "title", "author", "date", "artifact_profile",
                }
                or not all(
                    isinstance(item, str) and bool(item.strip())
                    for item in row.values()
                )
                or not _is_pdf_url(row.get("url", ""))
                or row.get("artifact_profile") != "tanner"
                or parsed_date is None
                or parsed_date.isoformat() != row["date"]
            ):
                raise TannerListError(f"state has invalid PDF row: {page_url}")
    return value


def _contract(args: argparse.Namespace, urls: list[str]) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "script_version": SCRIPT_VERSION,
        "sitemap_url": args.sitemap_url,
        "ordered_inventory_sha256": _inventory_sha256(urls),
        "user_agent": args.user_agent,
        "rate_limit_seconds": float(args.rate_limit),
    }


def run(args: argparse.Namespace, fetcher: Fetcher | None = None) -> int:
    output_path = Path(args.output).expanduser()
    state_path = (
        Path(args.state).expanduser()
        if args.state else output_path.with_suffix(output_path.suffix + ".state.json")
    )
    if output_path.resolve() == state_path.resolve():
        raise TannerListError("--output and --state must resolve to different files")
    if (
        not math.isfinite(args.rate_limit)
        or args.rate_limit < MIN_RATE_LIMIT_SECONDS
    ):
        raise TannerListError(
            f"--rate-limit must be at least {MIN_RATE_LIMIT_SECONDS:g} seconds"
        )
    if args.resume and not state_path.is_file():
        raise TannerListError(f"--resume requires an existing state file: {state_path}")
    if not args.resume and (state_path.exists() or output_path.exists()):
        raise TannerListError(
            "fresh run refuses existing output/state; use --resume or new paths"
        )
    if fetcher is None:
        fetcher = PoliteHttpFetcher(
            rate_limit_seconds=args.rate_limit,
            user_agent=args.user_agent,
            timeout=args.timeout,
        )
    sitemap = fetcher.fetch(args.sitemap_url)
    if not sitemap.ok or not sitemap.text:
        raise TannerListError(f"sitemap fetch failed with status {sitemap.status}")
    urls = parse_sitemap(args.sitemap_url, sitemap.text)
    contract = _contract(args, urls)
    if args.resume:
        state = _load_state(state_path)
        if state.get("contract") != contract:
            raise TannerListError("resume state does not match sitemap inventory/options")
        extra_pages = sorted(set(state["pages"]) - set(urls))
        if extra_pages:
            raise TannerListError(
                "resume state contains pages outside the bound sitemap inventory"
            )
        _save(state_path, output_path, state)
    else:
        state = {"contract": contract, "pages": {}}
        _save(state_path, output_path, state)

    attempted = 0
    for index, page_url in enumerate(urls, start=1):
        prior = state["pages"].get(page_url)
        if prior is not None and not (
            args.retry_errors and prior.get("status") in RETRYABLE_STATUSES
        ):
            continue
        if args.max_pages is not None and attempted >= args.max_pages:
            break
        attempted += 1
        sys.stderr.write(f"[{index}/{len(urls)}] {page_url}\n")
        page = fetcher.fetch(page_url)
        if not page.ok or not page.text:
            result = {
                "status": "fetch_error", "rows": [],
                "detail": f"status={page.status}",
            }
        else:
            try:
                rows = parse_lecture_page(page.final_url or page_url, page.text)
                result = {
                    "status": "pdf_link_found" if rows else "no_pdf",
                    "rows": rows, "detail": "",
                }
            except TannerListError as exc:
                result = {
                    "status": "parse_error", "rows": [], "detail": str(exc),
                }
        state["pages"][page_url] = result
        _save(state_path, output_path, state)
        sys.stderr.write(f"  {result['status']}\n")

    counts = {status: 0 for status in sorted(TERMINAL_STATUSES)}
    for result in state["pages"].values():
        counts[result["status"]] += 1
    output_rows = len(_render_output(state).splitlines())
    sys.stderr.write(
        "Tanner list summary: "
        + ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
        + f", output_rows={output_rows}, attempted_this_run={attempted}\n"
    )
    if output_rows == 0 and not args.allow_empty:
        return 1
    if args.max_pages is None and (
        counts["fetch_error"] or counts["parse_error"]
    ):
        return 1
    return 0


def _rate_limit(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < MIN_RATE_LIMIT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"must be at least {MIN_RATE_LIMIT_SECONDS:g} seconds"
        )
    return parsed


def _timeout(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Build a checkpointed Tanner lecture PDF URL list for "
            "acquire_pdf_urls.py without downloading any PDFs."
        ),
    )
    parser.add_argument("--output", required=True, help="Canonical JSONL feed path.")
    parser.add_argument("--state", help="Atomic checkpoint path (default: <output>.state.json).")
    parser.add_argument("--sitemap-url", default=DEFAULT_SITEMAP_URL)
    parser.add_argument(
        "--rate-limit", type=_rate_limit, default=MIN_RATE_LIMIT_SECONDS,
        help="Seconds between same-host requests; minimum/default 10.",
    )
    parser.add_argument("--timeout", type=_timeout, default=30.0)
    parser.add_argument(
        "--user-agent", default=DEFAULT_USER_AGENT.format(version=SCRIPT_VERSION),
    )
    parser.add_argument("--max-pages", type=int, help="Cap page attempts in this invocation.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-errors", action="store_true",
        help="With --resume, revisit only prior fetch_error/parse_error pages.",
    )
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="Exit 0 when the current checkpoint contains no PDF links.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.retry_errors and not args.resume:
        parser.error("--retry-errors requires --resume")
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    try:
        return run(args)
    except TannerListError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    sys.exit(main())
