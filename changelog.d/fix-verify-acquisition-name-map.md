### Fixed

**`acquire_gmail_sent.py verify-acquisition` no longer refuses acquisitions
that used `--name-map`.** Display names are substituted into cleaned text and
are therefore part of every committed `content_hash`, but the verifier rebuilt
the recipient map with an empty display-name table. It recomputed different
text than the one committed, and refused. Acquisition and verification now
build the map through one shared `_build_recipient_map`, so they cannot drift.

### Changed

**`gmail_author_pipeline` authenticates predecessor stages by hash rather than
re-execution.** Driving the seven producer stages forward started 28 domain
child processes rather than 7, because every call re-ran each predecessor's
verifier; a full re-verification pass cost another 28, with stage 05
recomputing MinHash over the whole manifest each time. Predecessors are now
authenticated from their lineage receipts and real output artifacts. Both
numbers are 7, and a new test pins one child per call.
