### Changed

The iMessage atomic acquisition spec records the owner's 2026-11-05 ruling on mixed text with object-replacement markers.
- **Prose:** U+FFFC is stripped from usable sender text, and nothing else changes.
- **Attachments:** recorded separately, in a sidecar `attachment_evidence` object.
- **Unchanged:** attachment-only messages stay excluded, and legacy behavior is unchanged.
- **Scope:** spec text only. No code change and no re-import.
