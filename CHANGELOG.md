# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/) with [Semantic Versioning](https://semver.org/). The version field in `plugins/setec-voiceprint/.claude-plugin/plugin.json` bumps on every shipped commit: `feat:` → MINOR, `fix:` / `chore:` / `docs:` → PATCH. Major version is reserved for breaking changes to the public CLI / JSON contract.

## Unreleased

_(Empty. Future work lands here, gets versioned on commit.)_

## [1.16.0] - 2026-05-09

Plugin packaging fix: scripts now ship with the plugin install. Pre-1.16.0, `scripts/`, `references/`, and `requirements*.txt` lived at the repo root and the plugin dir at `plugins/setec-voiceprint/` only contained `.claude-plugin/plugin.json` + 5 SKILL.md files. SKILL.md script paths used `${CLAUDE_PLUGIN_ROOT}/../../scripts/`, which assumes the marketplace install ships the whole repo — but in practice it ships only the plugin source dir. Result: a fresh marketplace install of setec-voiceprint had `voice_distance.py`, `acquire_blog.py`, every other script, and every reference doc missing. Users would invoke a skill, follow its example command, and hit `python3: can't open file '.../scripts/voice_distance.py': No such file or directory`.

This is a structural fix to ship scripts inside the plugin where every other plugin in the same marketplace ecosystem keeps them (APODICTIC's `plugins/apodictic/scripts/` and `skills/<name>/scripts/` are the model). The MINOR bump is for the structural change; no behavior changes for existing dev-checkout workflows because top-level paths are preserved via symlinks.

### Changed

- **Scripts moved into the plugin directory.** `scripts/` → `plugins/setec-voiceprint/scripts/`; `references/` → `plugins/setec-voiceprint/references/`; `requirements.txt` / `requirements-acquisition.txt` / `requirements-calibration.txt` → `plugins/setec-voiceprint/`. Files moved with `git mv` so blame history is preserved.
- **Top-level paths preserved as symlinks.** `<repo>/scripts -> plugins/setec-voiceprint/scripts`, same pattern for `references` and the three `requirements*.txt`. Git stores symlinks (mode `120000`); `python3 scripts/foo.py`, `pip install -r requirements.txt`, and every existing dev workflow keeps working from the repo root with no changes.
- **SKILL.md script paths updated.** `${CLAUDE_PLUGIN_ROOT}/../../scripts/foo.py` → `${CLAUDE_PLUGIN_ROOT}/scripts/foo.py` across all five skills (44 total replacements). After this change, the path is correct under both the dev-checkout layout (where `${CLAUDE_PLUGIN_ROOT}` is `<repo>/plugins/setec-voiceprint/`) and the marketplace install (where it's `<install-root>/plugins/setec-voiceprint/`).
- **`parents[N]` indices updated** in scripts that resolved their location relative to repo root: `scripts/acquisition_core.py:resolve_baselines_dir` (was `parents[1]`, now `parents[3]`); `scripts/calibration/calibrate_thresholds.py`, `editlens_to_manifest.py`, `fetch_pangram_editlens.py` (were `parents[2]`, now `parents[4]`). The new indices reflect that scripts now live two levels deeper under `plugins/setec-voiceprint/`.

### Notes

- 203 tests pass + 2 skipped (unchanged from 1.15.3). Verified via both `python3 -m pytest scripts/tests/` (top-level symlink path) and `python3 -m pytest plugins/setec-voiceprint/scripts/tests/` (canonical path).
- Marketplace browsers should now resolve to `1.16.0` and the scripts should be present at `${CLAUDE_PLUGIN_ROOT}/scripts/...` on a fresh install.
- Three version fields stay in sync at 1.16.0: `plugin.json :: version`, `marketplace.json :: metadata.version`, `marketplace.json :: plugins[0].version`.
- Symlink behavior across platforms: macOS and Linux honor symlinks transparently. Windows users would need `core.symlinks=true` (default-on with admin / dev-mode); for users who can't do that, the canonical paths under `plugins/setec-voiceprint/` continue to work without symlinks.

## [1.15.3] - 2026-05-09

Five reviewer-flagged P2 fixes against `acquire_blog.py` + `acquisition_core.py` (1.15.0). The cluster is exactly where impostor-corpus tooling needs to be honest: completeness of Substack archive capture, robots.txt and user-agent honesty, paywall handling on direct-HTML fetches, and manifest validity at exit. None of these change cleaned-text shape or downstream semantics; they all close gaps where the script could silently miss content, misrepresent itself to upstream sites, or emit drafts that immediately fail validation.

### Fixed

- **Substack sitemap-index parsing.** `acquire_blog.parse_sitemap_urls` only inspected `<url>` nodes, but most Substack `/sitemap.xml` responses are `<sitemapindex>` documents whose children are `<sitemap><loc>` daughter pointers. The pre-fix function returned an empty list on indexes; the daughter-fetch fallback in `acquire_substack` checked for "sitemap" in the URL basename of returned pairs and never fired because the pairs list was empty. Result: archive-only posts published before the feed window were silently invisible. Fixed by accepting both `<url>` and `<sitemap>` element kinds — they share the same `<loc>` / `<lastmod>` shape, so the parser handles them uniformly and the caller's existing daughter-detection logic now sees the daughter URLs and recurses into them. Two new regression tests: a unit test on `parse_sitemap_urls` with a synthesized index payload, and an end-to-end Substack acquisition where the top-level sitemap is an index pointing at a daughter sitemap that contains the only copy of a post.
- **Robots.txt user-agent-specific disallow honored.** `acquisition_core.Fetcher._robots_allows` returned `rp.can_fetch(self.user_agent, url) or rp.can_fetch("*", url)`. `urllib.robotparser.can_fetch()` already implements user-agent matching with a `*` fallback when no specific block matches; the explicit `or "*"` check overrode any UA-specific disallow with the open `*` block. A site that wrote `User-agent: setec-voiceprint / Disallow: /` while leaving `User-agent: * / Allow: /` would still be scraped — exactly the user-specific opt-out we shouldn't bypass. Fixed by calling `rp.can_fetch(self.user_agent, url)` once. Two new regression tests: a unit test on `_robots_allows` with a synthesized robots parser, and an end-to-end test where the fixture's robots.txt names our UA explicitly and our run produces zero acquired files even though `*` is allowed.
- **`--user-agent` CLI flag is now actually used.** The CLI exposed `--user-agent`, but `run()` constructed the production fetcher via `make_requests_fetcher(version, rate_limit_seconds)` — no `user_agent` parameter, no threading. Live HTTP requests AND robots.txt checks both used the framework's default UA regardless of what the user passed. Fixed by adding a `user_agent` keyword argument to `make_requests_fetcher` (None defaults to `DEFAULT_USER_AGENT.format(version=version)`) and passing `args.user_agent` from `run()`. Two new regression tests verify the override path and the version-formatted default.
- **Paid Substack posts on the direct-HTML path are skipped.** Feed-entry items already get the `_is_paid_excerpt` check via `FeedItem.is_paid` in pass 1 of `acquire_substack`, but pass 2 (sitemap-only posts fetched as raw HTML) sent the post body straight to `process_one_post`. A paid Substack page served as HTML carries the same paywall markers (`paywall` / `subscriber-only` classes, "Subscribe to read" footer, "This post is for paid subscribers" body) but with the actual essay stripped — the previous flow could write the subscription wrapper as a real impostor entry. Fixed by running `_is_paid_excerpt(post_result.text, {})` on the fetched HTML before processing; matches increment `skipped_paid` and log to the skip ledger. New regression test uses a paid-page HTML fixture (paywall markers + minimal body) reachable only via a sitemap pointing at it; the run must produce zero `.txt` files for that URL.
- **`--impostor-for` is required at argparse time.** The flag previously defaulted to `[]`. Every entry `acquire_blog.py` emits has `corpus_role: "impostor"` hardcoded, and `manifest_validator` errors on impostor entries with empty / missing `impostor_for`. The pre-fix flow let users run a full acquisition (spending the network rate-limit budget on, e.g., 50 fetches), exit with code 0, and only discover the manifest was invalid when they ran the validator afterward. Fixed by switching to `required=True` so argparse rejects the missing flag immediately, before any fetch. Two new regression tests verify both the rejection path and that the normal-use path still works.

### Notes

- 203 tests pass + 2 skipped (was 194 + 2 in 1.15.2; +9 new regression tests across the five fixes).
- No cleaned-text format changes. Pre-1.15.3 acquisition runs are still valid; the fixes affect what gets acquired (more, in the sitemap-index case; less, in the paid-post and UA-disallow cases) and the manifest-validity gate (now caught at argparse time).
- One additional fixture file (`substack_sitemap_index.xml`, `substack_sitemap_daughter.xml`, `substack_post_only_in_daughter.html`, `substack_sitemap_paid_only.xml`, `substack_post_paid_html.html`, `robots_disallow_specific.txt`) covers the new test paths.

## [1.15.2] - 2026-05-09

Marketplace version-field completeness. The Claude Code marketplace UI reads version from `marketplace.json`'s `metadata.version` and `plugins[*].version` — not from the source-pointed `plugin.json` — so without those fields, marketplace browsers show stale or fall-through values regardless of how often `plugin.json` bumps. The APODICTIC plugin's `marketplace.json` carries both fields; setec-voiceprint's didn't. New installs were showing 1.7-era metadata as a result.

### Fixed

- `.claude-plugin/marketplace.json`: added `metadata.version` (top-level) and `plugins[0].version` (per-plugin). Both pinned to `1.15.2` and kept in sync with `plugin.json` going forward.
- Cross-check: `python3 -c "..."` script verifies that `plugin.json`'s `version` equals `marketplace.json`'s `metadata.version` equals `marketplace.json`'s `plugins[0].version`. The release process should keep all three locked together; future drift is the same kind of bug that produced the 1.7 fall-through.

### Notes

- Three version fields total: `plugins/setec-voiceprint/.claude-plugin/plugin.json :: version`, `.claude-plugin/marketplace.json :: metadata.version`, `.claude-plugin/marketplace.json :: plugins[0].version`. All bumped together every release.
- No code changes; metadata only. 194 tests pass + 2 skipped (unchanged from 1.15.1).

## [1.15.1] - 2026-05-09

Marketplace metadata catch-up. The `description` field in `.claude-plugin/marketplace.json` had been drifting since the cathedral upgrades landed (1.10.x onward) — it stopped at "MVP empirical validation against labeled corpora" and never picked up voice drift, per-POV voiceprints, restoration packets, before/after verdicts, calibration toolchain, or impostor-corpus acquisition. Marketplace browsers were seeing a stale feature list. The repo carries no separate version field on marketplace.json, but the plugin description is what users see when they search the marketplace, so this is a real surface to keep current.

### Changed

- `.claude-plugin/marketplace.json`: top-level `metadata.description` and per-plugin `description` rewritten to match the current feature surface (voice-coherence including drift / per-POV / impostor pool, validation including voice-validation harness and calibration toolchain, craft-restoration including before/after verdicts, blog acquisition for the impostor pool).
- Both `plugin.json` and `marketplace.json` now share the same expanded `keywords` list: `voice-drift`, `pov-analysis`, `impostor-corpus`, `general-imposters`, `craft-restoration`, `calibration` added alongside the existing eight. The two files are kept in sync so marketplace search results match the installed plugin's metadata.

### Notes

- No code or test changes; documentation/metadata only. 194 tests pass + 2 skipped (unchanged from 1.15.0).
- Future releases should keep `marketplace.json`'s `description` and `keywords` in sync with `plugin.json` whenever a feature lands. A small lint at release time (or a pre-commit hook) would catch drift earlier; deferred to a maintenance pass.

## [1.15.0] - 2026-05-09

Blog acquisition tooling for the impostor corpus. Commit 2 of three for the impostor-corpus spec (`internal/2026-05-08-impostor-corpus-spec.md`); Commit 1 (1.14.3) shipped the schema, Commit 3 (`acquire_magazine.py` + `pdf_inventory.py` + `pdf_extract.py`) follows. The General Imposters validation harness still has to be wired up separately, but with this release the framework can now build the impostor pool the harness needs from any Substack, WordPress / Ghost blog, or generic-HTML archive.

### Added

- **`scripts/acquire_blog.py`** — single-author blog/Substack archive acquisition with auto-detection across four extraction paths:
  1. **Substack** (`*.substack.com` or Substack-shaped feed at `<url>/feed`) — RSS for recent posts (full text) plus `sitemap.xml` for the full archive. Paid-only posts are detected via class markers / `audience: only_paid` and skipped with a flag; v1 ships no `--include-paid` because authenticated fetch is out of scope.
  2. **WordPress / Ghost** (responds with WP/Ghost-shaped feed at `/feed/` or `/rss/`) — feed parse plus a per-post HTML fetch when feed body looks short.
  3. **Generic HTML archive** (no recognizable feed) — requires `--archive-pattern` pointing at the index page; default link heuristic catches `/YYYY/MM/`-style and `/posts/` URLs.
  4. **Wayback Machine** (`--wayback`) — uses the CDX API to enumerate snapshots within the date window; fetches the most recent snapshot per URL.
- **CLI surface** mirrors the spec: `--persona`, `--impostor-for`, `--register`, `--register-match`, `--topic-match`, `--consent-status`, `--era`, `--since` / `--until`, `--max-posts`, `--rate-limit`, `--user-agent`, `--dry-run`, `--emit-manifest`, `--output-dir`, `--out`, `--allow-non-prose` / `--strip-rules` / `--strip-aggressive` (passed through to `preprocessing.py`), `--allow-public-output` (privacy guard override), and source-type override flags (`--substack` / `--wordpress` / `--html-archive` / `--wayback`). Site-config registry seeded for `marginalrevolution.com`, `slatestarcodex.com`, `overcomingbias.com`, `jehsmith.substack.com`, `thedarkmagazine.com`.
- **Per-piece output convention.** Each acquired post produces `<output>/<YYYY-MM-DD>_<title-slug>.txt` (cleaned text) plus a `.meta.json` sidecar (URL, date, hash, raw byte length, scraper version, full preprocessing metadata block). Default output dir is `<baselines>/impostors/<register>/<author_slug>/`; baselines root resolves through `$SETEC_BASELINES_DIR`, then the documented sibling-of-repo `ai-prose-baselines-private/`, then a fallback under `~/Documents/`. Draft manifest written to `<output>/draft_manifest.jsonl` by default; user merges into `corpus_manifest.jsonl` after review.
- **Impostor manifest emission.** Every emitted entry carries `corpus_role: "impostor"`, `use: ["voice_impostor"]`, `split: "baseline"`, `privacy: "private"`, plus all five impostor-required fields (`impostor_for`, `register_match`, `topic_match`, `consent_status`, `era`) and `acquired_via` keyed by source-type and date (e.g. `acquire_blog_substack_rss_2026-05-09`). `content_hash` (SHA-256 of cleaned text, prefix `sha256:`) populated for dedupe.
- **`scripts/acquisition_core.py`** — shared helpers for the impostor-corpus pipeline. Will be reused by Commit 3's magazine + PDF tools:
  - `slugify` (Unicode-folded ASCII slug with `max_length` and word-boundary trim) and `author_to_persona_slug` (deterministic `lastname_firstname_<suffix>` with collision suffixes).
  - `compute_content_hash` (SHA-256 with `sha256:` prefix, matching the manifest schema).
  - `parse_iso_date` (anchored `YYYY[-MM[-DD]]` + python-dateutil fallback for human formats; returns `None` rather than raising on garbage).
  - `is_private_safe_path` and `check_output_privacy` — marker-based privacy guard mirroring `voice_profile.is_private_output_path` (any path component named `ai-prose-baselines-private` qualifies; repo-internal, sibling, and absolute paths all pass).
  - `Fetcher` abstract base + `FixtureFetcher` (test mock; URL → fixture-file mapping) + `make_requests_fetcher` (production wrapper around `requests` with rate limiting per host, robots.txt enforcement via `urllib.robotparser`, and SETEC user-agent header). The fetcher abstraction is what lets the regression tests run without network access.
  - `html_to_text` (BeautifulSoup with lxml backend, drops `<script>` / `<style>` / `<nav>` / `<aside>` / `<footer>` / `<form>` / `<svg>` globally, plus user-supplied strip selectors, then restricts to a CSS content selector with sensible fallbacks). `html_text_is_clean` is the corresponding test predicate that asserts no HTML tags survived.
  - `AcquiredPiece` dataclass (one acquired text artifact) + `RunSummary` dataclass (acquisition-run aggregate; renders the `Acquired: N files / Skipped (paid-only): N / ...` block on stderr).
  - `write_piece` (atomic `.txt` + `.meta.json` write), `content_hash_already_present` (within-output-dir dedupe scan), `compose_manifest_entry` (impostor-schema-conforming dict), `append_manifest_entry` (append-only JSONL writer with stable key ordering).
  - `preprocess_text` — pipe-through to `scripts/preprocessing.py` so impostor entries are subject to the same content-level guards as identity baselines.
- **`requirements-acquisition.txt`** — opt-in dependency layer matching the existing `requirements-calibration.txt` pattern. Pins `requests`, `feedparser`, `beautifulsoup4`, `lxml`, `python-dateutil`, `pypdf` (for Commit 3); the optional `wayback` and `ocrmypdf` lines are commented with install notes. Ordinary diagnostics, validation, voice distance, and plugin installation do NOT need this layer.
- **Fixture corpus** under `scripts/test_data/acquisition_blog_fixture/`: `substack_feed.xml` (one full-text post + one paid/excerpt-only post + one extra full-text post), `substack_sitemap.xml` (six URLs spanning 2017–2024 for date-window tests), `substack_post_archive.html` (the sitemap-only post fetched via HTML extraction), `wordpress_feed.xml`, `generic_archive.html` (with two post links plus a non-post `/about/` link), `generic_post_quiet_room.html` and `generic_post_attention.html` (with sidebar/script/nav noise that must be stripped), `robots_allow.txt`, `robots_disallow.txt`. All synthetic-prose-only; no real third-party content.
- **`scripts/tests/test_acquire_blog.py`** — 32 regression tests covering the full surface:
  - `acquisition_core` unit tests: slugify (basic + Unicode + max_length), persona-slug rule, content hash determinism, ISO date parser partials, marker-based private-path check, `html_to_text` script/style/nav stripping, `html_text_is_clean` predicate.
  - Substack feed parsing: full-text extraction + paid-marker detection across three flavors + RFC822 date parsing.
  - Sitemap URL filtering by date window.
  - Source-type auto-detection across hostname / feed-probe / generic-fallback.
  - End-to-end Substack acquisition: 3 written posts (paid skipped, sitemap-only one fetched via HTML), manifest entries carry every impostor schema field, cleaned text passes the no-HTML-residue check, no `Subscribe` widget leaks, no trailing comments block, content hashes unique, preprocessing metadata present per sidecar, manifest validates clean.
  - End-to-end WordPress and generic-HTML acquisition with the same invariants.
  - Dedupe-by-content-hash within output dir (a second run against the same dir writes nothing new).
  - Privacy guard refusal path (non-private output → `sys.exit(2)`) + acceptance path (sibling-style private root works).
  - Robots.txt: `Disallow: /` blocks all fetches (zero posts written); `Allow: /` lets fetches through.
  - `--since` / `--until` filters posts by date_written.
  - `--dry-run` writes nothing.
  - `compose_manifest_entry` direct check — every required field present, no None values that would trip validator warnings.
  - End-to-end manifest-validator integration: emitted manifest validates clean (zero errors) when an identity_baseline entry naming the impostor's target persona is added.

### Notes

- This is Commit 2 of three. Commit 3 (`acquire_magazine.py` + `pdf_inventory.py` + `pdf_extract.py`) reuses the `acquisition_core` helpers shipped here. The General Imposters validation harness — the consumer that turns the impostor pool into calibrated attribution claims — is roadmap-tracked separately.
- Privacy posture: acquired text is voice-cloning input from someone else's prose. Default output goes under `ai-prose-baselines-private/impostors/<register>/<author_slug>/`; the privacy guard refuses non-private paths unless `--allow-public-output` is set; impostor entries are never published or distributed; future public-report harnesses must anonymize impostor identities by default and refuse to name `consent_status: undocumented` writers.
- 194 tests pass + 2 skipped (was 163 + 1 in 1.14.3; +32 new acquisition tests, with one previous test reclassified between buckets). One additional `voice_validation_harness.test_manifest_validator_accepts_voice_validation_use` is now part of the count after the impostor schema landed; net change is +31 tests, +1 reclassified.

## [1.14.3] - 2026-05-09

Manifest schema + validator extensions for impostor-corpus support. Commit 1 of three for the impostor-corpus tooling spec (`internal/2026-05-08-impostor-corpus-spec.md`); Commit 2 (`acquire_blog.py`) and Commit 3 (`acquire_magazine.py` / `pdf_inventory.py` / `pdf_extract.py`) follow. The General Imposters validation harness (Koppel et al. 2014, Kestemont et al. 2016) the framework will eventually wire up needs an impostor pool labeled with provenance, consent, register-match strength, era, and corpus-role; this release ships the schema and the validator ratchets that catch impostor-pool misconfiguration at manifest-load time.

### Added

- **Manifest schema additions** (per `references/manifest-schema.md`, the new canonical schema reference). `corpus_role` (default `identity_baseline` for backward compatibility), `impostor_for`, `register_match`, `topic_match`, `consent_status`, `era`, `acquired_via`, `content_hash` are now recognized fields in `KNOWN_FIELDS`. `voice_impostor` added to `ALLOWED_USE`. `literary_horror` added to `ALLOWED_REGISTER` (one of the magazine-acquisition genres in Commit 3). New enum constants: `ALLOWED_CORPUS_ROLE`, `ALLOWED_REGISTER_MATCH`, `ALLOWED_TOPIC_MATCH`, `ALLOWED_CONSENT_STATUS`, `ALLOWED_ERA`.
- **Five new ratchet rules** in `manifest_validator.validate_entry` and `validate_manifest`:
  1. **Impostor required fields** (error). Entries with `corpus_role: impostor` must carry the full impostor metadata block (`impostor_for`, `register_match`, `topic_match`, `consent_status`, `era`, `acquired_via`). Missing any → error.
  2. **Persona-reference + cross-register cross-check** (warning). The validator builds a `persona → set(register)` map from identity-baseline entries during the first pass; impostor entries are then validated in a post-pass against that map. An impostor's `impostor_for` referencing a persona absent from any identity-baseline entry warns. An impostor with `register_match: high` whose own register doesn't appear in the target persona's register set warns.
  3. **Consent-status redistribution ratchet** (warning). `corpus_role: impostor` + `consent_status: undocumented` warns. Future public-report harnesses should escalate this to a refusal unless identities are anonymized and no raw text is emitted.
  4. **Post-AI-era warning** (warning). `corpus_role: impostor` + `era: post_ai_widespread` warns. Post-2024 prose may include AI-collaborated writing that contaminates the human-impostor signal; the user can override intentionally.
  5. **Identity-baseline era recommendation** (warning). Entries with effective `corpus_role: identity_baseline` AND `use` overlapping `{baseline, voice_profile, voice_validation, idiolect, voice_impostor}` AND missing `era` warn. Validation-only entries are exempt — era is for impostor calibration, not for labeled-AI test data.
- **New summary buckets** in the validator's report and JSON output: `by_corpus_role`, `by_era`, `by_consent_status`, `by_register_match`. These appear alongside the existing `by_register` / `by_ai_status` / etc. buckets.
- **`references/manifest-schema.md`**: canonical schema reference. Required fields, common optional fields, impostor fields, allowed enum values, ratchet rules, summary block, and three example entries (identity baseline, impostor, validation). The schema previously lived across `scripts/README.md`, `manifest_validator.py`, and various examples; this consolidation gives downstream contributors and Codex sessions one place to look.
- **`scripts/test_data/impostor_corpus/manifest.jsonl`**: 10-entry synthetic mixed-manifest fixture exercising every ratchet path. Includes a clean impostor, a high-register-match impostor that does pass, an impostor pointing at an unknown persona, an impostor with `register_match: high` but a different register from its target, an undocumented-consent impostor, a post-AI-era impostor, an impostor missing the full required block, an identity-baseline missing `era` with impostor-relevant `use`, and a validation-only entry that's exempt from the era warning. Plus 10 stub `.txt` files so path validation passes.
- **`scripts/tests/test_impostor_manifest_ratchets.py`**: 18 regression tests covering constants surface (the new enum sets, `voice_impostor` in `ALLOWED_USE`, `literary_horror` in `ALLOWED_REGISTER`, new fields in `KNOWN_FIELDS`), all five ratchets with both passing and failing cases, the validation-only-exempt-from-era path, and the new summary-bucket counts (concrete numbers pinned: 7 impostor + 3 identity_baseline, 6 pre_chatgpt + 1 post_ai_widespread, etc.).

### Notes

- This is Commit 1 of three. Commits 2 and 3 (`acquire_blog.py` and the magazine + PDF acquisition tools) require new dependencies (`requests`, `feedparser`, `beautifulsoup4`, `lxml`, `python-dateutil`, `pypdf`, optional `ocrmypdf`) and will land in `requirements-acquisition.txt` per the existing opt-in pattern. They're independently shippable; the schema work shipped here is the prerequisite they all consume.
- Backward compatibility: pre-impostor manifests still validate. `corpus_role` defaults to `identity_baseline` when absent. The era-recommendation ratchet only fires on entries that actually feed impostor calibration, so old `use: validation` manifests don't suddenly generate noise.
- 163 tests pass + 1 skipped (was 145 + 1 in 1.14.2; +18 new ratchet tests).

## [1.14.2] - 2026-05-09

Two further reviewer-flagged P2s on the voice-drift and per-POV trackers (1.13.0 / 1.14.0 / 1.14.1). Both are bugs that surfaced because the tools' behavior was technically working but pedagogically training users into the wrong habits.

### Fixed

- **Privacy-guard allowlist mismatch** in `voice_drift_tracker._check_output_privacy` and `pov_voice_profile._check_output_privacy`. The previous implementation rooted the allowlist at `<repo>/ai-prose-baselines-private/`, but the README and the documented standard layout use a SIBLING `../ai-prose-baselines-private/` directory next to the repo. Users following the documented safe path were hitting the refusal and learning to bypass it with `--allow-public-output` — which trained them to disable the privacy guard. Fixed by switching to the marker-based check `voice_profile.py` already uses (`is_private_output_path`): a path is treated as private if any component in its resolved-absolute form is named `ai-prose-baselines-private`. Repo-internal, sibling, and any other location named that all pass; everywhere else still requires the explicit override. Three new regression tests per tracker cover the sibling-path acceptance, the nested-path acceptance, and the still-refused-without-marker path.
- **Two-POV corpus-mean overclaim** in `pov_voice_profile.pov_vs_corpus_mean_distances`. The previous implementation computed an unweighted midpoint of POV centroids; with K=2, both POVs were equidistant from that midpoint by construction (the existing test asserted this equidistance). The markdown report's framing — "identifies which POV is closest to the writer's neutral default" — was a false claim in the K=2 case. Two-part fix: (a) the function now computes a **word-weighted** corpus-mean centroid (long chapters carry more voice; the mean is biased toward the POV(s) that dominate the manuscript), restoring real signal for K≥2 with unequal word counts and any K≥3; (b) the markdown renderer **suppresses** the corpus-mean section when K=2 with an explicit caveat noting that the diagnostic is structurally weak at two POVs (the word-weighted midpoint just measures which POV got more pages, which is tautological with the input). JSON output retains the raw values either way for callers who want them with the caveat in mind. The existing equidistance test was replaced with a word-weighted-asymmetry test (Madison, with 7848 words on the Federalist fixture, is now closer to the weighted mean than Hamilton with 5888) plus a synthetic test (POV with 10000 words is closer to the weighted mean than POV with 1000) and a markdown-suppression test (the K=2 caveat fires; the per-POV table doesn't render).

### Notes

- Both fixes are reviewer-flagged P2s on top of 1.14.1 — the framework caught them because the tools' technically-correct behavior was training users into the wrong instincts (bypass the privacy guard; trust a structurally-meaningless diagnostic). The CHANGELOG records both because future contributions should know which framings are verified vs. which carry asterisks.
- 145 tests pass + 1 skipped (was 138 + 1 in 1.14.1; +7 new regression tests).

## [1.14.1] - 2026-05-09

Three reviewer-flagged P2 fixes against the voice-drift and per-POV trackers shipped in 1.13.0–1.14.0. The Burrows-Delta one is the substantive one: numeric output for two-period or two-POV reports changes from the (broken) constant `sqrt(2)` to magnitude-sensitive values. Date parser strictness and stdout privacy posture also tightened.

### Fixed

- **Burrows-Delta two-group degeneracy** in `voice_drift_tracker.cross_period_distances` and `pov_voice_profile.cross_pov_distances` + `pov_vs_corpus_mean_distances`. The pre-fix implementation computed z-score column stats over the K period/POV centroids themselves; with K=2 (the natural pre/post or two-character workflow), every informative feature collapsed to symmetric z-scores ±sqrt(2)/2, forcing |z_a − z_b| to a constant `sqrt(2) ≈ 1.4142` regardless of actual drift magnitude. Reproduced by the reviewer with both a tiny shift and a huge shift returning bit-identical Burrows-Delta values. Fixed by computing column stats over the per-DOCUMENT feature vectors across all groups (matches the convention `voice_validation_harness` already uses). Numeric output changes for any two-period or two-POV report; calibrated values from prior runs are not comparable. Cosine-distance values are unchanged (cosine doesn't z-score). Two new regression tests per tracker assert the value is no longer the suspicious sqrt(2) constant on the Federalist fixture, plus synthetic micro-fixtures verify large-drift Burrows-Delta > small-drift Burrows-Delta — the magnitude signal the pre-fix degeneracy threw away.
- **Date parser accepted malformed suffixes and impossible calendar dates** in `voice_drift_tracker._parse_iso_date`. The pre-fix regex was prefix-anchored only, so `"2020-01-foo"` parsed as January 2020. Day-of-month wasn't validated against the month, so `"2020-02-31"` parsed as a real date. Both failure modes silently misclassified documents into wrong periods. Fixed: regex now anchored at both ends and requires fixed-width components (`YYYY`, `YYYY-MM`, or `YYYY-MM-DD`); full year-month-day values are validated via `datetime.date` so impossible combinations (Feb 30/31, Apr 31, non-leap-year Feb 29, etc.) are rejected. Year-only and year-month partials still accepted. Three new regression tests cover trailing-garbage rejection, impossible-date rejection (including leap-year edge cases for 2020 vs. 2021), and continued acceptance of valid partials.
- **Stdout privacy bypass** in both `voice_drift_tracker.main` and `pov_voice_profile.main`. The privacy guard checked only `--out` and `--json-out` paths; when both were omitted, the report wrote to stdout without going through the guard. Voice-drift and POV-voiceprint output is voice-cloning input, and stdout writes can leak voiceprint details into terminal logs / CI artifacts / shell history. Fixed: stdout output now also requires `--allow-public-output`; without the override, `main()` exits with code 2 and a stderr message pointing at the file-output flags. Two new regression tests per tracker verify the refusal path and the allow-flag override path.

### Notes

- The Burrows-Delta fix changes numeric output. Anyone who recorded specific values from the 1.13.0 / 1.14.0 trackers should re-run after this update; the old values were degenerate (constant `sqrt(2)` for two-group reports).
- All 138 tests pass + 1 skipped (was 126 + 1 in 1.14.0; +12 new regression tests for these three fixes).

## [1.14.0] - 2026-05-09

Closes cathedral upgrade #6 (voice profile expansion). `pov_voice_profile.py` is the second sub-item — per-POV-character voiceprints for multi-POV fiction, with a heuristic voice-collapse detector flagging pairs of POVs that share too much voice space to be reliably distinguished. Pairs with `voice_distance.py` (writer vs. own baseline) and `voice_drift_tracker.py` (baseline disaggregated by time) to give the framework a complete voice-coherence diagnostic stack: drift across writers, drift across time, drift across characters.

### Added

- `scripts/pov_voice_profile.py` (~600 lines): per-POV voiceprint generator. Reads a manifest with the `pov` field on selected entries (filterable by `--use`, default `voice_profile`). Per-POV centroid in shared feature space; pairwise Burrows-Delta + cosine across POVs; weighted-family aggregate using `FAMILY_WEIGHTS` and `OVERALL_FAMILY_DELTA_CAP`. Reports POV-vs-corpus-mean distance (which POV is closest to the writer's neutral default — useful for identifying the writer's home register). Reports top distinguishing features per POV (per-POV centroid vs. mean of OTHER POVs, not the corpus mean — that would dilute the comparison by including this POV itself). Voice-collapse verdict flags pairs whose weighted Burrows-Delta falls below the configurable `--collapse-threshold` (heuristic default 0.5; calibration roadmap). Refuses to run when fewer than 2 POVs survive `--min-docs-per-pov` filtering. `task_surface: voice_coherence`. Privacy guard refuses output paths outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed.
- `scripts/test_data/federalist_pov_manifest.jsonl`: synthetic POV-tagged manifest pointing at the existing public-domain Federalist Papers fixture. Maps the 6 documents to two POVs (Hamilton, Madison; 3 docs each) — same trick the drift tracker uses. Cross-POV Burrows-Delta = 1.4142 (different writers in function-word space); collapse verdict correctly does NOT fire at the default 0.5 threshold.
- `scripts/tests/test_pov_voice_profile.py`: 18 regression tests covering manifest loading + POV grouping + min-docs filter, end-to-end run on Federalist (Burrows-Delta > 0.5, distinguishing features surface, POV-vs-mean equidistant for the 2-POV case), no-collapse-flag at default threshold, collapse-flag-fires with aggressive threshold, refusal paths (only one POV after filtering, no POV-tagged entries), privacy guard, JSON / markdown rendering (with collapse section appearing only when flagged), CLI smoke test.

### Changed

- `scripts/README.md` Surface 2 entry extended to mention `pov_voice_profile.py`. Surface tag table updated.
- `plugins/setec-voiceprint/.claude-plugin/plugin.json` description extended to include "per-POV voiceprints with voice-collapse detection."

### Cathedral status

After 1.14.0, **cathedral upgrade #6 is fully shipped:**

- ✅ #1 Manifest as law
- ✅ #2 Length-matched bootstrap
- ✅ #3 Validation harness (both surfaces)
- 🚧 #4 Impostor baselines — corpus-bound; the only upgrade still genuinely blocked on a non-code prerequisite
- ✅ #5 Sliding-window localization
- ✅ **#6 Voice profile expansion** — core (1.0.0), idiolect (1.6.0), time drift (1.13.0), per-POV profiles (this release)
- ✅ #7 Before/after restoration loop
- ✅ #8 Privacy / packaging guards

Seven of eight cathedral upgrades are shipped. The framework's voice-coherence stack now answers four distinct questions: "how far is this draft from baseline?" (`voice_distance.py`), "what phrases must survive revision?" (`idiolect_detector.py`), "has the writer's voice changed across time?" (`voice_drift_tracker.py`), and "are this writer's POV characters voice-distinct?" (`pov_voice_profile.py`).

## [1.13.0] - 2026-05-08

Cathedral upgrade #6 — voice profile expansion: time-drift tracking. `voice_drift_tracker.py` disaggregates the writer's baseline by time period, computes cross-period voice distance, and identifies drifting vs. stable features. Pairs with `voice_distance.py` to distinguish "drift between draft and baseline" (recent) from "drift across the writer's own history" (long-term).

### Added

- `scripts/voice_drift_tracker.py` (~600 lines): time-drift surface for cathedral upgrade #6. Reads date-tagged baseline documents from a manifest (with `date_written`) or a directory (with date-prefixed filenames via configurable regex), or accepts an explicit `--periods-json` mapping. Groups documents into periods at the requested granularity (`year` / `quarter` / `month` / `custom` with explicit boundaries). Per-period voiceprint computed via `stylometry_core.extract_features` + `select_feature_names` + per-doc-mean centroid. Cross-period distance: pairwise Burrows-Delta + cosine in a shared feature space (centroids z-scored over the set of period centroids; informative-feature filter same as oracle and voice-validation harness). Weighted-family aggregate using `FAMILY_WEIGHTS` and `OVERALL_FAMILY_DELTA_CAP`. Per-feature drift scoring: coefficient of variation across period centroids. Reports top drifting + top stable features per family. Refuses to run when fewer than 2 periods survive `--min-docs-per-period` filtering. `task_surface: voice_coherence`. Privacy guard: refuses output paths outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed (voice drift output is voice-cloning input).
- `scripts/test_data/federalist_drift_manifest.jsonl`: synthetic date-tagged manifest pointing at the existing public-domain Federalist Papers fixture. Six entries spanning 1787-10-27 through 1788-01-16. Year granularity yields 2 periods (1787 with 5 docs, 1788 with 1 doc); the cross-period distance reflects authorship change (Hamilton vs. Madison) which is detectable as voice change. Useful for exercising the code paths even though it's not single-writer time drift.
- `scripts/tests/test_voice_drift_tracker.py`: 20 regression tests covering date parsing (partial dates, granularity-specific period keys, custom boundaries), manifest loading + filtering by `use`, period grouping with `min-docs-per-period` filter, end-to-end run on the Federalist fixture (Burrows-Delta > 0.5 between Hamilton and Madison periods, drifting features surface for `function_words`), refusal-when-only-one-period, privacy guard (refuses public output without `--allow-public-output`), JSON / markdown rendering, and CLI smoke tests.

### Changed

- `scripts/README.md` Surface 2 entry extended to mention `voice_drift_tracker.py`. Surface tag table updated. Added explanatory note: drift tracker can tell *which* features are drifting but not *why* — natural stylistic evolution and symptomatic distortion both produce drift; the writer's local read decides.
- `plugins/setec-voiceprint/.claude-plugin/plugin.json` description extended to include "voice drift tracking across time periods."

### Cathedral status

After 1.13.0, **6 of 8 cathedral upgrades are shipped or partly shipped** with the gap reduced:

- ✅ #1 Manifest as law
- ✅ #2 Length-matched bootstrap
- ✅ #3 Validation harness (both surfaces)
- 🚧 #4 Impostor baselines — corpus-bound; no code unlock pending
- ✅ #5 Sliding-window localization
- 🟡 #6 Voice profile expansion — time drift shipped (this release); `pov_voice_profile.py` is the remaining sub-item
- ✅ #7 Before/after restoration loop
- ✅ #8 Privacy / packaging guards

Two upgrades remain partly open: #4 (corpus-bound) and #6 sub-item #2 (`pov_voice_profile.py` for multi-POV fiction).

## [1.12.1] - 2026-05-08

Roadmap pass: records the next bounded calibration-corpus follow-ups + flags `voice_drift_tracker.py` as the active next pick under cathedral upgrade #6. No code changes.

### Changed

- `ROADMAP.md`: new "Calibration corpus track" section documenting three bounded follow-ups to the 1.10.0 calibration toolchain — `fetch_raid.py` (RAID benchmark, Apache-2.0, openly redistributable; the most comprehensive openly-licensed AI-detection benchmark), `fetch_mage.py` (MAGE benchmark, MIT; companion to RAID), and `PROVENANCE_TEMPLATE.md` (walkthrough for new users on collecting and labeling personal pre-AI baseline corpora — the irreducible piece of the corpus pool). Each is independently shippable; ordered by leverage. Cathedral upgrade #6 status line updated to flag `voice_drift_tracker.py` as the active next pick (bounded code work on `stylometry_core` primitives, no exotic borrow), with `pov_voice_profile.py` queued behind it.

## [1.12.0] - 2026-05-08

Closes cathedral upgrade #7 v2. The post-check loop that v1 (1.11.0) left as a manual workflow is now automated: `scripts/before_after_restoration.py` reads "before" and "after" diagnostic JSONs (plus the original `restoration_packet.py` output) and reports per-target verdicts with a metric-gaming detector. The framework's metric-gaming resistance now has both a *preventive* surface (the targetability taxonomy in 1.11.0 refuses to issue revision instructions on aggregate divergences) and a *detective* surface (this release's gaming heuristic flags improved targets whose improvement coincides with worsening avoid-direct aggregates).

### Added

- `scripts/before_after_restoration.py` (~600 lines): post-check verdict reporter. Reads any subset of the standard SETEC diagnostic JSONs (variance audit, bigram diff, voice distance, idiolect detector) for both before and after states, plus the original packet output. Two modes:
  - **Packet-driven mode** (`--packet-json` supplied): evaluates each target in the packet against its before/after value with direction-aware improvement logic. For variance signals, looks up the registry's `ThresholdSpec.direction` to know which way is improvement (`lt` signals like `burstiness_B` improve when value rises; `gt` signals like `connective_density` improve when value falls). For bigram packets, improvement = `|kl_contrib|` decreases regardless of sign direction. Per-target verdicts: `improved` / `no_change` / `degraded` / `gamed` / `not_measurable`. Per-signal noise thresholds in `NOISE_THRESHOLDS` constant prevent micro-fluctuations from registering as verdicts.
  - **Diff-only mode** (no packet): raw before/after deltas across every measurable signal. Useful for general "what changed" inspection without committing to a pre-registered set of targets.
- **Metric-gaming detector.** When any actionable (direct or translated) target improves AND a registered avoid-direct aggregate (POS-bigram KL total, voice-distance overall) moves *against* improvement by more than its noise threshold, the verdict flips from `improved` to `gamed`. The note explains why: the revision optimized the local target without addressing the underlying drift — exactly the failure mode that the v1 targetability taxonomy refuses to issue revision instructions on, now caught after the fact when a writer or LLM optimized one anyway.
- **Preservation-list survival check.** When `--original-text` and `--revised-text` are supplied, the report includes a case-insensitive substring search confirming whether each phrase from the idiolect packet's preservation list appears in the revised text. Reports survival rate + list of missing phrases (capped at 30 to keep the output bounded).
- `scripts/test_data/before_after_restoration/`: synthetic fixtures simulating each verdict path (improved, gamed, degraded) plus paired bigram fixtures and two revised-text fixtures (one preserves all phrases, one drops two).
- `scripts/tests/test_before_after_restoration.py`: 19 regression tests covering each verdict path (direction-aware classification for both `lt` and `gt` registry signals; bigram `|kl_contrib|`-reduction logic; degradation), the metric-gaming detector (gaming flag fires when aggregate KL rose; doesn't fire when aggregate fell), avoid-direct packets never claiming improvement, preservation-list survival (full survival + partial survival + skip-when-no-text), diff-only mode (with band-shift detection), JSON / markdown rendering, and CLI smoke tests.

### Changed

- `scripts/README.md` Surface 4 entry extended to mention `before_after_restoration.py` alongside `aic_pattern_audit.py` and `restoration_packet.py`. Surface tag table updated.
- `plugins/setec-voiceprint/.claude-plugin/plugin.json` description refresh to include "before/after restoration verdicts with metric-gaming detection."

### Cathedral status

After 1.12.0:

- ✅ #1 Manifest as law
- ✅ #2 Length-matched bootstrap
- ✅ #3 Validation harness (both surfaces)
- 🚧 #4 Impostor baselines — corpus-bound; no code unlock pending
- ✅ #5 Sliding-window localization
- 🚧 #6 Voice profile expansion — `voice_drift_tracker.py` + `pov_voice_profile.py` are bounded code work
- ✅ #7 Before/after restoration loop (v1 packet generator + v2 post-check verdict reporter both shipped)
- ✅ #8 Privacy / packaging guards

Six of eight cathedral upgrades are shipped. Two remain partly open: #4 (corpus-bound) and #6 (bounded code work). The framework now has both the prevention surface (refuse to issue revision instructions on aggregate divergences) and the detection surface (flag improved targets that came at the cost of worse aggregates) for metric-gaming resistance.

## [1.11.0] - 2026-05-08

Metric-targeted restoration: cathedral upgrade #7's first scoped slice. Closes the bridge between SETEC's diagnostic surfaces (Surface 1 smoothing-diagnosis, Surface 2 voice-coherence) and its revision-advisor surface (Surface 4 craft-restoration). The new skill consumes diagnostic JSON and emits bounded prompt packets that classify each signal as direct / translated / investigate-first / avoid-direct, with named guardrails and required post-check commands. The framework's metric-gaming resistance lives in the targetability taxonomy.

### Added

- `references/metric-targeted-restoration.md` (343 lines): the canonical reference. Four-class targetability taxonomy with examples for each class (direct, translated, investigate_first, avoid_direct); POS-bigram and POS-trigram translation tables; dependency-n-gram handling; the restoration-packet JSON schema; prompt-packet field requirements; before/after verification protocol; privacy guard rules. Cross-references the existing Surface 4 reference docs (`aic-flags.md`, `source-triage.md`, `rhetorical-countermoves.md`, `distributional-diagnostics.md`) so the new surface integrates with the existing craft-restoration reference prose.
- `scripts/restoration_packet.py` (~700 lines): the packet generator. Consumes JSON outputs from any subset of `variance_audit.py`, `bigram_diff.py`, `voice_distance.py`, `idiolect_detector.py`, and `aic_pattern_audit.py` (at least one required). Classifies each signal via `DIRECT_TARGETS`, `POS_BIGRAM_TRANSLATIONS`, `POS_TRIGRAM_TRANSLATIONS`, `DEP_NGRAM_TRANSLATIONS`, `INVESTIGATE_FIRST`, and `AVOID_DIRECT` constants. Direction-aware translation (over- vs. under-represented bigrams emit different diagnoses). Severity classification (`light` / `moderate` / `heavy`) from KL contribution or z-score magnitude. `--max-targets` caps actionable (direct + translated) targets per packet at 3 by default, since combining five metric instructions produces incoherent revision pressure. CLI emits both JSON (`--json-out`) and markdown (`--out`); the markdown report is copy/paste-ready as a prompt with the named guardrails attached. Privacy guard refuses output outside `ai-prose-baselines-private/` when private inputs (`--idiolect-json` or `--voice-json`) are supplied unless `--allow-public-output` is passed. `task_surface: craft_restoration`.
- `plugins/setec-voiceprint/skills/metric-targeted-restoration/SKILL.md`: new plugin skill (the framework's fifth public skill, sibling to `craft-restoration` rather than a replacement). Trigger phrases include "reverse this smoothing trend," "make a revision prompt from this diagnostic," "what can an LLM safely target," "metric-targeted restoration," "translate POS bigrams/trigrams," and "post-check this revision." Documents the four-class targetability taxonomy, the workflow (run diagnostics → generate packet → read sections → apply prompt → run post-check), the guardrails, and the privacy posture.
- `scripts/test_data/restoration_packet/`: three synthetic JSON fixtures (`synthetic_bigram_diff.json`, `synthetic_variance.json`, `synthetic_idiolect.json`) crafted to fire specific packet IDs. The bigram fixture's top contributor is `DET-ADJ-NOUN` (a trigram, skipped by bigram translations); the next-ranked `ADJ-NOUN` lands as the first translated packet. `PRON-VERB` has negative `kl_contrib` to test the under-represented direction branch. An unknown bigram (`X-Y`) tests the unknown-bigram skip path.
- `scripts/tests/test_restoration_packet.py`: 20 regression tests covering taxonomy correctness (the load-bearing thing — the framework's metric-gaming resistance lives here), each surface's packet generator, top-level packet assembly + ordering + the actionable cap, render correctness for both JSON and markdown (including the "raw POS labels never appear without a plain-language gloss" check), and a CLI smoke test. The taxonomy tests assert that aggregate divergences (`pos_bigram_kl_total`, `burrows_delta_overall`, `char_ngram_distance`) NEVER appear in the direct/translated/investigate buckets — guards against silent regression of the framework's metric-gaming resistance.

### Changed

- `scripts/README.md`: Surface 4 entry extended to mention `restoration_packet.py` alongside `aic_pattern_audit.py`. Surface tag table updated. Explicit note that `restoration_packet.py` does NOT rewrite prose, claim AI provenance, or optimize metrics directly — the metric-gaming resistance lives in the targetability taxonomy.
- `.claude-plugin/marketplace.json`: plugin description extended to mention "metric-targeted restoration packets that translate diagnostic outputs into bounded revision-safe prompts," plus the calibration toolchain and voice-validation harness that landed in 1.9.0 + 1.10.0.
- `plugins/setec-voiceprint/.claude-plugin/plugin.json`: same description refresh.

### Notes on cathedral status

This commit ships cathedral upgrade #7's v1: metric-targeted restoration packets. The remaining v2 piece is an automated before/after restoration script (`scripts/before_after_restoration.py`) that reruns the diagnostics on the revised text and compares deltas; v1 makes that step manual via the post-check commands embedded in every packet.

After 1.11.0, three of the eight cathedral upgrades remain partly open:

- **#4 Impostor baselines** — still corpus-bound; no code unlock pending.
- **#6 Voice profile expansion** — `voice_drift_tracker.py`, `pov_voice_profile.py` are bounded code work, no exotic borrows.
- **#7 Before/after restoration loop** — v1 (this release) ships the packet generator + post-check workflow; v2 automation is roadmap.

## [1.10.2] - 2026-05-08

Audit `derive_seed` in `validation_harness.py` for the same `hash()` bug pattern the reviewer caught in `voice_validation_harness._stable_seed` during 1.9.0. Finding: NOT buggy. `derive_seed` uses `(i+1)*ord(ch)` accumulation, which is stable across Python processes because Unicode code points don't depend on `PYTHONHASHSEED`. Confirmed empirically (two independent Python invocations produce identical seeds). Adds documentation + a pinned-value regression test so a future "modernizer" can't silently replace the implementation with `hash((parts...))` thinking they're improving it.

### Changed

- `scripts/validation_harness.py` `derive_seed`: docstring expanded to document the cross-process-stable contract and the reasoning behind the `(i+1)*ord(ch)` choice over `hash()`. Behavior unchanged.

### Added

- `scripts/tests/test_validation_harness_seeds.py`: four regression tests pinning the cross-process-stable behavior of `derive_seed`. Pins specific output values (e.g., `derive_seed(42, "per_signal", "burstiness_B") == 29082`) so any algorithm change fails immediately. Also tests that distinct `parts` tuples produce distinct seeds (collision check ensures per-slice bootstrap RNGs stay independent), and that a `None` base seed propagates correctly (preserves the system-entropy fallback when no seed is supplied). Companion to the `voice_validation_harness._stable_seed` regression test from 1.9.0; the two harnesses use different algorithms (validation: `(i+1)*ord(ch)` accumulation, voice: SHA-256 of joined parts) but both satisfy the same cross-process-stable contract.

## [1.10.1] - 2026-05-08

Pre-registers the standards a calibration entry must meet before it lands in `COMPRESSION_HEURISTICS`. No behavior change; documentation only. The calibration toolchain shipped in 1.10.0 now has explicit selection criteria and an "in-sample calibration" epistemic-seatbelt convention recorded *before* any actual calibration run, so the first calibrated threshold (a future commit) is held to standards that pre-date the data rather than being chosen retrospectively.

### Changed

- `scripts/calibration/PROVENANCE.md`: four new sections.
  - **Selection criteria for a calibration entry.** Five gates, all pre-registered: expected polarity matches; AUC/AP not embarrassing; enough negative controls for the requested FPR (with a soft check on TPR-CI width even when `fpr_resolution` is satisfied); interpretable threshold (not "predict almost nothing"); ESL slice behaves conservatively (calibrating against `nonnative_english.csv` should not produce a more aggressive threshold than the heuristic — the ethical commitment is that ESL prose is not the failure mode the band classifier should flag).
  - **In-sample calibration.** Defines the epistemic-seatbelt phrase used in every committed provenance entry: empirical metrics are computed on the same corpus the threshold was derived from; a heldout split is roadmap; the threshold's evidentiary weight is "this value separates the two classes on this fixture under this calibration method," not "this value generalizes." The phrase lives in the JSON ledger entry's `notes`, the Markdown ledger entry's **Notes** bullet, and every calibrated-threshold CHANGELOG entry until a heldout split lands.
  - **Calibration commit shape.** Pre-registers the four-artifact diff a calibration commit produces: one `COMPRESSION_HEURISTICS` registry edit (value + provenance + provisional flipped together; the dataclass mutex enforces it), one new PROVENANCE.md section, one ledger entry appended, CHANGELOG entry + version bump (PATCH or MINOR depending on whether the new value will shift band verdicts on borderline documents).
  - **To populate this ledger** workflow updated: explicit "survey first, pick second" pattern. The previous draft used `burstiness_B` in the example calibrate command; the workflow now lists candidate signals (`burstiness_B`, `connective_density`, `fkgl_sd`, `mattr`, `mtld`, `adjacent_cosine_mean`, `adjacent_cosine_sd`) and explicitly requires the maintainer to survey several before committing the first signal that earns provenance under the criteria above.

## [1.10.0] - 2026-05-08

Per-signal threshold calibration toolchain. Steps 1-8 of `internal/SPEC_calibration_toolchain.md` v2.1, implementing the toolchain on top of the `ThresholdSpec` registry refactor that landed in 1.9.2.

### Added

- `requirements-calibration.txt` at the repo root, pinning `huggingface_hub>=0.23,<1` and `pyarrow>=14`. Calibration-only dependencies; opt-in install via `pip install -r requirements-calibration.txt`. Core `requirements.txt` is untouched. Users who don't run calibration never pay the dependency cost.
- `scripts/calibration/fetch_pangram_editlens.py`: downloads Pangram Labs' EditLens corpus from HuggingFace (`pangram/editlens_iclr`) into `ai-prose-baselines-private/editlens/`. Verifies the dataset card declares CC BY-NC-SA 4.0 (refuses to proceed if the license has drifted). Records HF revision SHA. Auto-writes `NOTICE.md` with attribution + license + redistribution prohibition. Idempotent. Supports `--split` (default `nonnative_english`, the smallest ESL slice) and `--token` (HF access token; required because the dataset is gated). Refuses gracefully when `huggingface_hub` isn't installed, with a clear pointer to `requirements-calibration.txt`.
- `scripts/calibration/editlens_to_manifest.py`: schema-discovery-first conversion of CSV/parquet labeled corpora into SETEC `corpus_manifest.jsonl` slices. `--inspect` mode prints columns + a sample row; explicit `--text-column` / `--label-column` / `--label-map` flags required unless a `--preset` matches (`editlens_nonnative`, `editlens_test`, `editlens_human_detectors` are bundled). Per-row text files spilled to a sibling directory; refuses to write outside `ai-prose-baselines-private/` unless `--allow-public-output` is passed. Reference-detector scores from each row (`fastdetectgpt_score`, `binoculars_score`, EditLens model scores, Pangram v3.2 score) are preserved in the entry's `notes` field for cross-tool comparison. Validates the output manifest via `manifest_validator.validate_manifest` before exit.
- `scripts/calibration/calibrate_thresholds.py`: direction-aware per-signal threshold sweep + provenance writer. Looks up direction (`gt`/`lt`) and dotted signal path from `COMPRESSION_HEURISTICS[signal].direction` and `.signal_path` (the registry is the single source of truth). FPR-resolution check refuses targets below `1/n_neg`. Picks the highest-TPR threshold whose empirical FPR ≤ target. Computes fixed-threshold paired-bootstrap CIs on TPR / FPR / precision at the chosen threshold (selection uncertainty / nested bootstrap is roadmap). Bootstrap seed derivation uses SHA-256 (per the 1.9.0 voice-harness fix) so reproducibility holds across processes. Writes a complete provenance entry to `scripts/calibration/thresholds_calibrated.json` including corpus name + HF revision SHA + license, calibration metrics, CI bounds, SETEC commit, command, derivation date, and a `split_role: calibration_only` tag flagging the in-sample empirical metrics.
- `scripts/calibration/PROVENANCE.md`: human-readable companion to the JSON ledger. v1 ships with no calibrated entries (the toolchain is the deliverable, not the calibrations themselves) but documents the entry format, the calibration workflow, and the legal posture. Entries land via PR as the maintainer's local calibration runs produce them.
- `scripts/calibration/thresholds_calibrated.json`: machine-readable provenance ledger. Initially `[]`; entries appended by `calibrate_thresholds.py`.
- `scripts/tests/test_calibration_provenance.py`: nine regression tests covering ledger integrity (parseability, well-formed slugs, required fields, registry↔ledger referential integrity for slug + signal_path + direction, calibrated value matches ledger derived_value), regardless of whether the private corpus is available. A tenth test re-derives each calibrated threshold via `calibrate_thresholds.derive_threshold` and asserts a match within tolerance — skipped silently when the private corpus is absent (CI-safe), runs in the maintainer's local environment.
- `validation_harness.collect_signal_records(records, signal_path)`: new helper exposing `(label, score)` paired samples for the calibrator. Refactor extracts the per-signal extraction logic that `per_signal_ranking_metrics` previously did inline; both consumers now share the same loop, guaranteeing they operate on identical paired samples (important when the calibrator's derived threshold is later checked against the harness's reported AUC).

### Changed

- `references/implementation-survey.md`: new Implementation Queue item #10 ("Per-signal threshold calibration toolchain — ✅ Shipped (1.10.0)"). Item #12 ("Larger ESL test class") now notes the unblock via `fetch_pangram_editlens.py --split nonnative_english`. Items #13 (band-threshold calibration) and #14 (directional-cluster consistency calibration) added to roadmap as separate methodology passes that build on the v1 toolchain pattern.
- `README.md` Installation section adds a "Calibration toolchain (opt-in)" paragraph noting `requirements-calibration.txt`, the local-only design, and pointers to PROVENANCE.md + the spec.

## [1.9.2] - 2026-05-08

Step 0 of the calibration toolchain (per `internal/SPEC_calibration_toolchain.md` v2.1): replace the tuple-based `COMPRESSION_HEURISTICS` registry with a `ThresholdSpec` dataclass that carries calibration metadata. Unblocks the rest of the calibration toolchain by giving each per-signal threshold a place to record its provenance slug and provisional flag.

### Changed

- `scripts/variance_audit.py`: `COMPRESSION_HEURISTICS` and `POS_BIGRAM_KL_HEURISTIC` are now `ThresholdSpec` dataclass instances instead of `(threshold, direction, weight, length_floor)` tuples. New fields: `signal_path` (the dotted audit-output path the validation harness uses for score extraction), `provenance` (slug into `scripts/calibration/PROVENANCE.md`, `None` for heuristic thresholds), `provisional` (bool; `True` for heuristic, `False` for calibrated). The registry shape is identical in semantics — every existing field is preserved — but consumers now use attribute access (`spec.value`, `spec.weight`, etc.) rather than tuple unpacking.
- Mutual-exclusion contract enforced in `ThresholdSpec.__post_init__`: a threshold cannot be both `provisional=True` and have a non-`None` `provenance` slug, and vice versa. Setting `provenance` to a slug requires clearing `provisional`. Bad direction values (anything other than `"gt"` or `"lt"`) also raise. Catches calibration-vs-heuristic confusion at registry definition time, not at output time.
- Updated all `COMPRESSION_HEURISTICS` consumers to use attribute access: `classify_compression()` (band classifier), `compare_to_baseline()` (length-floor lookup for z-score warnings), POS-bigram KL handling, and `validation_harness.py`'s `_expected_polarity_direction()` polarity check. Behavior is unchanged; the refactor is a code-shape change only.
- `classify_compression()` JSON output gains a new `calibration_status` block: `{n_calibrated, n_provisional, n_total, calibrated_signals, provisional_signals}`. Each entry in `thresholds_used` now also carries `signal_path`, `provenance`, and `provisional` fields. Backward-compatible (only new fields added; existing fields untouched).
- `format_summary()` markdown output gains a "Calibration status" footer line that reports "X of Y signal thresholds carry calibration provenance" and points users at `scripts/calibration/PROVENANCE.md`. v1 release ships with `0 of 11 ... all are heuristic` as expected; the footer flips automatically once calibrated thresholds land.

### Added

- New helpers `provisional_signals(heuristics) -> list[str]` and `calibrated_signals(heuristics) -> list[str]` partition the registry by calibration status. Used by the report renderer; will be used by `scripts/calibration/calibrate_thresholds.py` (Step 4 of the toolchain) to look up which signal paths still need calibration runs.
- `scripts/tests/test_threshold_spec.py`: nine regression tests covering the dataclass contract (default = provisional + no provenance; calibrated must declare provenance; mutex enforcement; direction validation), registry well-formedness (every entry has a non-empty signal_path, valid direction, positive length_floor + weight), `POS_BIGRAM_KL_HEURISTIC` shape, the partition invariant on the `provisional_signals` / `calibrated_signals` helpers, and a "v1 is all provisional" assertion that flips when the first calibrated threshold lands.

## [1.9.1] - 2026-05-08

Roadmap pass on cathedral upgrade #7 (before/after restoration loop): records the metric-targeted restoration packets framing as the next scoped slice.

### Changed

- `ROADMAP.md` cathedral upgrade #7 status line now names the next scoped slice ("metric-targeted restoration packets that translate diagnostic outputs into revision-safe prompt targets, then require a SETEC post-check"). New "Metric-targeted restoration packets" subsection between the adversarial-test-classes and Phase 7+ sections, with the targetability taxonomy (direct targets / translated targets / investigate-first targets / avoid-direct targeting) named so the framework's promised craft-restoration surface has a concrete v1 shape. New cross-layer architectural question added: "which diagnostic signals are safe restoration targets?" — captures that POS bigram/trigram drift is the central test case (diagnostic in raw form, revision-useful only after translation into prose moves).

## [1.9.0] - 2026-05-08

Voice-coherence validation harness. Closes the asymmetry where Surface 1 (smoothing diagnosis) had `validation_harness.py` with ROC AUC + bootstrap CIs + ESL slicing + FPR-target framing, and Surface 2 (voice coherence) had only literature anchoring (Mosteller-Wallace 1964) but no labeled-fixture validation in the repo.

### Added

- `scripts/voice_validation_harness.py`: Surface 2 sibling to `validation_harness.py`. Quantifies how well SETEC's voice-distance feature machinery discriminates same-author document pairs from different-author document pairs on a labeled fixture. Structurally different from the smoothing harness: scores PAIRS (not individual documents), labels by `same_author = (doc_a.author == doc_b.author)`, ranks pairs by per-family Burrows-Delta or cosine distance. Feature-space construction matches production: `select_feature_names` over the entire selected validation slice, `vector_stats` (column mean + SD) over the slice, then per-pair Burrows-Delta as mean absolute z-difference over informative features (sd > 0). Does NOT call `family_distance()` for pairs — that helper is baseline-oriented and a one-document baseline has zero SD on every feature. New CLI: `--manifest`, `--use voice_validation`, `--bootstrap-method {document_cluster,naive_pair}`, `--bootstrap-resamples`, `--bootstrap-confidence`, `--bootstrap-seed`, `--fpr-target`, `--label-by {author,persona}`. Module-level `TASK_SURFACE = "voice_coherence"`; importable as `voice_validation_harness.run_harness(args) -> dict` for downstream gating. Refuses to publish a single aggregate accuracy / TPR / FPR number absent an explicit `--fpr-target` operating point, matching the smoothing harness convention.
- Document-cluster bootstrap CI as the preferred uncertainty estimate: resample documents with replacement within each author stratum, deduplicate, rebuild unordered pairs over the surviving distinct documents, recompute AUC. Skips resamples that lack both same-author and different-author pairs. Treats documents (not pairs) as the unit of evidence, since pair records are dependent — each document appears in multiple pairs. The naive paired-record bootstrap is still available via `--bootstrap-method naive_pair` and is labeled in JSON output with a note that pair dependence makes the interval smoke-test-only.
- Per-family ranking table: AUC + AP + bootstrap CI + n_pairs + polarity check ("OK" if AUC ≥ 0.5 in the expected direction; "INVERTED" if not) for each (family, metric) pair across `function_words`, `char_ngrams_3/4/5`, `pos_trigrams`, `dependency_ngrams`, `punctuation`, `paragraph_dialogue`, `pronoun_modal_negation`. Optional weighted-family aggregate row using `FAMILY_WEIGHTS` and `OVERALL_FAMILY_DELTA_CAP` matching production `voice_distance.py`'s overall-score shape.
- `scripts/test_data/federalist_voice_validation_manifest.jsonl`: smoke fixture pointing at the existing public-domain Federalist Papers fixture. Six entries (3 Hamilton + 3 Madison), all `public_domain`, all `pre_ai_human`, all `native`, all `register: policy_advocacy`. Hamilton vs. Madison is the canonical Mosteller-Wallace voice-attribution benchmark. Six docs → 15 unordered pairs (6 same-author, 9 different-author). On this tiny fixture the smoke values are: function-word Burrows-Delta AUC ≈ 0.65, function-word cosine AUC ≈ 0.81. These are smoke regression values, not calibration claims; the fixture is too small for a calibration study.
- `scripts/tests/test_voice_validation_harness.py`: six regression tests covering the smoke run, pair-label correctness, function-word AUC tolerance band against the documented smoke values, refusal-of-aggregate-accuracy claim license, operating-point appearance under `--fpr-target`, and `manifest_validator.ALLOWED_USE` round-trip.

### Changed

- `manifest_validator.ALLOWED_USE` extended with `voice_validation`. The new value coexists with `validation` (which routes to the smoothing harness) so a single manifest entry can be tagged `use: ["voice_validation", "validation"]` if it serves both surfaces.
- `references/implementation-survey.md` Implementation Queue item #9 ("Voice-coherence validation harness — Surface 2 sibling to `validation_harness.py`") moves from Roadmap to Shipped.

## [1.8.2] - 2026-05-08

Followup doc fix to 1.8.1: the generated comparison report's Phase A' description still said "same per-doc renormalization within the top-K subset," which is the opposite of what 1.8.1 fixed. The implementation was correct but the report description contradicted it.

### Fixed

- `scripts/oracle/compare.py` Phase A' description text in `render_freq_table_phase_block` now says "full-family relative frequencies preserved (no selected-subset renormalization, matching production `stylometry_core.py`)" and notes that "Row sums are typically < 1.0." The previous phrasing was a leftover from the pre-1.8.1 oracle and contradicted the fix that 1.8.1 actually shipped.
- `scripts/oracle/results/oracle_comparison_report.md` regenerated with the corrected Phase A' description. Numerical content unchanged (Phase A and Phase A' still report Pearson 1.0, mean |Δ| 0.0 across all six feature families).

## [1.8.1] - 2026-05-08

Oracle frequency-table denominator fix: the oracle now exports production-shaped selected-feature vectors instead of selected-subset-renormalized vectors. The Phase A agreement with R `stylo` was previously verifying the math on an altered table whose denominators didn't match production; the fix realigns the oracle with `stylometry_core.py`'s actual feature space.

### Fixed

- `scripts/oracle/setec_to_stylo.py` `char_ngram_table()`, `pos_trigram_table()`, and `dep_ngram_table()` no longer renormalize each document's selected feature vector by the selected-subset total. The exported value for each selected feature is now its full-family relative frequency — the same denominator `stylometry_core.char_ngram_features` / `pos_trigram_features` / `dependency_ngram_features` produces internally before selection. Row sums are typically < 1.0 (the mass not captured by the top-K is the share of features outside the selection); earlier versions divided by the subset total so rows summed to 1.0, which produced an internally-consistent but non-production table. The bug existed in `char_ngram_table()` since it shipped in 1.7.0, and was reproduced in `pos_trigram_table()` / `dep_ngram_table()` when those landed in 1.8.0; this commit fixes all three. Reproduction recorded in `internal/SPEC_oracle_frequency_table_denominator_fix.md`: production `pos:ADP-DET-NOUN` for the first Federalist document was 0.045188, oracle was 0.054225 (8% drift); after the fix, both equal 0.045188.
- `scripts/oracle/run_stylo.R` `build_corpus_table()` no longer divides each row of the selected-feature matrix by the row total. The exported frequencies are preserved exactly from the input full-family-normalized per-document vectors, matching the SETEC-side fix.
- All committed oracle CSVs regenerated with the fixed denominators: `setec_char{3,4,5}_freqs.csv`, `setec_distances_char{3,4,5}.csv`, `setec_pos_trigram_freqs.csv`, `setec_dep_ngram_freqs.csv`, `setec_distances_pos_trigrams.csv`, `setec_distances_dep_ngrams.csv`, `stylo_pos_trigram_freqs.csv`, `stylo_dep_ngram_freqs.csv`, and the corresponding `stylo_distances_phase_a_*` files. Function-word outputs unchanged (the function-word path uses a fixed wordlist with no top-K selection so was unaffected). The comparison report content is unchanged because Phase A and Phase A' agreement remain at perfect (Pearson 1.0, mean |Δ| 0.0) on the production-shaped tables — the fix changes *what is being verified*, not the *answer*.
- Documentation in `references/stylometry-oracle.md` updated to remove "rows sum to 1.0" framing for selected top-K tables and to reframe Phase A and Phase A' as verifying production-shaped selected-feature vectors with full-family denominators preserved.

### Added

- `scripts/tests/test_oracle_frequency_tables.py`: regression tests guarding against the renormalization sneaking back in. Four tests: per-family (char-ngrams, POS-trigrams, dep-n-grams) verify that exported oracle values equal full-family relative frequencies and at least one row sum is < 1.0; a fourth test compares the committed `setec_*_freqs.csv` against `stylo_*_freqs.csv` cell-by-cell to verify the Phase A' acceptance condition without requiring R/stylo at test time.

## [1.8.0] - 2026-05-08

POS-trigram and dependency-n-gram oracle pass against R `stylo`. Closes the last footnote on cross-tool stylometric verification: all six feature families that `voice_distance.py` reports are now oracle-verified at floating-point precision.

### Added

- POS-trigram and dependency-n-gram oracle pass extending `scripts/oracle/`. The function-word oracle and the per-n char-n-gram oracle pass (1.4.0 + 1.7.0) verified SETEC's Burrows-Delta + cosine math on those four feature spaces; this extension does the same for the two spaCy-derived families. Because stylo doesn't natively do POS or dependency parsing, spaCy is the parser of record on both sides: `setec_to_stylo.py` writes per-document parse TSVs to `scripts/oracle/results/parses/<doc_id>.tsv`, and `run_stylo.R` reads them to do its own independent n-gramming. Three checks per family: Phase A (distance correctness on SETEC's frequency table) — both Burrows-Delta and cosine match to floating-point precision (Pearson 1.0, mean |Δ| ≈ 2e-9); Phase A' (frequency-table reconstruction from identical parses) — bit-exact match cell-by-cell (1800 cells, zero setec-only feats, zero stylo-only feats, mean |Δ| = 0.00). The Phase A' result confirms SETEC's `pos_trigram_features` / `dependency_ngram_features` + selection + normalization code paths match a from-scratch reimplementation; the only remaining unverified component is the spaCy parse itself, which is the parser of record on both sides. New SETEC-side helpers `parse_documents`, `write_parse_tsvs`, `pos_trigram_table`, `dep_ngram_table` mirror the existing char-ngram pattern. New R-side helpers `build_pos_trigrams`, `build_dep_ngrams`, `build_corpus_table` reimplement n-gram window construction independently. New `compare.py` helper `render_freq_table_phase_block` compares wide-format frequency tables cell-by-cell. POS / dep pass requires spaCy in the runtime; without spaCy, those exports are skipped with a notice and the rest of the oracle still runs.
- Rolling-window Delta oracle blocker recorded in `references/stylometry-oracle.md`: `stylo::rolling.delta` exposes only four parameters (`gui`, `path`, `primary.corpus.dir`, `secondary.corpus.dir`); window controls (`text.slice.length`, `text.slice.overlap`, `mfw`, `distance.measure`) are baked into the function body as local defaults; `config.txt` override hangs the R process under the conditions tested. Recommended next step if rolling-window verification becomes load-bearing: SETEC-internal pytest contract test rather than cross-tool oracle, since `stylo::rolling.delta`'s API was never going to provide a clean cross-tool reference at this surface.

### Changed

- `references/stylometry-oracle.md` results table extended from four feature spaces to six. Phase A' results table added for POS-trigrams and dep-n-grams. Methodology section reframed from "two complementary phases" to "three complementary phases" (A, A', B) reflecting Phase A''s addition for the spaCy-parsed families.

## [1.7.1] - 2026-05-08

Documentation pass on the Cowork install / update flow with empirical cache findings.

### Changed

- `README.md` Plugin install section for Cowork rewritten. The marketplace path (re-add `anotherpanacea-eng/setec-voiceprint` through Cowork's Plugins UI) is documented as the recommended install and the only path that supports updates. The `--plugin-dir` path is documented as a one-time snapshot: empirical testing on 2026-05-08 found that `git pull` on a `--plugin-dir`-installed local checkout does NOT propagate updates to the running Cowork install even after a version bump and a Cowork restart, with the cache located at `~/Library/Application Support/Claude/local-agent-mode-sessions/<session>/rpm/plugin_<id>/`. This is stronger than the previous catch-up commit's claim (which said only that content changes within an unchanged version field don't invalidate). Working remediation is to remove the `--plugin-dir` install and re-add via the marketplace path. Diagnostic command for users hitting the symptom is included.

## [1.7.0] - 2026-05-08

Char-n-gram correctness pass against R `stylo`, extending the function-word oracle to all three per-n character n-gram families.

### Added

- Char-n-gram oracle pass extending `scripts/oracle/`. The function-word oracle that closed issue #4 verified SETEC's Burrows-Delta + cosine math against stylo on the function-word feature space; this extension does the same for all three of SETEC's per-n character n-gram families (char-3-grams, char-4-grams, char-5-grams) using the top-200 corpus-derived char-ngrams per n. Phase A result on the Federalist fixture: SETEC matches stylo to floating-point precision (Pearson 1.0, Mean |Δ| = 0) for Burrows-Delta and cosine distance on each per-n table independently. Confirms SETEC's per-n separation design (introduced in commit `88c6073` to fix the prior unified-family char-ngram bug) is internally consistent — each per-n family behaves like a standalone Burrows-Delta input the way stylo expects. New helper `setec_to_stylo.char_ngram_table(docs, n)` exports per-n top-K corpus-derived char-ngram frequency tables; `run_stylo.R` reads each and runs `stylo::dist.delta` / `dist.cosine` per n; `compare.py` surfaces the four feature-space results (function words + char-3 + char-4 + char-5) in the comparison report. Char-n-gram Phase B (stylo's own char-ngram tokenization vs. SETEC's) is roadmap. POS-trigram and dependency-n-gram oracle passes are also roadmap (would need a different reference since stylo doesn't natively do POS or dependency parsing).

## [1.6.0] - 2026-05-08

Idiolect detector, content-level corpus-hygiene gate wired into the validation harness, adversarial Unicode-layer fixtures.

### Added

- `scripts/idiolect_detector.py`: keyness/collocation extractor for voice preservation. Scores 1/2/3-grams against a reference corpus, reports idiolectic and anti-idiolectic candidates, emits a quota-balanced "do not normalize" preservation list, shares corpus-hygiene preprocessing with the rest of the pipeline, and applies voice-cloning-grade output privacy guards.
- `scripts/adversarial_fixtures.py` and `scripts/test_data/adversarial/`: deterministic Unicode-layer validation stress fixtures derived from the bundled AI smoke sample (zero-width spaces, Cyrillic homoglyphs, soft hyphens). Manifest entries carry `adversarial_class`, `source_id`, and `transform`; `manifest_validator.py` summarizes adversarial classes and `validation_harness.py` reports a `by_adversarial_class` slice.
- `language_status` field on `corpus_manifest.jsonl` entries (`native | non_native_advanced | non_native_intermediate | learner | unknown`). `manifest_validator.py` warns when entries with non-native language status land in `use: baseline`, `use: voice_profile`, or `use: idiolect`, because ESL prose sits in the same low-variance region of stylometric space as RLHF-aligned LLM output (Liang et al., *Patterns* 2023, 61% average FPR on TOEFL essays across seven detectors). Validator summary block now reports `by_language_status` counts.

### Changed

- `validation_harness.py` can now run the content-level corpus hygiene gate with `--check-corpus`. The harness validates the manifest, selects the entries under `--use`, runs `check_corpus_paths()` with the same strip-rule configuration, fails fast if contamination exceeds `--corpus-fail-threshold` (default 0.05), and includes a `corpus_hygiene` block in JSON/Markdown.

## [1.5.2] - 2026-05-08

Documentation pass: license-compatibility section added to the implementation survey, implementation queue refreshed.

### Changed

- `references/implementation-survey.md`: new license-compatibility section enumerating the licenses of each external package SETEC adopts or treats as a reference implementation, with notes on GPL-compatible vs. permissive trade-offs for downstream redistribution. Implementation queue refreshed against current code state.

## [1.5.1] - 2026-05-08

Surface-tag chore fix on `aic_pattern_audit.py`.

### Fixed

- `scripts/aic_pattern_audit.py` surface tag aligned with the rest of the smoothing-diagnosis surface.

## [1.5.0] - 2026-05-08

Layer B/C named-pattern density audit. The framework's first scriptable Layer B/C tool.

### Added

- `scripts/aic_pattern_audit.py`: counts the named rhetorical patterns from `references/aic-flags.md` and `references/source-triage.md` in a target document, reports per-thousand-word density, and (with `--baseline-dir`) compares against a baseline corpus to flag patterns whose density exceeds the writer's voice envelope. Patterns covered: negation hedge, disguised correctio, pseudo-aphorism, manifesto cadence, triplet, professional-parallel stack, plus the four regex-tractable nonfiction parallel patterns (false-balance, hedge-and-affirm, recommendation template, authority laundering). Markdown blockquote stripping is on by default (`--keep-quotes` to disable). Layer C earned/unearned verdicts remain the writer's call per instance; the script surfaces candidates and density. Two patterns deferred to v2 because they need NER + abstractness scoring or context analysis: abstraction shielding and indefinite-pronoun gesture. Known v1 limitation: the disguised-correctio detector matches only the explicit `not X, but Y` inline form and the `It is not X. It is Y` frame; multi-sentence correctios are deferred to a sentence-pair scanner using spaCy's dependency parse.

## [1.4.0] - 2026-05-08

Stylometry oracle test harness with R `stylo` and the public-domain Federalist Papers fixture corpus. Closes #4.

### Added

- Stylometry oracle test harness: `scripts/oracle/setec_to_stylo.py` + `scripts/oracle/run_stylo.R` + `scripts/oracle/compare.py`, plus the public-domain Federalist Papers fixture corpus at `scripts/test_data/federalist_oracle/` (six papers from Project Gutenberg eBook #18: 3 Hamilton, 3 Madison, ~13,700 words). Two-phase comparison: Phase A tests distance correctness on identical input (SETEC's Burrows-Delta and cosine distance vs. R `stylo`'s `dist.delta` and `dist.cosine` on the same frequency table); Phase B tests end-to-end agreement on raw text where each side does its own tokenization and feature selection. Phase A: SETEC matches stylo to floating-point precision on both metrics (Pearson 1.0, Mean |Δ| = 0). Phase B: cosine Spearman 0.97 (feature-set choice barely shifts cosine ranking), Burrows-Delta Spearman 0.65 (the fixed-list-vs-corpus-derived-MFW design choice meaningfully shifts the L1-z-score Delta ranking). One bug surfaced and fixed in the oracle harness during the test: the initial draft averaged Burrows-Delta over all features in the fixed wordlist (including constant-zero columns), producing a systematic factor-of-(n_informative / n_total) underestimate vs. stylo's informative-features-only mean; the production `stylometry_core.family_distance` was already correct (only accumulates abs(z) when sd > 0), so the discovery confirmed the production path. The full methodology and divergence catalog lives in `references/stylometry-oracle.md`. R remains optional: the comparison is run-once-when-validating, output CSVs are committed alongside the report, no R install required to read it.

## [1.3.0] - 2026-05-08

Per-signal AUC table for the smoothing-diagnosis validation harness.

### Added

- Per-signal AUC table in `scripts/validation_harness.py`. The harness now reports ROC AUC + average precision + paired bootstrap CIs for each of the 13 Layer A signals independently, in addition to the aggregate `compression_fraction` ranking. Signal scores are extracted at scoring time into `record["per_signal_scores"]`; `per_signal_ranking_metrics()` loops over `_SIGNAL_PATHS`, builds a `(label, signal_value)` paired sample per signal, and runs the existing rank-based metrics + paired bootstrap. Output appears under `slices.overall.per_signal_ranking` in JSON and as a "Per-Signal Discrimination" section in the markdown report. Per-signal CIs are computed only on the overall slice (per-slice per-signal would explode report size and slice samples are typically too small for stable per-signal CIs). Each signal's polarity is checked against `COMPRESSION_HEURISTICS`'s expected direction (`gt` for signals like `yules_k`/`connective_density`/`function_word_ratio` that rise under compression; `lt` for variance signals like `mattr`/`mtld`/`fkgl_sd`/`burstiness_B` that fall); the harness labels each signal as "matches expected direction" or "does NOT match expected direction" so calibration drift or polarity inversion is visible at a glance. Addresses the "which signals are actually carrying the discrimination on this corpus" diagnostic gap that aggregate ranking metrics hide.

## [1.2.0] - 2026-05-08

Per-bigram POS-bigram KL decomposition. Pair of scripts surfacing which specific syntactic templates drive an elevated KL.

### Added

- `scripts/bigram_diff.py` and `scripts/manuscript_bigram_diff.py`: per-bigram POS-bigram KL decomposition. `bigram_diff.py` compares one target document against a cluster of comparator files; `manuscript_bigram_diff.py` compares two corpora at the aggregate level. Both decompose `variance_audit.py`'s aggregate POS-bigram KL into ranked per-bigram contributions, surfacing which specific syntactic templates drive an elevated KL number. Cluster aggregation toggles between pooled counts (long files dominate) and per-file mean (each file weighted equally); default `both` reports side-by-side. Markdown output includes two ranked tables per mode (over-represented and under-represented in target/corpus A) with KL contribution, log₂ ratio, raw probabilities, and example token pairs. JSON output preserves the same fields with `task_surface: smoothing_diagnosis`. Cache machinery in `parse_cluster_files` parses each file once even when running both aggregation modes.
- New helpers in `scripts/variance_audit.py`: `normalize_pos_bigram_counts(counts, keys=None, *, alpha=0.0)` returns Lidstone add-α normalized probabilities; `pos_bigram_kl_contributions(target_probs, baseline_probs, *, target_counts=None, baseline_counts=None, eps=1e-9, min_count=1)` decomposes KL into per-bigram contributions sorted by `abs(kl_contrib)` descending. Both reused by the new bigram-diff scripts.

## [1.1.0] - 2026-05-08

Corpus-hygiene preprocessor wired into Layer A and the validation harness. Catches CSS / HTML / JS / code contamination that previously inflated POS-bigram KL by ~4× against register-matched baselines.

### Added

- `scripts/preprocessing.py`: shared corpus-hygiene preprocessor for `variance_audit.py` and `stylometry_core.py`. Strips suspected non-prose (HTML/CSS/JS scaffolding, Markdown code, loose CSS blocks, conservative HTML tags, JSON-shaped blocks, ASCII tables, YAML front matter) before tokenization and POS-tagging; exposes per-rule token accounting and baseline per-file metadata.
- `scripts/check_corpus.py`: standalone content-level corpus hygiene gate. Runs the shared preprocessing detector over files, directories, or manifest-selected slices; reports stripped-token ratios, dominant stripping rules, and per-file clean/warning/fail status; exits nonzero when contamination exceeds the configured fail threshold or any file cannot be read. Importable as `check_corpus_paths()` for future validation-harness gating.
- `scripts/test_data/preprocessing/css_contaminated_fixture.md`, `scripts/test_data/preprocessing/css_contaminated_fixture_clean.md`, and `scripts/test_data/clean_baseline/`: public synthetic CSS-contamination fixture plus regression coverage for the failure mode where embedded CSS scaffolding inflates POS-bigram KL by ~4× against a register-matched baseline.
- POS-bigram KL band integration: the band classifier in `variance_audit.py` now reads the POS-bigram KL/JSD divergence as a contributing signal alongside the eleven Layer A variance signals, with its own length-floor and weight in `COMPRESSION_HEURISTICS`.

### Changed

- POS-bigram KL and all Layer A text statistics in `variance_audit.py` are computed on preprocessed text by default. Baseline files receive the same preprocessing as the target; `--allow-non-prose` records an explicit opt-out in JSON for users intentionally auditing markup-heavy material. Voice-coherence feature extraction inherits the same preprocessing through `stylometry_core.py`.

## [1.0.0] - 2026-05-07

Initial Cowork plugin release. Packages the SETEC stylometric framework as a Claude Code / Cowork plugin with four task-surface skills. Bundles the development burst that brought the framework from "MVP plus voiceprint" to "validation-spine prerequisites in place."

### Added

- Plugin packaging for Claude Code CLI / Desktop and the Cowork SDK harness. New `.claude-plugin/marketplace.json` declares the marketplace catalog with one plugin entry; new `plugins/setec-voiceprint/.claude-plugin/plugin.json` declares the plugin manifest at version 1.0.0. Four `SKILL.md` files at `plugins/setec-voiceprint/skills/{smoothing-diagnosis,voice-coherence,validation,craft-restoration}/` map one-to-one onto the framework's four task surfaces; each skill's `description` field carries trigger phrases for model-driven invocation, and each script-wrapping skill documents CLI usage with `${CLAUDE_PLUGIN_ROOT}/../../scripts/...` paths so invocations work regardless of where the repo is cloned. README installation section now distinguishes the Claude Code CLI/Desktop install path (`claude plugin marketplace add` + marketplace-driven update flow) from the Cowork harness install path (`--plugin-dir` against a local checkout, `git pull` + new session for updates). The version field lives only in `plugin.json` (not duplicated in the marketplace plugin entry) so resolution priority is unambiguous, and only the canonical `.claude-plugin/marketplace.json` ships (no root-level marketplace.json duplicate) to avoid split-brain when only one of two catalog files gets bumped.
- `scripts/manifest_validator.py`: schema and integrity checks for `corpus_manifest.jsonl`. Per-entry checks (required fields, enum-valued fields, `use` is a list, `word_count` non-negative, unknown field names flagged), cross-entry checks (duplicate `id`, missing-on-disk path, two-ids-one-file, `use: validation` + `split: baseline` contradiction, `use: voice_profile` privacy ratchet, provenance contradictions). JSON output, markdown report, importable `validate_manifest(path) -> dict` for downstream gating.
- `scripts/manuscript_repetition_audit.py`: manuscript-aggregate vocabulary audit. Composes the chapter splitters from `manuscript_audit.py` with the per-document scorer from `repetition_audit.py`. Output: dispersed habit-vocabulary table (words flagged in many chapters at moderate ratio), concentrated repetition table (one or two chapters at high peak ratio), per-chapter top-N. JSON preserves per-chapter and aggregated structures separately.
- `scripts/chapter_distinctiveness_audit.py`: leave-one-out internal-baseline vocabulary audit. For each chapter, baseline is the union of all other chapters; surfaces words distinctive to one chapter rather than habit-vocabulary dispersed across the manuscript. Default `--min-ratio 1.5` because "distinctive" is a stronger claim than "barely over-represented."
- Sliding-window mode in `scripts/variance_audit.py`. New `split_into_windows()`, `audit_windows()`, and `format_windows_dashboard()` plus three CLI flags (`--window-size`, `--window-stride`, `--window-only`). Catches localized compression that whole-document scores would mask: a synthetic document combining clean and AI-flavored prose averages to "Lightly smoothed" at whole scope; the window scan correctly localizes the compression to the AI-flavored sections.
- POS-bigram KL/JSD divergence against baseline aggregate, in `scripts/variance_audit.py`. New `pos_bigram_distance()` helper with Laplace smoothing on the union of bigrams; new `compare_distributions()` entry point keeps the existing `compare_to_baseline()` z-score path unchanged.
- Feature-cluster mode for `scripts/voice_distance.py`. New `FUNCTION_WORD_CLUSTERS` registry (26 predefined syntactic groupings), new `compute_clusters()` aggregator reporting mean signed z, direction consistency, and top contributors per cluster. Catches authorial fingerprints that single-feature top-N misses when a cluster of related features moves together at moderate magnitudes.
- Genre tolerance quick-reference table in `references/aic-flags.md`. 7×6 grid (seven AIC flags by six genres) with three tolerance bands (Low, Med, High) plus N/A and six footnotes for cells where a single band misrepresents the call (AIC-1 in testimony; AIC-2 in testimony; AIC-7 in essay/testimony; AIC-3 in blog; AIC-7 in blog; AIC-3 in testimony).
- `task_surface` field on every script's JSON output and markdown header. Values: `smoothing_diagnosis` (variance/manuscript/repetition audits), `voice_coherence` (voice_distance, voice_profile, idiolect_detector), `validation` (manifest_validator, validation_harness). Each script exports a module-level `TASK_SURFACE` constant for downstream importers.
- Per-n character n-gram families (`char_ngrams_3`, `char_ngrams_4`, `char_ngrams_5`) in `scripts/stylometry_core.py`. Each family normalizes within its own n, has its own selection cap, and contributes its own Burrows-Delta and cosine distance. Replaces the prior unified family that mixed all three n-values in one frequency space.
- `references/implementation-survey.md`: borrow-before-building survey for validation, bootstrap, stylometry-oracle, idiolect, and manifest-schema work. Records which external packages should become dependencies, which should remain reference implementations, and which SETEC-specific responsibilities stay local.
- Final license texts. `LICENSE` carries the canonical GNU GPL v3 text governing code (`GPL-3.0-or-later`); `LICENSE-docs` carries the canonical Creative Commons Attribution-ShareAlike 4.0 International text governing documentation and reference prose (`CC-BY-SA-4.0`); `NOTICE` enumerates which files each license governs and confirms that personal baseline corpora and generated voice profiles fall outside the repository's licensed scope.
- `requirements.txt`: declares `spacy>=3.7,<4`, `scipy>=1.11`, `scikit-learn>=1.3`, `statsmodels>=0.14`, and `nltk>=3.8` as runtime dependencies, with `sentence-transformers` and `textstat` listed as commented optional extras for calibrated cohesion and tightened FKGL. Replaces the scattered `pip install` snippets in the README's Installation section.
- `scripts/length_bootstrap.py` and `--bootstrap` flag in `variance_audit.py`. Phase 1 step 3 of the validation spine. For each Layer A signal, the bootstrap samples random length-matched word-slice windows from each baseline file, pools the per-window statistic values into an empirical distribution at the target's word length, reports the target's mid-rank percentile in that distribution, and uses `scipy.stats.bootstrap` to put a BCa confidence interval on the percentile. Replaces noisy z-scores at small N. Flags: `--bootstrap-windows-per-file` (default 50), `--bootstrap-max-windows` (default 500), `--bootstrap-resamples` (default 9999), `--bootstrap-confidence` (default 0.95), `--bootstrap-seed`. Output appears under `baseline_bootstrap` in JSON and as a "Length-matched bootstrap" section in markdown.
- `scripts/validation_harness.py`: MVP validation harness for the `smoothing_diagnosis` surface. Reads a validated manifest, runs `variance_audit` scoring on entries tagged `use: validation`, reports ROC AUC / average precision with paired bootstrap CIs when both classes are present, and reports thresholded FPR/TPR/FNR/precision only when an explicit `--fpr-target` operating point is supplied. Slices output by register, length bucket, language status, and AI status; includes claim-license language refusing individual-document provenance verdicts and single aggregate accuracy. Defaults leave `mixed` outside the binary label frame unless explicitly mapped.
- `scripts/test_data/validation_smoke_manifest.jsonl`: public smoke fixture for `validation_harness.py`, pointing at the bundled capybara human sample and AI smoke sample.

### Changed

- `references/implementation-survey.md` rewritten against actual code state. spaCy and scikit-learn now appear under Adopt As Dependencies (both already imported by `variance_audit.py` and `stylometry_core.py` as optional, now formally adopted via `requirements.txt`); SciPy moved from "future bootstrap dep" to current runtime requirement; NLTK is now the optional-reference-corpus path for `idiolect_detector.py`'s Brown corpus mode. New sections: ESL handling, adversarial test classes for the validation harness, 0.01% FPR target framing, and a Phase 7+ horizon item for local-LLM cross-perplexity.
- `ROADMAP.md` updated to enumerate ESL handling, adversarial test classes (paraphrase / humanizer / Unicode-layer), the 0.01% FPR target as the recommended deployment threshold for accusation-grade settings, and a Phase 7+ horizon for a local-LLM cross-perplexity sibling tool. The harness step now explicitly slices by `language_status` and refuses to publish a single aggregate accuracy number absent a stated FPR target.
- `README.md` Installation section now points to `requirements.txt` and documents the `python -m spacy download en_core_web_sm` step explicitly, replacing the prior scattered Tier 1 / Tier 2 / Tier 3 pip snippets.
- Band classifier in `classify_compression()` now reports `compression_fraction = weighted_score / available_weight` and thresholds the fraction (< 0.15 / < 0.40 / >= 0.40), not the absolute weighted score. New `Insufficient signal` band for documents below all length floors. Previously such documents falsely classified as "Lightly smoothed."
- Baseline z-score output in `compare_to_baseline()` carries `length_floor`, `length_floor_satisfied`, and a warning string when the target is below the heuristic's floor. Markdown output marks unreliable rows with `[!]`.
- Default `--min-ratio` for `chapter_distinctiveness_audit.py` raised from 1.0 to 1.5. The leave-one-out baseline can drag down ratios for habit-vocabulary that some chapters omit; "distinctive" is a stronger claim than "barely over-represented."
- Default `--char-top` in `voice_distance.py` and `voice_profile.py` lowered from 500 to 200. Semantic also changed: now per-n cap rather than total cap across all three n-values.
- Cluster registry pruned from 27 to 26 families. Dropped `modals_volitional` (singleton "will" never fired under the cluster floor); dropped `more`/`most` from `comparison` (they overlapped `quantifiers` and read as a duplicate lens).
- Cluster `direction` label now derives from majority sign of feature deviations, not from the mean signed z. Prevents the directional flag and direction label from contradicting when one large outlier of opposite sign overwhelms several smaller features pulling the same way.
- Dispersed-habit sort key in `manuscript_repetition_audit.py` changed from `(n_chapters, mean_ratio)` to `(n_chapters, median_ratio)`. Median resists single-spike inflation.

### Fixed

- `voice_distance.py` now drops the target file from baseline entries when the same path appears in `--baseline-dir`. Previously the target self-normalized the score (cosine min collapsing to 0.0).
- `manuscript_repetition_audit.py` and `repetition_audit.py` now refuse zero-token baselines (raises `BaselineError`); surface skipped baseline files with a stderr warning rather than silently dropping them; expose `baseline_files_loaded`, `baseline_files_skipped`, and `baseline_tokens` in JSON output.
- `manifest_validator.py` rejects directory paths after path resolution (uses `is_file()` instead of `exists()`); the voiceprint privacy ratchet now warns on `voice_profile` and `idiolect` entries with missing or non-string `privacy` values, not just non-`'private'` strings.
- `repetition_audit.py` and `manuscript_repetition_audit.py` apply a `min_ratio` floor to candidate scoring (default 1.0). The previous behavior admitted under-represented words (ratio < 1.0) into the candidate list; downstream aggregators treated them as habit-vocabulary candidates.
- `variance_audit.py` POS-bigram metric documentation and computation now match. The reference doc described KL divergence; the script previously computed only entropy of the target. Both are now produced when a baseline is supplied.
- `variance_audit.py` function-word reference doc now points readers to `voice_distance.py` for the actual Burrows-style and Cosine Delta computation. Layer A reports only `function_word_ratio` as advertised.
- README length-floor table now matches `COMPRESSION_HEURISTICS` for all 11 signals (Burstiness B 200, Shannon entropy 2000, Sentence-length SD 5000 corrected from prior stale values).
- Genre tolerance table internal contradictions resolved. Three cells (AIC-3 blog, AIC-7 blog, AIC-3 testimony) now use `Mixed` with footnotes splitting the tolerance by subtype rather than the single-band labels that contradicted the explanatory prose.

[Unreleased]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.14.3...HEAD
[1.14.3]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.14.2...v1.14.3
[1.14.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.14.1...v1.14.2
[1.14.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.14.0...v1.14.1
[1.14.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.13.0...v1.14.0
[1.13.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.12.1...v1.13.0
[1.12.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.12.0...v1.12.1
[1.12.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.11.0...v1.12.0
[1.11.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.10.2...v1.11.0
[1.10.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.10.1...v1.10.2
[1.10.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.10.0...v1.10.1
[1.10.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.9.2...v1.10.0
[1.9.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.9.1...v1.9.2
[1.9.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.9.0...v1.9.1
[1.9.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.8.2...v1.9.0
[1.8.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.8.1...v1.8.2
[1.8.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.8.0...v1.8.1
[1.8.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.7.1...v1.8.0
[1.7.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.5.2...v1.6.0
[1.5.2]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.5.1...v1.5.2
[1.5.1]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/anotherpanacea-eng/setec-voiceprint/releases/tag/v1.0.0
