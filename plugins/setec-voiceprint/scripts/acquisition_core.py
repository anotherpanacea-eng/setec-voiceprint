#!/usr/bin/env python3
"""acquisition_core.py — shared helpers for corpus acquisition.

The acquisition scripts (`acquire_blog.py`, future `acquire_magazine.py`,
and `pdf_extract.py`) share a common pipeline:

  fetch → extract plain text → preprocess (corpus hygiene) → hash →
    deduplicate → write `.txt` + `.meta.json` → emit draft manifest entry.

This module factors out the parts that don't vary by source: slug rules,
content hashing, date-to-era mapping, stable private identifier redaction,
the output-path convention, the fetcher protocol that tests can substitute,
the per-file write, the manifest entry composer, and the run-summary
aggregator. Acquisition scripts import these helpers instead of reimplementing
them, keeping per-script code focused on source-specific extraction (Substack
selectors vs. WordPress selectors vs. PDF text-layer extraction).

Privacy: all output paths are checked against the marker-based
`ai-prose-baselines-private` rule that voice-profile tools already use
(`voice_profile.is_private_output_path`, mirrored in
`voice_drift_tracker._check_output_privacy`). Impostor text is voice-
cloning input from someone else's prose; it is never published or
distributed. The acquisition pipeline writes only into a private path
unless the user explicitly opts out with `--allow-public-output`.

Network behavior: the `Fetcher` class is the only place HTTP happens.
Tests inject a `FixtureFetcher` that maps URLs to local fixture files,
so the acquisition scripts can be exercised end-to-end without network
access. Production runs construct a `RequestsFetcher` that honors
`robots.txt`, applies a per-host rate limit, and identifies itself
with a SETEC user-agent header.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


# Task-surface tag. Acquisition is upstream of every voice-coherence
# tool that consumes the impostor pool, so it gets its own surface.
TASK_SURFACE = "voice_coherence_acquisition"

# Default User-Agent advertised to upstream sites. Identifies the
# framework + a stable contact route so site operators can correlate
# traffic and reach out if our scraping bothers them.
DEFAULT_USER_AGENT = (
    "setec-voiceprint/{version} (+https://github.com/anotherpanacea-eng/"
    "setec-voiceprint)"
)

# Marker directory name for the private-safe path check. Mirrors the
# existing `voice_profile.is_private_output_path` convention.
PRIVATE_DIR_NAME = "ai-prose-baselines-private"

# Default base directory for acquired text. Resolved through the
# `SETEC_BASELINES_DIR` env var if set, else falls back to a sibling
# of the repo (the documented standard layout).
DEFAULT_BASELINES_ENV = "SETEC_BASELINES_DIR"
DEFAULT_BASELINES_FALLBACK = (
    Path.home() / "Documents" / "Claude Cowork Working Folder"
    / "ai-prose-baselines-private"
)


# --------------- Slug + hash utilities -----------------------------


_SLUG_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_SLUG_TRIM_RE = re.compile(r"^-+|-+$")


def slugify(text: str, *, max_length: int = 80) -> str:
    """Filename-safe slug: ASCII-folded, lowercase, hyphenated.

    Unicode is normalized to NFKD then encoded as ASCII (with
    non-encodable characters dropped) so European diacritics and
    smart quotes don't leak into filenames. The result is bounded
    to ``max_length`` to keep paths well under typical filesystem
    limits even after parent directories are prepended.

    Empty results (text that was all punctuation or non-ASCII)
    return ``"untitled"`` rather than an empty string so callers
    don't accidentally write to a directory-named file.
    """
    if not text:
        return "untitled"
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    lowered = folded.lower()
    slug = _SLUG_NON_ALNUM_RE.sub("-", lowered)
    slug = _SLUG_TRIM_RE.sub("", slug)
    if not slug:
        return "untitled"
    if len(slug) > max_length:
        # Trim at a word boundary if possible.
        slug = slug[:max_length].rsplit("-", 1)[0] or slug[:max_length]
    return slug


def author_to_persona_slug(author: str, *, suffix: str = "personal") -> str:
    """Generate a deterministic persona slug from an author's name.

    Rule: ``lastname_firstname_<suffix>`` for two-or-more-token names,
    ``<single>_<suffix>`` for one-token names. Unicode is normalized
    to ASCII; punctuation stripped; tokens joined on underscore.

    The spec calls this out for `acquire_magazine.py`'s
    ``--persona-from-author`` mode but the rule is generic enough to
    factor here. Tests pin the slug for stability across runs.
    """
    folded = unicodedata.normalize("NFKD", author or "")
    folded = folded.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9\s-]", " ", folded).strip()
    parts = [p for p in re.split(r"[\s-]+", cleaned) if p]
    if not parts:
        return f"unknown_{suffix}"
    if len(parts) == 1:
        return f"{parts[0].lower()}_{suffix}"
    last = parts[-1].lower()
    first = "_".join(p.lower() for p in parts[:-1])
    return f"{last}_{first}_{suffix}"


def compute_content_hash(text: str) -> str:
    """SHA-256 of cleaned text, prefixed ``sha256:``.

    The prefix matches the manifest convention in
    ``references/manifest-schema.md`` and lets future hash families
    (e.g., a normalized-text fingerprint) coexist without ambiguity.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# --------------- Date parsing -------------------------------------


_ISO_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$")


