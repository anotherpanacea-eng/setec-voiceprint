---
name: corpus-acquisition
description: >
  Help the user adapt SETEC's acquisition pipeline to a new source. Use
  when the user wants to add prose to the impostor pool from a source
  the framework doesn't already cover: a Slack export, an Obsidian
  vault, a Notion dump, an mbox file, a Discord export, a custom CMS,
  a JSON archive, an internal wiki, an academic-paper PDF library
  outside the standard format, etc. Also triggers on "add this to my
  corpus," "import these files into the impostor pool," "scrape my
  Substack" (when SETEC's Substack handling doesn't fit), "I want to
  use my notes as a baseline," "build a corpus from these messages,"
  or "adapt acquire_blog.py for X."
version: 1.0.0
---

# Corpus Acquisition Adaptation Skill

This skill helps the user adapt SETEC's acquisition pipeline (the pattern shared by `acquire_blog.py`, `acquire_blogger_takeout.py`, `acquire_magazine.py`, `pdf_inventory.py`, `pdf_extract.py`) to a source the framework doesn't already cover. The architecture is stable: every acquisition script does the same six-step pipeline. What changes per source is three pure functions plus the CLI surface.

## What this skill licenses, and what it does not

- **Licenses:** reading the user's description of a new source, walking them through `references/acquire-corpus-pattern.md`, copying `scripts/acquire_corpus_template.py` to a new file, filling the four `TODO(LLM)` markers based on the source's specifics, helping the user run `--dry-run` against the source, and validating the emitted manifest.
- **Does not license:** implementing acquisition for sources without considering consent and legal posture (SETEC's manifest schema requires `consent_status`; if the user can't articulate it, neither should the new script run); auto-pushing the new script into the framework as a permanent addition (one-off scripts stay in the user's local checkout); silently updating existing acquisition scripts when "adapt" was the request.

## The framework you're working with

Every acquisition script in SETEC follows this pipeline:

```
discover items → extract one → preprocess → content-hash dedupe → write .txt + .meta.json → emit manifest entry
```

The first two steps are source-specific. The last four are shared in `scripts/acquisition_core.py`. A new acquisition script is mostly: implement the first two for your source, wire CLI args through `parse_options`, and let the shared infrastructure do the rest.

The full reference: **`${CLAUDE_PLUGIN_ROOT}/references/acquire-corpus-pattern.md`**. Read it before doing anything else with the user's source — it captures all the conventions (CLI flag names, manifest field defaults, privacy posture, testing pattern) that a new script has to honor.

The starting-point template: **`${CLAUDE_PLUGIN_ROOT}/scripts/acquire_corpus_template.py`**. Copy it, replace `SOURCE_NAME` and `TOOL_NAME`, fill the four `TODO(LLM)` markers, add source-specific CLI flags. The shared pipeline (preprocessing → dedupe → write → manifest emit) is already wired.

## Workflow

### Step 1: Survey the source

Get the user to describe the source clearly. Ask:

1. **Where does the content live?**
   - URL of an index page or feed?
   - Local directory? Single archive file (zip / tar / mbox)?
   - API endpoint with auth?
   - Something else?

2. **What format is each piece in?**
   - HTML page?
   - Markdown file?
   - JSON record (and which field has the body)?
   - PDF (text layer or image)?
   - Email with multipart body?
   - Something exotic?

3. **What metadata is available per piece?**
   - Title (or do you derive it from the body / filename)?
   - Author (or is it always one author — your own writing)?
   - Date (publication / file mtime / message timestamp)?
   - Any obvious skip cases (ads, system messages, comment threads, paid-only, etc.)?

4. **What's the consent / legal posture?**
   - This is required by the manifest schema. Pick one:
     `public_record` (public-domain or government source)
     `cc_licensed` (Creative Commons / open license)
     `fair_use_research` (third-party, used research-only, not redistributed)
     `author_consent` (the original author has explicitly OK'd the use)
     `undocumented` (the validator warns; future public-report harnesses refuse to name)
   - If the user can't articulate this, stop. The schema has the gate for a reason.

5. **Will there be enough text?**
   - Acquisition is for stylometric work. The framework wants ~500+ words per piece minimum, ideally 2000+. If the source is mostly short messages, the pieces won't help the impostor pool.

### Step 2: Read the reference

Open `${CLAUDE_PLUGIN_ROOT}/references/acquire-corpus-pattern.md` and `${CLAUDE_PLUGIN_ROOT}/scripts/acquire_corpus_template.py`. The reference doc is structured for LLM consumption — every section the new script needs has its own heading.

If you (Claude) are reading this skill, also load the worked examples:
- `${CLAUDE_PLUGIN_ROOT}/scripts/acquire_blogger_takeout.py` — simplest local-source acquisition. Best example for archive / file-walking sources.
- `${CLAUDE_PLUGIN_ROOT}/scripts/acquire_blog.py` — most general live-network acquisition. Best example for URL / feed sources.
- `${CLAUDE_PLUGIN_ROOT}/scripts/acquire_magazine.py` — multiple per-source modules behind a uniform CLI. Best example when the user has several similar sources to handle (e.g. multiple Slack workspaces).

### Step 3: Adapt the template

Copy the template to a working file. Suggested naming: `acquire_<source>.py` if reusable, or `acquire_<corpus_name>_oneoff.py` if it's a one-off.

```bash
cp "${CLAUDE_PLUGIN_ROOT}/scripts/acquire_corpus_template.py" \
   "${CLAUDE_PLUGIN_ROOT}/scripts/acquire_<source>.py"
```

Fill the four `TODO(LLM)` markers in order:

1. **`SOURCE_NAME` and `TOOL_NAME` constants** at the top. `SOURCE_NAME` becomes part of `acquired_via` (e.g. `acquire_obsidian_2026-05-09`); `TOOL_NAME` is the argparse program name.

2. **`discover_items(source, options, fetcher)`** — list the items. Yield `ItemMeta` objects with locator, title, author, date, and any source-specific extras. Apply early date-window filtering when discovery has dates cheaply; otherwise leave that for `extract_one`.

3. **`extract_one(item, source, options, fetcher)`** — given one item, return `(body_text, title, author, date)`. The body MUST be plain text — convert HTML / Markdown / JSON / PDF here. Return empty strings + None for fields the source doesn't provide.

4. **`build_arg_parser`** additions — add source-specific flags (archive path, API token file, channel filter, custom selectors). Standard flags are already there.

5. **`parse_options`** — wire any source-specific flags into `ProcessOptions.source_extras` (a free dict so you don't have to fight the type system).

### Step 4: Test against the user's actual source with `--dry-run`

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/acquire_<source>.py" \
    "${USER_SOURCE_PATH_OR_URL}" \
    --persona "<some_slug>" \
    --impostor-for fiction \
    --register blog_essay \
    --consent-status fair_use_research \
    --era pre_chatgpt \
    --max-items 5 \
    --dry-run
```

Expect to see "would write N files" output without actually writing. If `discover_items` finds zero items, the discovery logic is wrong. If `extract_one` produces empty bodies, the extraction logic is wrong. Iterate until dry-run reports the right count.

### Step 5: Real run, then validate

After dry-run looks right:

```bash
# Real run (small batch first):
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/acquire_<source>.py" \
    "${USER_SOURCE}" \
    --persona "<slug>" \
    --impostor-for fiction \
    --register blog_essay \
    --consent-status fair_use_research \
    --max-items 10

# Validate the draft manifest:
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/manifest_validator.py" \
    "<output-dir>/draft_manifest.jsonl"
```

If the validator reports errors, the impostor-required fields aren't getting set correctly. Check `compose_manifest_entry` is being called with `corpus_role: impostor` (default in `acquisition_core.compose_manifest_entry`).

### Step 6: Decide one-off vs. permanent

After the user is happy with the output, ask: should this script live in the framework permanently?

- **Permanent** — if the source is reusable (a popular platform, a common archive format), help the user add fixtures + tests + README section + open a PR. The five existing acquisition scripts are the model.
- **One-off** — if the source is the user's specific archive (a personal Slack, a private note system), keep the script local and don't promote it. Document any source-specific quirks inline as comments so future-self knows why the extractor looks the way it does.

## Example: walking through a Slack export

User: "I have a Slack export I'd like to use. It's a zip with per-channel directories of JSON files."

You:

1. **Survey:** Single zip archive, per-channel JSON. Each message has `text`, `user`, `ts`. The user's own messages are what they want — they'd filter to messages where `user == "U_THEIR_ID"`. Date is the `ts` Unix timestamp. They've been the sole author of those messages for years; consent is `author_consent` (their own writing).

2. **Read the reference + template.**

3. **Adapt:**
   - `SOURCE_NAME = "slack"`, `TOOL_NAME = "acquire_slack"`.
   - `discover_items`: iterate channel directories, glob `*.json`, parse each, yield one `ItemMeta` per long-enough message from the user's `user_id`. The `extra` dict carries `channel_name` for the manifest's `notes` field.
   - `extract_one`: read the message's `text` field, optionally strip Slack mention markers (`<@USERID>` → empty, `<#CHANNELID>` → channel name). Return `(body_text, title="", author=user_display_name, date=datetime.fromtimestamp(float(ts)).date())`.
   - Add `--filter-channel` and `--user-id` flags via `build_arg_parser`.

4. **Dry-run:** `python3 acquire_slack.py path/to/slack_export.zip --user-id U123 --persona <slug> --impostor-for self --register personal --consent-status author_consent --max-items 5 --dry-run`. Verify discovery finds messages, that they're long enough, that extraction produces clean text.

5. **Real run** with a higher `--max-items`. Validate the manifest. Spot-check a few `.txt` files for cleanliness (no leftover Slack markup, no emoji codes).

6. **Probably one-off** — Slack exports are private; the script lives in the user's local checkout, not the framework.

## Common patterns by source family

- **File system / archives** — walk + glob; skip the fetcher entirely; date from mtime or front-matter.
- **JSON-message stream (Slack, Discord, Telegram, X export)** — iterate JSON, filter by author / channel, strip platform markup in `extract_one`.
- **Wiki / Notion / Obsidian markdown** — recursive directory walk; preserve front-matter dates; strip wiki-link syntax (`[[link]]`).
- **Email mbox** — `mailbox.mbox(path)`; pull body, drop quoted reply text, only keep outgoing.
- **Custom CMS API** — use `acquisition_core.Fetcher` with auth headers; paginate the index; fetch posts by ID.

Each of these is a one-day adaptation if the user has a clean export and can articulate consent posture.

## Privacy / safety rules

The standard SETEC privacy guard applies to every adaptation:

- Default output goes under `ai-prose-baselines-private/`. The marker-based check refuses non-private paths unless `--allow-public-output` is set.
- Acquired text is voice-cloning input; treat third-party prose with the same care as the user's own.
- Manifest entries with `consent_status: undocumented` are validator-warned; future public-report harnesses must refuse to name them.
- Don't acquire content the user can't articulate consent for. The validator's gate is real.

## Self-test

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/acquire_corpus_template.py" --help
```

The template's `--help` output lists the standard CLI surface. If it errors with `NotImplementedError`, that's expected — the template's stubs raise on call. Filled-in copies of the template (`acquire_<source>.py`) should run cleanly.
