### Fixed

**`fetch_raid.py` / `fetch_mage.py` — bind downloads to recorded revisions and
report corpus posture truthfully.** License metadata, file listings, and every
download now use the resolved Hugging Face revision. Versioned fetch receipts
record the exact observed repository wrapper-license declaration and SETEC's
local-only content posture. RAID now refuses `--no-adversarial` before download
when the hosted monolithic CSV layout cannot honor file-level exclusion, and
directs operators to `raid_to_manifest.py --no-adversarial` for row filtering.
Both fetchers invalidate stale provenance before corpus mutation, and both
converters now deny public-output overrides and emit private manifest entries.
