### Changed

**Zero-install consumer case for `author_corpus_export` (CB-6).**
The gate writes one synthetic `gmail_sent` source, an HMAC key with mode 0600 and
an output path inside a scratch `ai-prose-baselines-private` root, in the shape
the exporter's own tests use. It then runs a `--dry-run` export and pins the
no-prose producer receipt. `producer_revision` (a hash of the exporter's own
source) is normalized. The ID leaves the pending list.
