### Added

Added an internal, model-free `compose_imessage_review_bursts.py` CLI that
turns one fully closed atomic iMessage acquisition into deterministic review
bursts. It preserves exact retained-row and word accounting, treats excluded
and held rows as explicit non-members, keeps member identities inside the
private package, and supports owner-only macOS create-new/resume publication.
The tool is not a registered capability and does not activate, classify, or
ingest any corpus.