def parse_iso_date(text: str | None) -> _dt.date | None:
    """Parse an ISO partial date (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`).

    Returns ``None`` for null/empty/unparseable input rather than
    raising — acquisition data is messy and a malformed date should
    skip a single entry's date filter rather than abort the run.

    Bare-year and year-month strings are anchored to January 1 / day 1
    so callers can compare with ``<=`` / ``>=`` against a window.
    """
    if not text:
        return None
    text = str(text).strip()
    m = _ISO_DATE_RE.match(text)
    if not m:
        # Try python-dateutil if installed for non-ISO feed formats.
        try:
            from dateutil import parser as _du_parser  # type: ignore
        except ImportError:
            return None
        try:
            parsed = _du_parser.parse(text, default=_dt.datetime(1970, 1, 1))
        except (ValueError, OverflowError):
            return None
        return parsed.date()
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 1
    day = int(m.group(3)) if m.group(3) else 1
    try:
        return _dt.date(year, month, day)
    except ValueError:
        return None


def era_from_date(date: _dt.date | None) -> str:
    """Map an acquisition date onto the manifest's coarse AI-era bands."""
    if date is None:
        return "undated"
    if date < _dt.date(2022, 11, 1):
        return "pre_chatgpt"
    if date < _dt.date(2024, 7, 1):
        return "pre_ai_widespread"
    return "post_ai_widespread"


# --------------- Stable private-identity redaction ----------------


class StableRedactionMap:
    """Persist raw identifiers behind stable sequential private labels.

    The persisted JSON map is the only place raw recipient/contact identifiers
    live.  Callers select the public label prefix and key normalization rule;
    ``reuse_gaps`` preserves source-specific numbering contracts.
    """

    def __init__(
        self,
        path: Path,
        *,
        label_prefix: str,
        normalize_key: Callable[[str], str] | None = None,
        display_names: dict[str, str] | None = None,
        reuse_gaps: bool = True,
        map_name: str | None = None,
        error_factory: Callable[[str], Exception] = ValueError,
    ) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", label_prefix):
            raise ValueError("label_prefix must be a lowercase identifier")
        self.path = path
        self.label_prefix = label_prefix
        self._normalize_key = normalize_key or (lambda value: value)
        self._reuse_gaps = reuse_gaps
        self._map_name = map_name or f"{label_prefix} map"
        self._error_factory = error_factory
        self._map: dict[str, str] = {}
        self._display_names = {
            self._normalize_key(key): value
            for key, value in (display_names or {}).items()
        }

        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                self._fail(f"could not read {self._map_name} {path}: {exc}")
            label_re = re.compile(rf"{re.escape(label_prefix)}_[0-9]+")
            if not isinstance(loaded, dict) or not all(
                isinstance(key, str)
                and isinstance(label, str)
                and label_re.fullmatch(label)
                for key, label in loaded.items()
            ):
                self._fail(
                    f"{self._map_name} {path} must be a JSON object mapping "
                    f"strings to {label_prefix}_NN labels."
                )
            normalized = {
                self._normalize_key(key): label for key, label in loaded.items()
            }
            if len(normalized) != len(loaded):
                self._fail(
                    f"{self._map_name} {path} contains duplicate normalized keys."
                )
            if len(set(normalized.values())) != len(normalized):
                self._fail(
                    f"{self._map_name} {path} reuses a {label_prefix}_NN label."
                )
            self._map = normalized

    def _fail(self, message: str) -> None:
        raise self._error_factory(message)

    def _used_numbers(self) -> set[int]:
        prefix = f"{self.label_prefix}_"
        return {
            int(label.removeprefix(prefix)) for label in self._map.values()
        }

    def _next_unused(self) -> str:
        used = self._used_numbers()
        if self._reuse_gaps:
            number = 1
            while number in used:
                number += 1
        else:
            number = max(used, default=0) + 1
        return f"{self.label_prefix}_{number:02d}"

    def ensure_all(self, identifiers: Iterable[str]) -> None:
        """Assign missing identifiers deterministically in normalized order."""
        normalized = {self._normalize_key(value) for value in identifiers}
        for identifier in sorted(normalized):
            if identifier not in self._map:
                self._map[identifier] = self._next_unused()

    def stable_id(self, identifier: str) -> str:
        normalized = self._normalize_key(identifier)
        if normalized not in self._map:
            self.ensure_all([normalized])
        return self._map[normalized]

    def display(self, identifier: str) -> str:
        normalized = self._normalize_key(identifier)
        stable = self.stable_id(normalized)
        return self._display_names.get(normalized, stable)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps(self._map, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)


# --------------- Privacy guard ------------------------------------


def is_private_safe_path(path: Path) -> bool:
    """Marker-based private-path check.

    Returns True iff any component of the resolved absolute path is
    named ``ai-prose-baselines-private``. Mirrors
    ``voice_profile.is_private_output_path`` and
    ``voice_drift_tracker._check_output_privacy``. Both repo-internal
    and sibling private roots are accepted; the documented standard
    layout uses a sibling directory.
    """
    return PRIVATE_DIR_NAME in path.expanduser().resolve().parts


def check_output_privacy(
    paths: Iterable[Path], *, allow_public: bool, tool: str,
) -> None:
    """Enforce the marker-based private-path rule across output paths.

    Acquisition tools call this once after computing every path they
    plan to write (output dir, manifest path, summary report). When
    ``allow_public`` is False and any path is outside a private root,
    the tool prints a refusal explaining both options (write into a
    private root, or pass ``--allow-public-output`` for non-personal
    corpora) and exits with code 2.
    """
    if allow_public:
        return
    for p in paths:
        if p is None:
            continue
        if not is_private_safe_path(Path(p)):
            sys.stderr.write(
                f"Refusing to write {p}: not under any directory "
                f"named '{PRIVATE_DIR_NAME}'. {tool} output is voice-"
                f"cloning input. Either write into a directory named "
                f"'{PRIVATE_DIR_NAME}' (repo-internal or sibling — "
                f"both are accepted), or pass --allow-public-output "
                f"for non-personal corpora.\n"
            )
            sys.exit(2)


