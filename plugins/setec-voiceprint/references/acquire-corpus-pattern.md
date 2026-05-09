# Acquire-corpus pattern

A reference for adapting SETEC's impostor-pool acquisition pipeline to a new source. The five acquisition scripts that ship with the framework (`acquire_blog.py`, `acquire_blogger_takeout.py`, `acquire_magazine.py`, `pdf_inventory.py` + `pdf_extract.py`) all share the same pipeline shape. This doc captures the shape so an LLM helping a user add a new source — a Slack export, an Obsidian vault, a Notion dump, a custom CMS, anything — has one place to ground from.

## When to reach for this

Use this pattern (and the companion `scripts/acquire_corpus_template.py`) when:

- The user has prose they want to add to the impostor pool from a source SETEC doesn't already handle.
- The source is a directory of files, an export archive, an API response, or a feed shape we don't yet support.
- The work is one-off enough that landing a permanent acquisition script in the framework isn't justified, but the user still wants the pieces (manifest emission, privacy guard, preprocessing, content-hash dedupe) to work the same way.

If the source is something that should ship as a permanent script (a popular blog platform, a major magazine), the pattern below is the implementation guide. Land it as `scripts/acquire_<source>.py` and add tests + fixtures matching the existing scripts' shape.

## The pipeline

Every acquisition script follows the same six-step pipeline. Three steps are the same for all sources (preprocess, hash, manifest emit); three are source-specific (fetch/discover, extract, source-specific dedupe).

```
[1. fetch / discover items]   ← source-specific: list URLs, paths, items
            │
            ▼
[2. extract per item]          ← source-specific: HTML, PDF, JSON, etc. → plain text
            │
            ▼
[3. preprocess (corpus hygiene)]   ← shared: scripts/preprocessing.py
            │
            ▼
[4. content-hash dedupe]        ← shared: acquisition_core.content_hash_already_present
            │
            ▼
[5. write .txt + .meta.json]    ← shared: acquisition_core.write_piece
            │
            ▼
[6. emit manifest entry]         ← shared: acquisition_core.compose_manifest_entry
                                          + acquisition_core.append_manifest_entry
```

The shared steps are factored into `scripts/acquisition_core.py`. The source-specific steps are what a new acquisition script implements.

## What `acquisition_core.py` gives you

The helpers below cover the parts every acquisition script needs. New scripts should consume these rather than reimplement.

### Slug + hash utilities

| Helper | Purpose |
|---|---|
| `slugify(text, max_length=80)` | Filename-safe slug. Unicode → ASCII fold, lowercase, hyphenated, word-boundary trim. |
| `author_to_persona_slug(author)` | Deterministic `lastname_firstname_personal` from a display name. |
| `compute_content_hash(text)` | SHA-256 of cleaned text, prefixed `sha256:`. Manifest convention. |
| `parse_iso_date(text)` | Tolerant date parser. Returns `None` on garbage rather than raising. |

### Privacy guard

| Helper | Purpose |
|---|---|
| `is_private_safe_path(path)` | Marker-based check: any path component named `ai-prose-baselines-private` qualifies. |
| `check_output_privacy(paths, allow_public, tool)` | Refuses non-private paths unless `allow_public=True`. Used at the top of `run()`. |
| `resolve_baselines_dir()` | Resolves the configured baselines root through `$SETEC_BASELINES_DIR` → sibling-of-repo → fallback. |
| `default_output_dir(register, author_slug)` | Default `<baselines>/impostors/<register>/<slug>/`. |

### Fetcher abstraction (for network-bound sources)

| Helper | Purpose |
|---|---|
| `Fetcher` (abstract base) | Per-host rate limit + robots.txt enforcement + UA header. |
| `FixtureFetcher` | Test mock; URL → fixture-file mapping. |
| `make_requests_fetcher(version, rate_limit_seconds, user_agent)` | Production fetcher backed by `requests`. |

For non-network sources (file system, JSON dump, archive), you don't need a fetcher — read directly from disk.

### Preprocessing pipe-through

| Helper | Purpose |
|---|---|
| `preprocess_text(text, ...)` | Calls `scripts/preprocessing.py` with the same flags every other script honors (`--allow-non-prose`, `--strip-rules`, `--strip-aggressive`). |

