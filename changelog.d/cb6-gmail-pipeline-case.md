### Changed

**Zero-install consumer case for `gmail_author_pipeline` (CB-6).**
The gate writes a one-message synthetic Sent mbox, an HMAC key and a stage-01
request inside a scratch `ai-prose-baselines-private` root. It then runs the real
`01_source_smoke` stage through `setec_run` under the default umask. The stage
completes only because acquisition now writes owner-only artifacts, so the case
guards that fix. The config identity and the artifact set are pinned. The
descriptor timestamp and the manifest's acquisition date vary by run, so those
identities, and the domain and lineage hashes over them, are normalized;
`normalize` paths may now index lists. The ID leaves the pending list.