# --------------- Output-path conventions --------------------------


def resolve_baselines_dir(env_var: str = DEFAULT_BASELINES_ENV) -> Path:
    """Return the configured baselines root.

    Order of resolution:
      1. ``$SETEC_BASELINES_DIR`` if set.
      2. Sibling ``ai-prose-baselines-private`` next to the repo, if
         it exists.
      3. ``DEFAULT_BASELINES_FALLBACK``.

    Acquisition output goes under ``<baselines>/impostors/<register>/
    <author_slug>/`` by default; the per-script ``--output-dir`` flag
    overrides.
    """
    import os
    env_val = os.environ.get(env_var)
    if env_val:
        return Path(env_val).expanduser()
    # After 1.16.0, this file lives at
    # ``<repo>/plugins/setec-voiceprint/scripts/acquisition_core.py``.
    # parents[3] is the repo root in dev (and the marketplace root
    # in install); the sibling lives next to it.
    repo_root = Path(__file__).resolve().parents[3]
    sibling = repo_root.parent / PRIVATE_DIR_NAME
    if sibling.exists():
        return sibling
    return DEFAULT_BASELINES_FALLBACK


def default_output_dir(
    register: str, author_slug: str, *, base: Path | None = None,
) -> Path:
    """Default output directory: ``<base>/impostors/<register>/<slug>/``."""
    base = base or resolve_baselines_dir()
    return base / "impostors" / register / author_slug


# --------------- Fetcher protocol ---------------------------------


