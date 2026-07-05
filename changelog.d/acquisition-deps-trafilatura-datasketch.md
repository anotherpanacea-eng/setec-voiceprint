### Added

**trafilatura primary HTML extraction + MinHash-LSH near-duplicate dedup for the
acquisition tier.** Two acquisition upgrades, each an *optional* dep with graceful
degradation so base imports stay pure.

- **trafilatura (Apache-2.0) as the primary main-content extractor.**
  `acquisition_core.extract_main_content(html, content_selector, strip_selectors,
  prefer_trafilatura=True)` returns `(text, title)` — the same contract as the
  existing `html_to_text` — but tries trafilatura's model-free readability
  heuristics first (boilerplate / nav / comment stripping, main-article
  detection), falling back to the BeautifulSoup path when trafilatura is absent,
  disabled, or returns nothing. Caller-provided `strip_selectors` are pre-stripped
  before trafilatura runs, so site-specific apparatus inside the main article is
  dropped and the primary path keeps the selector-path cleanliness.
  `acquire_blog.extract_post_body` now routes through it. trafilatura is
  lazy-imported and registered OPTIONAL within the acquisition tier
  (`requirements-acquisition.txt`, `dependency_check.py`).

- **`near_dup_dedup` — MinHash-LSH cross-source near-duplicate dedup (id:
  `near_dup_dedup`).** A new, opt-in acquisition capability (datasketch, MIT) that
  runs across a staged JSONL manifest *before* it is committed and drops
  near-identical reposts / reprints / cross-source pulls the exact SHA-256 guard
  (`content_hash_already_present`) cannot see. Documents are word-shingled, hashed
  into MinHash signatures, LSH-bucketed for a Jaccard threshold, candidate pairs
  confirmed by estimated Jaccard, unioned into clusters, and one deterministic
  representative kept per cluster (longest text, then lowest id). Exposed as
  `near_dup_dedup.dedup_records` / `dedup_manifest` and a `near_dup_dedup.py` CLI.
  datasketch (and its numpy/scipy transitive deps) is lazy-imported — base
  `import near_dup_dedup` stays clean; the pass raises a clear RuntimeError naming
  `requirements-acquisition.txt` when the dep is absent.