### HTML extraction

| Helper | Purpose |
|---|---|
| `html_to_text(html, content_selector, strip_selectors)` | BeautifulSoup with lxml backend. Drops `<script>` / `<style>` / `<nav>` / `<aside>` / `<footer>` / `<form>` / `<svg>` globally. Restricts to `content_selector` if matched. |
| `html_text_is_clean(text)` | Test predicate: no surviving HTML tags. |

### Per-piece dataclass + summary

| Helper | Purpose |
|---|---|
| `AcquiredPiece` | One acquired text artifact. Carries every field the manifest entry needs. |
| `RunSummary` | Acquisition-run aggregate. `acquired`, `skipped_paid`, `skipped_duplicate`, etc. with `render_stderr()` and `to_dict()`. |
| `write_piece(piece, output_dir, scraper_version)` | Writes `.txt` + `.meta.json` sidecar. |
| `content_hash_already_present(hash, output_dir)` | Within-output-dir dedupe scan. |
| `compose_manifest_entry(piece, text_path, manifest_relative_to)` | Impostor-schema-conforming dict. |
| `append_manifest_entry(manifest_path, entry)` | JSONL append with stable key ordering. |

## The CLI conventions every acquisition script follows

These flags are common across the five existing scripts. New scripts should keep the same names and semantics:

| Flag | Default | Purpose |
|---|---|---|
| `--persona` | derive from author / source | Persona slug for emitted entries. |
| `--impostor-for NAME [NAME ...]` | **required** | Persona slug(s) this impostor serves. Validator rejects empty. |
| `--register` | **required** | Manifest register; e.g. `blog_essay`, `literary_horror`. |
| `--register-match {high,medium,low}` | `high` | Closeness of register match. |
| `--topic-match {high,medium,low}` | `medium` | Closeness of topic overlap. |
| `--consent-status` | **required** | One of `public_record`, `cc_licensed`, `fair_use_research`, `author_consent`, `undocumented`. |
| `--era` | `pre_chatgpt` | One of `pre_chatgpt`, `pre_ai_widespread`, `post_ai_widespread`, `undated`. |
| `--since` / `--until` | none | Date-window filter (ISO `YYYY-MM-DD`). |
| `--max-items` (or `--max-stories` etc.) | varies | Cap per run. |
| `--output-dir` | `<baselines>/impostors/<register>/<slug>/` | Where `.txt` + `.meta.json` go. |
| `--emit-manifest` | `<output-dir>/draft_manifest.jsonl` | Where the draft manifest JSONL goes. |
| `--out` | none | Optional summary report (JSON). |
| `--rate-limit` | 2.0 (network only) | Seconds between same-host requests. |
| `--user-agent` | framework default | UA override; honored on both HTTP and robots checks. |
| `--dry-run` | off | Inventory what would be acquired; don't write. |
| `--allow-public-output` | off | Override the marker-based privacy guard. |
| `--allow-non-prose` / `--strip-rules` / `--strip-aggressive` | off / all rules / off | Pass-through to `preprocessing.py`. |

Argparse should make `--impostor-for`, `--register`, and `--consent-status` `required=True`. The validator will reject empty values; catching them at argparse time is cheaper than catching them after a network run.

## What the source-specific code has to implement

Three pure functions plus one wiring step. The template (`scripts/acquire_corpus_template.py`) has all four marked with `TODO(LLM)` comments.

### 1. `discover_items(source, options)` → `Iterable[ItemMeta]`

Lists every item in the source. Returns iterable of dicts (or a dataclass) with at minimum `{url_or_path, title, date_or_none}`. Date-window filtering happens here when the source provides dates cheaply (sitemap lastmod, file mtime, JSON timestamps); otherwise it happens after extraction.

For network sources: use the `Fetcher` abstraction to drive discovery (fetch the index page, parse it, yield item URLs).

For local sources: walk the directory, glob files, parse archive index files.

### 2. `extract_one(item, source, options)` → `(body_text, title, author, date)`

Given one item, returns its plain-text body and best-effort metadata. The body MUST be plain text — HTML / Markdown / JSON gets converted here, not later.

