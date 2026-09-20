# Open Grants to Zenodo candidate source list

`build_opengrants_zenodo_source_list.py` reads regular `_grants/*.md` Git blobs from
one full Open Grants commit SHA and Zenodo **record metadata**. It emits a sorted
four-field JSONL feed (`candidate_id`, `url`, `title`, `author`) plus a sidecar.
The URL is the metadata-reported `files[].links.self` content locator. This
helper never requests that locator or reads a PDF. A later, separately reviewed
`acquire_pdf_urls` run is responsible for fetching bytes, computing their
SHA-256, extracting text, and deciding whether a candidate is usable.

```text
python3 plugins/setec-voiceprint/scripts/acquisition_sources/build_opengrants_zenodo_source_list.py \
  --catalogue-repo /local/ogrants --source-commit FULL40SHA \
  --metadata-dir zenodo-record-metadata --year-min 2013 --year-max 2021 \
  --output opengrants-candidates.jsonl \
  --sidecar opengrants-candidates.sidecar.json
```

The local checkout must claim `weecology/ogrants` as `remote.origin.url`.
The helper reads Git objects at the exact commit; worktree edits are irrelevant.
Cached mode is the default and reads `zenodo-record-N.json` for each selected
record N. It has no network fallback. `--zenodo-mode live` explicitly requests
only `https://zenodo.org/api/records/N`, with redirects disabled, and atomically
caches each exact JSON response. Neither mode requests PDF content.

Source years are inclusive annual selection bounds, not writing dates. Feed
rows have no `date` or `artifact_profile`; Zenodo publication and creation
dates stay in the sidecar. `acquire_pdf_urls --since/--until` do not filter
these undated feed rows. The raw access and license assertions, including
missing-versus-null flags, are review evidence and do not confer rights to
external files. A record with two PDF keys yields two role-free candidates.

Valid no-link and non-Zenodo rows are counted as out of scope. A list of
multiple nonempty non-Zenodo strings is also out of scope. A multi-link row
containing a Zenodo link or an invalid value is insufficient: the resolver
never chooses one item. Malformed frontmatter, selected source identity,
record JSON, or PDF metadata produces `<sidecar>.error.json` and preserves
the last successful feed and sidecar. `--allow-empty` permits a valid zero
candidate render only; it does not forgive insufficiency.

The sidecar includes a SHA-256 of the exact JSONL bytes and a SHA-256 of the
canonical sorted Git path/blob inventory. The feed and sidecar are staged
before either is replaced, then replaced individually. If interrupted between
replacements, compare the sidecar feed hash with the feed bytes to detect a
mismatched pair. Output, sidecar, and error destinations must be distinct and
outside the catalogue checkout and its separate Git metadata directory.

The initial audited commit `0f74d8981716b7237e7cf4374413921708a8daaf`
had 303 direct grant Markdown blobs and eleven links mentioning Zenodo. This
is a bounded catalogue fact, not a claim that any linked file is a proposal,
a suitable prose sample, reachable, licensed for use, or an exact match to
the catalogue title and author. Do not turn metadata size or checksum into
fetched-byte custody.
