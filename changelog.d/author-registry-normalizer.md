### Added

- Add a private, no-prose author-registry normalizer that explicitly maps legacy source registers and source-qualified persona aliases to the multi-register author-modeling taxonomy, refuses duplicate-key, non-author, or non-baseline posture before eligibility, retains source provenance and exact-byte deduplication, and writes only owner-readable artifacts.
- Allow `author_corpus_export` to accept an explicit, receipt- and smoke-bound source-persona alias, so a verified legacy label can be intentionally joined to its canonical author persona.
- Add an exact-byte document-local adapter for attested pre-AI author material, with closed control-character and duplicate-key refusal, source-qualified persona aliases, symlink-closed owner-only output permissions, and Windows ACL checks for locally held HMAC keys.
