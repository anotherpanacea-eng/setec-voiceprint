### Changed

Relocate the ten eligible P3 support libraries (`atomic_publish`,
`embedding_backend`, `embeddings`, `passage_remediation_projection`,
`pool_guard`, `shingle_dedup_checkpoint`, `shingle_dedup_io`,
`shingle_dedup_validate`, `surprisal_backend`, and `windows_descriptor_io`)
into `setec.core`, retaining permanent legacy module aliases and historical
class module names.

Private APIs, shared monkeypatch state, lazy backend imports, and the native
Windows/POSIX import contract remain unchanged. Existing publication/privacy
policies, checkpoint formats, hashes, algorithms, provider behavior, and refusal
messages are preserved.

Account for only the ten exact legacy-alias bootstrap anchors through the
existing packaging checks. All production sys.path sites remain counted; the
measured ceiling changes from 159 to 169 against base `7537b9`. No layer
exemptions, classifications, workflows, fixtures, or data ownership change.
The frozen passage tokenizer remains outside this relocation.