@dataclass
class FetchResult:
    """Outcome of a single fetch.

    ``status`` mirrors HTTP status codes (200 OK, 404 Not Found, 0 for
    network errors). ``text`` carries the response body decoded as
    UTF-8 with replacement; ``content_type`` is the response's
    ``Content-Type`` header verbatim or ``""`` when unavailable.
    """
    url: str
    status: int
    text: str
    content_type: str = ""
    final_url: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Fetcher:
    """Abstract HTTP fetcher.

    Subclasses implement ``_do_fetch(url)``; the base class layers on
    rate limiting, robots.txt enforcement, and book-keeping. Tests use
    `FixtureFetcher`; production scripts use `RequestsFetcher`.

    Why an abstraction: the spec requires fixture-backed CI tests
    that don't depend on network access. Without a swappable fetcher,
    tests would have to monkey-patch ``requests.get`` and ``feedparser
    .parse`` in ways that are brittle across Python versions and
    library updates. With this abstraction, each acquisition script
    accepts an optional ``fetcher`` argument that defaults to
    `RequestsFetcher` and is overridden in tests.
    """

    def __init__(
        self,
        *,
        rate_limit_seconds: float = 2.0,
        user_agent: str = "",
        respect_robots: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.rate_limit_seconds = max(0.0, rate_limit_seconds)
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.respect_robots = respect_robots
        # Extra request headers (e.g. ``Authorization: Token …``) for
        # APIs that require header auth. Kept on the fetcher (not in the
        # URL) so a credential never lands in a stored source_url.
        # Production (`RequestsFetcher`) merges these into each GET;
        # `FixtureFetcher` ignores them (it maps by URL).
        self.extra_headers = dict(extra_headers or {})
        self._last_fetch_per_host: dict[str, float] = {}
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.fetch_count = 0

    def fetch(self, url: str) -> FetchResult:
        """Fetch a URL with rate limiting and robots.txt enforcement."""
        if self.respect_robots and not self._robots_allows(url):
            return FetchResult(
                url=url, status=403, text="",
                content_type="text/plain",
                final_url=url,
            )
        self._wait_for_rate_limit(url)
        result = self._do_fetch(url)
        self._record_fetch(url)
        self.fetch_count += 1
        return result

    def _do_fetch(self, url: str) -> FetchResult:  # pragma: no cover
        raise NotImplementedError

    def fetch_bytes(self, url: str) -> bytes | None:
        """Fetch raw bytes (for binary payloads like PDFs).

        Same rate-limit + robots enforcement as ``fetch``; returns the
        response body as bytes, or ``None`` on a robots block / network
        error / non-2xx. Used by the PDF acquisition path
        (``pdf_text_from_bytes``).
        """
        if self.respect_robots and not self._robots_allows(url):
            return None
        self._wait_for_rate_limit(url)
        data = self._do_fetch_bytes(url)
        self._record_fetch(url)
        self.fetch_count += 1
        return data

    def _do_fetch_bytes(self, url: str) -> bytes | None:  # pragma: no cover
        raise NotImplementedError

    def _wait_for_rate_limit(self, url: str) -> None:
        host = urllib.parse.urlparse(url).netloc
        last = self._last_fetch_per_host.get(host)
        if last is None or self.rate_limit_seconds <= 0:
            return
        elapsed = time.monotonic() - last
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)

    def _record_fetch(self, url: str) -> None:
        host = urllib.parse.urlparse(url).netloc
        self._last_fetch_per_host[host] = time.monotonic()

    def _robots_allows(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._robots_cache.get(host_key)
        if rp is None and host_key not in self._robots_cache:
            rp = self._load_robots(host_key)
            self._robots_cache[host_key] = rp
        if rp is None:
            # No robots.txt or fetch failed → allow (fail-open is the
            # robots.txt convention; a missing robots.txt is not a
            # restriction).
            return True
        # `urllib.robotparser.can_fetch` already implements the
        # user-agent matching algorithm: if the named UA matches a
        # specific block, those rules apply; if not, it falls back
        # to the ``*`` block. The previous implementation OR-ed an
        # explicit ``*`` check on top, which let a site's
        # specific-disallow rule for our UA be overridden by an open
        # ``*`` block (we'd proceed even though the site asked us
        # specifically to stay out). One call is the correct
        # behavior — and it honors a UA-specific opt-out.
        return rp.can_fetch(self.user_agent, url)

    def _load_robots(
        self, host_key: str,
    ) -> urllib.robotparser.RobotFileParser | None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{host_key}/robots.txt")
        result = self._do_fetch(f"{host_key}/robots.txt")
        if not result.ok or not result.text:
            return None
        # urllib.robotparser parses from a list of lines.
        rp.parse(result.text.splitlines())
        return rp


class FixtureFetcher(Fetcher):
    """Test fetcher backed by a URL→fixture mapping.

    Tests construct one of these with a dict mapping URLs to fixture
    file paths (or directly to FetchResult objects), then pass it to
    the acquisition script's main routine. Unmapped URLs return a 404,
    which lets tests assert that the script handles missing pages
    gracefully without raising.
    """

    def __init__(
        self,
        url_map: dict[str, str | FetchResult | Path],
        *,
        fixture_dir: Path | None = None,
        rate_limit_seconds: float = 0.0,
        respect_robots: bool = False,
    ) -> None:
        super().__init__(
            rate_limit_seconds=rate_limit_seconds,
            respect_robots=respect_robots,
        )
        self.url_map = url_map
        self.fixture_dir = fixture_dir
        self.fetched_urls: list[str] = []

    def _do_fetch(self, url: str) -> FetchResult:
        self.fetched_urls.append(url)
        target = self.url_map.get(url)
        if target is None:
            return FetchResult(url=url, status=404, text="", final_url=url)
        if isinstance(target, FetchResult):
            return target
        # Path or string path to a fixture file.
        path = Path(target)
        if self.fixture_dir is not None and not path.is_absolute():
            path = self.fixture_dir / path
        if not path.is_file():
            return FetchResult(url=url, status=404, text="", final_url=url)
        text = path.read_text(encoding="utf-8")
        # Infer content-type from extension.
        ext = path.suffix.lower()
        content_type = {
            ".xml": "application/xml",
            ".rss": "application/rss+xml",
            ".atom": "application/atom+xml",
            ".html": "text/html",
            ".htm": "text/html",
            ".txt": "text/plain",
            ".json": "application/json",
        }.get(ext, "application/octet-stream")
        return FetchResult(
            url=url, status=200, text=text,
            content_type=content_type, final_url=url,
        )

    def _do_fetch_bytes(self, url: str) -> bytes | None:
        self.fetched_urls.append(url)
        target = self.url_map.get(url)
        if target is None:
            return None
        if isinstance(target, FetchResult):
            return target.text.encode("utf-8")
        path = Path(target)
        if self.fixture_dir is not None and not path.is_absolute():
            path = self.fixture_dir / path
        if not path.is_file():
            return None
        return path.read_bytes()


def make_requests_fetcher(
    *,
    version: str = "0.0.0",
    rate_limit_seconds: float = 2.0,
    timeout: float = 30.0,
    user_agent: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Fetcher:
    """Construct a production fetcher backed by the `requests` library.

    Imported lazily so scripts and tests that don't actually fetch
    over the network can run without `requests` installed.

    ``user_agent`` overrides the default SETEC-identifying string;
    pass ``None`` (the default) to use ``DEFAULT_USER_AGENT.format(
    version=version)``. The chosen value is what the fetcher
    advertises both on outgoing HTTP requests AND when consulting
    robots.txt — both checks must agree, so the user-agent threading
    is end-to-end.

    ``extra_headers`` are merged into every GET (e.g. ``{"Authorization":
    "Token <key>"}`` for header-auth APIs like CourtListener). Keeping the
    credential in a header — never in the URL — means it cannot leak into a
    stored ``source_url``.
    """
    try:
        import requests  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "requests is not installed. Install acquisition dependencies "
            "with: pip install -r requirements-acquisition.txt"
        ) from e

    if not user_agent:
        user_agent = DEFAULT_USER_AGENT.format(version=version)

    class RequestsFetcher(Fetcher):
        def _do_fetch(self, url: str) -> FetchResult:
            try:
                resp = requests.get(
                    url,
                    headers={"User-Agent": self.user_agent, **self.extra_headers},
                    timeout=timeout,
                    allow_redirects=True,
                )
            except Exception as exc:
                sys.stderr.write(f"  network error: {url}: {exc}\n")
                return FetchResult(
                    url=url, status=0, text="", final_url=url,
                )
            try:
                # Force UTF-8 with replacement to avoid surprises on
                # latin-1 default decoding.
                if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"
                text = resp.text
            except UnicodeDecodeError:
                text = resp.content.decode("utf-8", errors="replace")
            return FetchResult(
                url=url,
                status=resp.status_code,
                text=text,
                content_type=resp.headers.get("Content-Type", ""),
                final_url=resp.url,
            )

        def _do_fetch_bytes(self, url: str) -> bytes | None:
            try:
                resp = requests.get(
                    url,
                    headers={"User-Agent": self.user_agent, **self.extra_headers},
                    timeout=timeout,
                    allow_redirects=True,
                )
            except Exception as exc:
                sys.stderr.write(f"  network error: {url}: {exc}\n")
                return None
            if not (200 <= resp.status_code < 300):
                return None
            return resp.content

    return RequestsFetcher(
        rate_limit_seconds=rate_limit_seconds,
        user_agent=user_agent,
        respect_robots=True,
        extra_headers=extra_headers,
    )


# --------------- Preprocessing pipe-through -----------------------


def preprocess_text(
    text: str,
    *,
    rules: str | Iterable[str] | None = None,
    allow_non_prose: bool = False,
    strip_aggressive: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Run text through ``scripts/preprocessing.py`` corpus-hygiene gate.

    Acquisition scripts pipe extracted text through this so impostor
    entries are subject to the same content-level guards as identity
    baselines. Returns ``(cleaned_text, metadata)``; metadata is the
    same shape ``preprocessing.strip_non_prose`` returns.
    """
    # Imported lazily so that test code paths that don't exercise
    # preprocessing don't depend on this module's full surface.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import preprocessing  # type: ignore
    finally:
        sys.path.pop(0)
    return preprocessing.strip_non_prose(
        text,
        rules=rules,
        allow_non_prose=allow_non_prose,
        strip_aggressive=strip_aggressive,
    )


# --------------- Per-piece dataclass ------------------------------


@dataclass
class AcquiredPiece:
    """One acquired text artifact, ready for write + manifest emission.

    Holds the cleaned text and all the metadata the manifest entry
    will carry. Acquisition scripts construct one of these per fetched
    piece and pass it to ``write_piece`` for atomic on-disk emission.
    """
    title: str
    author: str
    persona: str
    register: str
    date_written: _dt.date | None
    source_url: str
    cleaned_text: str
    raw_byte_length: int
    preprocessing_meta: dict[str, Any]
    acquired_via: str
    consent_status: str
    era: str
    register_match: str = "high"
    topic_match: str = "medium"
    impostor_for: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def content_hash(self) -> str:
        return compute_content_hash(self.cleaned_text)

    @property
    def word_count(self) -> int:
        return len(re.findall(r"\S+", self.cleaned_text))

    def filename_stem(self) -> str:
        if self.date_written:
            date_part = self.date_written.isoformat()
        else:
            date_part = "undated"
        return f"{date_part}_{slugify(self.title)}"


# --------------- Run summary --------------------------------------


@dataclass
class RunSummary:
    """Aggregate statistics for an acquisition run.

    Acquisition scripts mutate one of these as they go and print it on
    stderr at the end. The shape is stable across acquisition modes so
    downstream tooling (a future cross-script dashboard) can parse the
    same JSON.
    """
    acquired: int = 0
    skipped_paid: int = 0
    skipped_duplicate: int = 0
    skipped_parse_error: int = 0
    skipped_network_error: int = 0
    skipped_filtered: int = 0
    skipped_robots: int = 0
    total_cleaned_words: int = 0
    per_rule_strips: dict[str, int] = field(default_factory=dict)
    skip_log: list[dict[str, str]] = field(default_factory=list)
    draft_manifest_path: Optional[str] = None
    output_dir: Optional[str] = None

    def record_strip_meta(self, meta: dict[str, Any]) -> None:
        for rule, count in (meta.get("tokens_stripped_by_rule") or {}).items():
            self.per_rule_strips[rule] = (
                self.per_rule_strips.get(rule, 0) + int(count)
            )

    def log_skip(self, *, reason: str, url: str, detail: str = "") -> None:
        self.skip_log.append({"reason": reason, "url": url, "detail": detail})

    def render_stderr(self) -> str:
        lines = [
            f"Acquired: {self.acquired} files",
            f"Skipped (paid-only): {self.skipped_paid}",
            f"Skipped (duplicate hash): {self.skipped_duplicate}",
            f"Skipped (parse error): {self.skipped_parse_error}",
            f"Skipped (network error): {self.skipped_network_error}",
            f"Skipped (filter): {self.skipped_filtered}",
            f"Skipped (robots): {self.skipped_robots}",
            f"Total cleaned words: {self.total_cleaned_words:,}",
        ]
        if self.draft_manifest_path:
            lines.append(f"Draft manifest written to: {self.draft_manifest_path}")
        if self.per_rule_strips:
            strip_str = ", ".join(
                f"{k}={v}" for k, v in sorted(self.per_rule_strips.items())
            )
            lines.append(f"Per-rule preprocessing strips: {strip_str}")
        return "\n".join(lines) + "\n"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# --------------- Write + manifest emission ------------------------


def _unique_stem(piece: AcquiredPiece, output_dir: Path) -> str:
    """Return a collision-free filename stem for ``piece`` in ``output_dir``.

    Content-identical pieces are filtered upstream (via
    ``content_hash_already_present``), so an existing ``<stem>.txt`` here means a
    *different* piece already claimed that stem — e.g. a long title truncated by
    ``slugify`` past a chapter suffix, or two untitled pieces. Disambiguate
    deterministically with a short content-hash suffix rather than silently
    overwrite the earlier file.
    """
    base = piece.filename_stem()
    if not (output_dir / f"{base}.txt").exists():
        return base
    # content_hash is "sha256:<hexdigest>"; take 8 hex chars (no ':' — keep the
    # stem filesystem-safe, incl. Windows).
    suffix = piece.content_hash.split(":")[-1][:8]
    candidate = f"{base}-{suffix}"
    n = 2
    while (output_dir / f"{candidate}.txt").exists():
        candidate = f"{base}-{suffix}-{n}"
        n += 1
    return candidate


def write_piece(
    piece: AcquiredPiece,
    *,
    output_dir: Path,
    scraper_version: str,
) -> tuple[Path, Path]:
    """Write one acquired piece to disk.

    Produces:
      - ``<output_dir>/<YYYY-MM-DD>_<title-slug>.txt``  (cleaned text)
      - ``<output_dir>/<YYYY-MM-DD>_<title-slug>.meta.json`` (sidecar)

    Returns the (text_path, meta_path) tuple. Content-level dedup is the
    caller's job (``content_hash_already_present``); this function additionally
    guards against *stem* collisions — two different-content pieces whose
    ``filename_stem()`` slugs coincide — by appending a short content-hash
    suffix, so the second piece never silently clobbers the first.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = _unique_stem(piece, output_dir)
    text_path = output_dir / f"{stem}.txt"
    meta_path = output_dir / f"{stem}.meta.json"
    # The content hash is over the UTF-8 bytes of ``cleaned_text``.  Write those
    # exact bytes so Windows universal-newline translation cannot change LF to
    # CRLF (or pre-existing CRLF to CR-CR-LF) after the hash was computed.
    text_path.write_bytes(piece.cleaned_text.encode("utf-8"))
    meta = {
        "source_url": piece.source_url,
        "title": piece.title,
        "author": piece.author,
        "date_written": piece.date_written.isoformat() if piece.date_written else None,
        "raw_byte_length": piece.raw_byte_length,
        "content_hash": piece.content_hash,
        "word_count": piece.word_count,
        "acquired_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "scraper": piece.acquired_via,
        "scraper_version": scraper_version,
        "preprocessing": piece.preprocessing_meta,
    }
    meta_path.write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return text_path, meta_path


def content_hash_already_present(
    content_hash: str, output_dir: Path,
) -> Path | None:
    """Scan ``output_dir`` for an existing ``.meta.json`` whose paired
    ``.txt`` *currently* hashes to ``content_hash``. Returns the
    matching meta path, or ``None`` if not found.

    The sidecar's recorded ``content_hash`` is a hint, not proof:
    nothing re-verifies it against the paired ``.txt`` after
    acquisition (``manifest_validator`` checks that the field is
    *present*, never that its *value* still matches the bytes on disk).
    So a ``.txt`` edited in place — without touching its
    ``.meta.json`` — leaves a stale recorded hash. Trusting it would
    let this dedup gate drop a genuinely-new piece as "already present"
    even though the corpus no longer holds those bytes. Guard against
    that by recomputing the hash from the paired ``.txt``'s current
    bytes before honoring a match (recompute, don't trust). The sole
    compatibility exception is old Windows text-mode output whose exact
    CRLF bytes normalize to the logical-LF hash recorded by its sidecar.

    v1 dedupes within the target output directory only. Manifest-wide
    dedupe is a follow-up — for now, two impostor pools targeting the
    same author should share the same output directory.
    """
    if not output_dir.exists():
        return None
    for meta_file in output_dir.glob("*.meta.json"):
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("content_hash") != content_hash:
            continue
        # The recorded hash matches the incoming piece — but re-derive
        # it from the paired .txt's actual current bytes before
        # treating this as a duplicate.
        txt_file = meta_file.parent / (
            meta_file.name[: -len(".meta.json")] + ".txt"
        )
        try:
            stored_bytes = txt_file.read_bytes()
            stored_text = stored_bytes.decode("utf-8")
            actual_hash = compute_content_hash(stored_text)
        except (OSError, UnicodeDecodeError):
            # Paired .txt missing/unreadable: the recorded content is
            # not actually on disk. Fail open (let the caller
            # re-acquire) rather than silently drop the incoming piece.
            continue
        if actual_hash == content_hash:
            return meta_file
        # Compatibility for artifacts written by the old Windows text-mode
        # writer: metadata hashed the logical LF text, then ``write_text``
        # translated LF to CRLF on disk.  The former ``read_text`` dedupe path
        # reversed that translation through universal-newline handling.  Keep
        # recognizing only that narrow legacy representation; new writes are
        # still verified against their exact UTF-8 bytes above.  Lone CR is not
        # a Windows newline translation and therefore fails open for reacquire.
        newline_remainder = stored_bytes.replace(b"\r\n", b"")
        if (
            b"\r\n" in stored_bytes
            and b"\r" not in newline_remainder
            and b"\n" not in newline_remainder
        ):
            legacy_lf_text = stored_bytes.replace(b"\r\n", b"\n").decode(
                "utf-8"
            )
            if compute_content_hash(legacy_lf_text) == content_hash:
                return meta_file
        # Stale/edited sidecar: the .txt's real bytes no longer hash to
        # the recorded value, so this on-disk doc does NOT hold the
        # incoming content. Not a duplicate — keep scanning.
    return None


def compose_manifest_entry(
    piece: AcquiredPiece,
    *,
    text_path: Path,
    manifest_relative_to: Path,
    use: list[str] | None = None,
    privacy: str = "private",
    split: str = "baseline",
    corpus_role: str | None = "impostor",
    ai_status: str = "pre_ai_human",
    language_status: str = "native",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compose one draft manifest entry from an `AcquiredPiece`.

    The entry conforms to ``references/manifest-schema.md`` for
    impostor-corpus entries:
      - ``corpus_role: "impostor"``
      - ``use: ["voice_impostor"]``
      - ``split: "baseline"`` (reference pool, not identity baseline)
      - ``privacy: "private"``
      - all five impostor required fields present
      - ``content_hash`` populated

    For a non-impostor (test/drift) bucket, pass ``corpus_role=None``
    alongside ``use``/``split``/``ai_status`` overrides: the
    ``corpus_role`` field is then omitted entirely and the five
    impostor-only fields are not emitted, matching the hand-curated
    drift entries (e.g. ``wordpress_archive_post2022_uncertain/``).

    ``manifest_relative_to`` is the directory the manifest will live
    in; the entry's ``path`` is computed relative to that directory
    when possible (and absolute otherwise).
    """
    use = use or ["voice_impostor"]
    rel_path: str
    try:
        rel_path = str(text_path.resolve().relative_to(
            manifest_relative_to.resolve()
        ))
    except ValueError:
        rel_path = str(text_path.resolve())
    entry: dict[str, Any] = {
        "id": text_path.stem,
        "path": rel_path,
        "author": piece.author,
        "persona": piece.persona,
        "register": piece.register,
        "date_written": piece.date_written.isoformat() if piece.date_written else None,
        "ai_status": ai_status,
        "language_status": language_status,
        "word_count": piece.word_count,
        "use": list(use),
        "split": split,
        "privacy": privacy,
        "content_hash": piece.content_hash,
        "source": piece.source_url,
    }
    # Drop None values so the validator's "unknown enum" warnings
    # don't fire for date_written: null on undated entries.
    if entry["date_written"] is None:
        del entry["date_written"]
    # corpus_role is optional in the schema; emit it only when set so a
    # test/drift entry (corpus_role=None) carries no role field at all.
    if corpus_role is not None:
        entry["corpus_role"] = corpus_role
    # Impostor-required fields. Always emit for impostor entries; the
    # validator errors on missing ones.
    if corpus_role == "impostor":
        entry["impostor_for"] = list(piece.impostor_for)
        entry["register_match"] = piece.register_match
        entry["topic_match"] = piece.topic_match
        entry["consent_status"] = piece.consent_status
        entry["era"] = piece.era
        entry["acquired_via"] = piece.acquired_via
    if piece.notes:
        entry["notes"] = piece.notes
    if extra:
        entry.update(extra)
    return entry


def append_manifest_entry(
    manifest_path: Path, entry: dict[str, Any],
) -> None:
    """Append one JSON entry as a JSONL line to ``manifest_path``.

    Creates the file (and parent directories) if it doesn't exist.
    Each entry is on its own line, newline-terminated, sorted-key
    serialized for stable diffs.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, sort_keys=True, ensure_ascii=False)
    with manifest_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# --------------- HTML extraction helpers --------------------------


def html_to_text(
    html: str,
    *,
    content_selector: str | None = None,
    strip_selectors: Iterable[str] = (),
) -> tuple[str, str | None]:
    """Extract plain text from an HTML document.

    Pipeline:
      1. Parse with BeautifulSoup (lxml backend if available).
      2. Drop noise elements globally: ``<script>``, ``<style>``,
         ``<noscript>``, ``<svg>``, ``<form>``, ``<nav>``, ``<aside>``,
         ``<footer>``, anything in ``strip_selectors``.
      3. If ``content_selector`` is set and matches, restrict to that
         subtree; else use the document body.
      4. Convert to text with ``.get_text(separator='\\n')`` and
         normalize whitespace.

    Returns ``(text, title)`` where ``title`` is the HTML ``<title>``
    if present (used as a fallback when site-specific title selectors
    don't match).
    """
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "beautifulsoup4 is not installed. Install acquisition "
            "dependencies with: "
            "pip install -r requirements-acquisition.txt"
        ) from e

    # Try lxml first; fall back to the stdlib parser if lxml isn't
    # installed.
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Globally drop noise.
    for tag_name in (
        "script", "style", "noscript", "svg", "form", "nav",
        "aside", "footer", "iframe",
    ):
        for tag in soup.find_all(tag_name):
            tag.decompose()
    for sel in strip_selectors:
        for tag in soup.select(sel):
            tag.decompose()

    # Restrict to content_selector if it matches.
    container = None
    if content_selector:
        try:
            container = soup.select_one(content_selector)
        except Exception:
            container = None
    if container is None:
        container = soup.body or soup

    text = container.get_text(separator="\n")
    # Collapse runs of whitespace; preserve paragraph breaks.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), title


def _prestrip_html(html: str, strip_selectors: Iterable[str]) -> str:
    """Drop caller-named noise subtrees from ``html`` before extraction.

    trafilatura's readability heuristics are strong on generic boilerplate
    (nav / footer / global chrome) but can miss *site-specific* apparatus a
    maintainer has already pinned in ``strip_selectors`` — e.g. a Substack
    ``.comments`` / ``.subscription-widget`` block that sits inside the main
    ``<article>`` and reads as body text. Pre-stripping those exact subtrees
    hands trafilatura an already-de-chromed document, so the primary path
    keeps the site-specific cleanliness the selector-based fallback had.

    Best-effort: if bs4 is unavailable or parsing fails, return ``html``
    unchanged (trafilatura still runs on the raw document).
    """
    if not strip_selectors:
        return html
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return html
    try:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        for sel in strip_selectors:
            for tag in soup.select(sel):
                tag.decompose()
        return str(soup)
    except Exception:
        return html


def _trafilatura_extract(
    html: str,
    *,
    favor_recall: bool = True,
    strip_selectors: Iterable[str] = (),
) -> tuple[str, str | None] | None:
    """Extract main-content text + title with trafilatura, or ``None``.

    Returns ``(text, title)`` when trafilatura is installed AND returns a
    non-empty main-content body; returns ``None`` in every other case
    (trafilatura absent, extraction found nothing, or it raised). The
    ``None`` sentinel is what tells :func:`extract_main_content` to fall
    back to the BeautifulSoup path — trafilatura is an *upgrade*, never a
    hard dependency, so a miss must degrade silently rather than raise.

    trafilatura's model-free readability heuristics (boilerplate removal,
    comment/nav/footer stripping, main-article detection) replace the
    hand-maintained ``strip_selectors`` + ``content_selector`` guesswork
    for the common case; any ``strip_selectors`` the caller does pass are
    pre-stripped (:func:`_prestrip_html`) so site-specific apparatus inside
    the main article is dropped before trafilatura sees it.

    Imported lazily so base ``import acquisition_core`` stays pure — no
    acquisition run that doesn't extract HTML pays the import cost, and
    the module imports cleanly with trafilatura absent.
    """
    try:
        import trafilatura  # type: ignore
        from trafilatura.metadata import extract_metadata  # type: ignore
    except Exception:
        return None
    prepared = _prestrip_html(html, strip_selectors)
    try:
        text = trafilatura.extract(
            prepared,
            favor_recall=favor_recall,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            output_format="txt",
        )
    except Exception:
        return None
    if not text or not text.strip():
        return None
    title: str | None = None
    try:
        meta = extract_metadata(html)
        if meta is not None and getattr(meta, "title", None):
            title = str(meta.title).strip() or None
    except Exception:
        title = None
    # Normalize whitespace to match the html_to_text contract (collapse
    # intra-line runs, cap blank-line runs at one) so downstream
    # preprocessing + hashing see the same shape from either path.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(), title


def extract_main_content(
    html: str,
    *,
    content_selector: str | None = None,
    strip_selectors: Iterable[str] = (),
    prefer_trafilatura: bool = True,
) -> tuple[str, str | None]:
    """Primary HTML→text extraction: trafilatura first, BeautifulSoup fallback.

    Returns ``(text, title)`` — the same contract as :func:`html_to_text`,
    so acquisition scripts can call this in place of ``html_to_text``
    without changing how they consume the result.

    Path:
      1. **trafilatura** (when installed and ``prefer_trafilatura``): its
         readability heuristics strip boilerplate/nav/comments and isolate
         the main article body. This is the "single biggest acquisition
         upgrade" — it replaces per-site ``content_selector`` /
         ``strip_selectors`` tuning for the common case.
      2. **BeautifulSoup fallback** (:func:`html_to_text`): used when
         trafilatura is absent, is disabled, or returns nothing. The
         caller's ``content_selector`` + ``strip_selectors`` still drive
         this path, so site-specific extraction is never lost.

    Fail-soft is the whole point: a trafilatura miss (empty body on an
    unusual page, or the package simply not installed) transparently
    degrades to the existing extractor rather than dropping the piece.
    """
    if prefer_trafilatura:
        primary = _trafilatura_extract(html, strip_selectors=strip_selectors)
        if primary is not None:
            text, title = primary
            if text:
                # If trafilatura found no title, let the BeautifulSoup
                # <title> sniff fill it (cheap, and some feeds rely on it).
                if title is None:
                    try:
                        _, bs_title = html_to_text(
                            html,
                            content_selector=content_selector,
                            strip_selectors=strip_selectors,
                        )
                        title = bs_title
                    except Exception:
                        title = None
                return text, title
    return html_to_text(
        html,
        content_selector=content_selector,
        strip_selectors=strip_selectors,
    )


def html_text_is_clean(text: str) -> bool:
    """Sanity check: the cleaned text must not still contain raw HTML.

    Used in tests to assert that extraction is working. False if the
    text contains anything that looks like an HTML tag, a script
    block, or stray sidebar boilerplate.
    """
    if "<script" in text.lower() or "<style" in text.lower():
        return False
    # A bare ``<word>`` or ``</word>`` pattern surviving extraction
    # means the parser didn't drop the tag. Allow ``< ``, ``< 5``,
    # ``<="..."`` etc. — those are real prose.
    if re.search(r"</?[a-zA-Z][a-zA-Z0-9]*[\s/>]", text):
        return False
    return True


# --------------- PDF extraction -----------------------------------


def pdf_text_from_bytes(data: bytes) -> str:
    """Extract text from PDF bytes via the existing ``pdf_extract`` text-layer
    extractor (pypdf).

    ``pdf_extract.extract_text_layer`` takes a path, so the bytes are written
    to a temp file. Returns the extracted text, or ``""`` on empty / image-only
    / unparseable input (the caller treats ``""`` as a skip). OCR is out of
    scope here — image-only PDFs return ``""``; the operator runs the dedicated
    ``pdf_extract.py`` OCR pass for those.

    The PDF acquisition path (``acquire_pdf_urls.py``) calls this on bytes
    fetched via ``Fetcher.fetch_bytes``.
    """
    if not data:
        return ""
    import os
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import pdf_extract  # type: ignore
    finally:
        sys.path.pop(0)
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
            tf.write(data)
            tmp_path = tf.name
        return pdf_extract.extract_text_layer(Path(tmp_path)) or ""
    except Exception:
        return ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# --------------- Shared CLI argument helpers ----------------------
#
# Only flags whose definition (name, action, default, choices, AND
# help text) was already byte-identical across every `acquire_*.py`
# that declares them are factored here. Adding these via a helper
# produces argparse arguments indistinguishable from the inline
# `p.add_argument(...)` calls they replace — same `--help` text, same
# defaults, same parse behavior — so the CLI surface is unchanged.
#
# Flags that *look* shared but diverge in help text or defaults
# across scripts (e.g. `--persona`, `--register`, `--min-words`,
# `--max-items`, `--out`, `--dry-run`) are intentionally left inline:
# a single canonical definition would silently rewrite their `--help`
# output or change per-source defaults. See issue #198 item 3.


def add_user_agent_arg(parser: argparse.ArgumentParser) -> None:
    """Register the shared ``--user-agent`` flag.

    Byte-identical across the 8 network-bound acquirers that expose it;
    extracting it keeps the override consistent without changing the
    flag's name, behavior, or help text.
    """
    parser.add_argument("--user-agent",
                        help="Override the User-Agent header.")


def add_allow_empty_arg(parser: argparse.ArgumentParser) -> None:
    """Register the shared ``--allow-empty`` flag.

    Byte-identical across the acquirers that expose it (the 3-line help
    block was hand-duplicated verbatim). Same ``store_true`` action and
    same help text as the inline definitions it replaces.
    """
    parser.add_argument("--allow-empty", action="store_true",
                        help="Exit 0 even when nothing is acquired. By default a "
                             "zero-output run that isn't a dedupe-only rerun "
                             "(nothing matched the source/filters) fails.")