Common patterns:
- HTML: `acquisition_core.html_to_text(html, content_selector=...)`
- PDF: `pypdf.PdfReader(...).pages[i].extract_text()` (text layer) or `ocrmypdf` (image)
- JSON: pull the `body` field, possibly markdown-strip it
- Atom feed entry: pull `content[0].value`, run through `html_to_text`

Return `None` for fields the source doesn't provide; the caller handles fallbacks.

### 3. `parse_options(args)` → `ProcessOptions`

Build the per-source ProcessOptions dataclass from CLI args. The shape is roughly:

```python
@dataclass
class ProcessOptions:
    persona: str
    impostor_for: list[str]
    register: str
    register_match: str
    topic_match: str
    consent_status: str
    era: str
    since: datetime.date | None
    until: datetime.date | None
    output_dir: Path
    manifest_path: Path
    max_items: int
    dry_run: bool
    allow_non_prose: bool
    strip_rules: str | None
    strip_aggressive: bool
    acquired_via: str  # "acquire_<source>_<date>"
    # source-specific extras: archive path, API key, custom selectors, etc.
```

Use `acquisition_core.default_output_dir` and `acquisition_core.parse_iso_date` for the standard pieces; add source-specific fields as needed.

### 4. The `run(args)` driver

The standard shape:

```python
def run(args, fetcher=None):
    options = parse_options(args)

    # Privacy guard up front.
    paths = [options.output_dir, options.manifest_path]
    if args.out:
        paths.append(Path(args.out).expanduser())
    ac.check_output_privacy(
        paths, allow_public=args.allow_public_output, tool=TOOL_NAME,
    )

    summary = ac.RunSummary(
        draft_manifest_path=str(options.manifest_path) if not options.dry_run else None,
        output_dir=str(options.output_dir),
    )

    # Source-specific: fetch / walk / list.
    for item in discover_items(args.source, options):
        if summary.acquired >= options.max_items:
            break

        # Source-specific: extract plain text.
        body_text, title, author, date = extract_one(item, args.source, options)

        # Date-window filter (if discovery didn't already filter).
        if options.since and date and date < options.since:
            summary.skipped_filtered += 1
            continue
        if options.until and date and date > options.until:
            summary.skipped_filtered += 1
            continue

        if not body_text or len(body_text) < 200:
            summary.skipped_parse_error += 1
            continue

        # Shared: preprocess.
        cleaned, prep_meta = ac.preprocess_text(
            body_text,
            rules=options.strip_rules,
            allow_non_prose=options.allow_non_prose,
            strip_aggressive=options.strip_aggressive,
        )
        if not cleaned or len(cleaned) < 200:
            summary.skipped_parse_error += 1
            continue

        piece = ac.AcquiredPiece(
            title=title or "untitled",
            author=author or "Unknown",
            persona=options.persona,
            register=options.register,
            date_written=date,
            source_url=str(item.get("url") or item.get("path") or ""),
            cleaned_text=cleaned,
            raw_byte_length=len(body_text.encode("utf-8")),
            preprocessing_meta=prep_meta,
            acquired_via=options.acquired_via,
            consent_status=options.consent_status,
            era=options.era,
            register_match=options.register_match,
            topic_match=options.topic_match,
            impostor_for=list(options.impostor_for),
        )

        # Shared: dedupe.
        existing = ac.content_hash_already_present(
            piece.content_hash, options.output_dir,
        )
        if existing is not None:
            summary.skipped_duplicate += 1
            continue

        if options.dry_run:
            summary.acquired += 1
            continue

        # Shared: write + manifest.
        text_path, _ = ac.write_piece(
            piece, output_dir=options.output_dir,
            scraper_version=SCRIPT_VERSION,
        )
        entry = ac.compose_manifest_entry(
            piece, text_path=text_path,
            manifest_relative_to=options.manifest_path.parent,
        )
        ac.append_manifest_entry(options.manifest_path, entry)
        summary.acquired += 1
        summary.record_strip_meta(prep_meta)
        summary.total_cleaned_words += piece.word_count

    sys.stderr.write(summary.render_stderr())
    return 0 if summary.acquired > 0 else 1
```

## Testing pattern

The five existing acquisition scripts share a test architecture. New scripts should match it:

