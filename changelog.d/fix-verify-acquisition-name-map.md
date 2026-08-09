### Fixed

**`acquire_gmail_sent.py verify-acquisition` no longer refuses acquisitions
that used `--name-map`.** Display names are substituted into cleaned text and
are therefore part of every committed `content_hash`, but the verifier rebuilt
the recipient map with an empty display-name table. It recomputed different
text than the one committed, and refused. Acquisition and verification now
build the map through one shared `_build_recipient_map`, so they cannot drift.