1. **Fixture corpus** under `scripts/test_data/acquire_<source>_fixture/` — synthetic content only (no real third-party prose). For network sources, HTML / XML / JSON files mapped to URLs by `FixtureFetcher`. For local sources, a tiny directory or archive committed to the repo.

2. **Unit tests** for the source-specific helpers (`discover_items`, `extract_one`). Pure functions, no I/O.

3. **End-to-end tests** that drive `run()` with the FixtureFetcher (or a tmp_path source directory) and assert:
   - The expected number of `.txt` files appear in the output directory.
   - Each `.txt` has a paired `.meta.json` sidecar with the standard fields.
   - The draft manifest carries `corpus_role: impostor`, `use: ["voice_impostor"]`, and the impostor-required field block.
   - Cleaned text has no source-format residue (no HTML tags, no PDF artifacts, no JSON braces).
   - Content hashes are unique (dedupe is wired).

4. **Privacy guard test** — `--allow-public-output=False` + a non-private output path → `SystemExit(2)`.

5. **Argparse rejection test** for missing required flags.

6. **Manifest-validator integration test** — write the draft manifest, augment it with one identity-baseline entry naming the impostor's target persona, and assert `manifest_validator.validate_manifest` reports zero errors.

The five existing scripts have 23–32 tests each; aim for similar coverage on a new acquisition script.

## Working with an LLM

If you're using an LLM (Claude, GPT-4, etc.) to help you adapt the pattern to a new source, the workflow that works:

1. **Show the LLM this doc + `scripts/acquire_corpus_template.py`**. Both files are stable and short enough to fit in one context window.

2. **Describe the source.** What is it? Where does the content live (URL? file? archive?)? What format (HTML / Markdown / JSON / PDF / something exotic)? What metadata is available per item (title, author, date)? Are there obvious skip cases (paid content, comment threads, system messages)?

3. **Ask the LLM to fill the four `TODO(LLM)` markers in the template.** The template has clear interfaces — the LLM only needs to write the source-specific functions.

4. **Run the script's `--dry-run` mode first.** Catch source-specific surprises (rate limits, malformed content, unexpected schema variations) before spending the full network or compute budget.

5. **Run the manifest validator on the emitted draft.** If it fails, the impostor-required fields aren't getting set correctly — check that `compose_manifest_entry` is wired with the impostor flag.

6. **Promote to a permanent script if the source is reusable.** Add fixtures, tests, README section, and ship as `acquire_<source>.py`.

## Examples of what fits this pattern

- **Slack export.** ZIP archive with per-channel JSON files. Each message has `text`, `user`, `ts`. Discovery: walk the channel directories. Extract: parse JSON, filter to long messages from the user. Dedupe: per-message hash within output dir. Date: parse the `ts` Unix timestamp.

- **Obsidian vault / Markdown notes.** Recursive directory of `.md` files. Discovery: glob `**/*.md`. Extract: read file, strip front-matter, run through `acquisition_core.preprocess_text`. Date: use file mtime or front-matter `date` field.

- **Email mbox export.** Single `.mbox` file or per-folder archive. Discovery: `mailbox.mbox(path)` iteration. Extract: pull message body (handle multipart, drop quotes). Filter: only outgoing messages from the user.

- **Discord export.** JSON or HTML format. Same pattern as Slack.

- **Notion export (Markdown + CSV).** Walk extracted directory; per-page Markdown handled like Obsidian; CSV indexes give titles + dates.

- **A custom CMS via API.** GET a paginated index, then GET each post by id. Use the `Fetcher` abstraction. Same as `acquire_blog.py` but the source-type detection branch is a new one.

## See also

- `scripts/acquire_corpus_template.py` — the scaffold script with `TODO(LLM)` markers.
- `scripts/acquisition_core.py` — the shared helpers documented above.
- `scripts/acquire_blog.py` — the most general live-network acquisition example. Reads as a worked instance of this pattern.
- `scripts/acquire_blogger_takeout.py` — the simplest local-source acquisition example.
- `scripts/acquire_magazine.py` — a worked example of multiple per-source modules behind a uniform CLI.
- `references/manifest-schema.md` — the manifest contract the emitted entries have to satisfy.
- `internal/2026-05-08-impostor-corpus-spec.md` — the spec the existing scripts were built from.
