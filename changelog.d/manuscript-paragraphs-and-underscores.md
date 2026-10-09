### Fixed

**`acquire_manuscript` — preserve manuscript paragraphs and literal underscores.**
Fixed word windows retain blank-line paragraph boundaries while keeping their
existing word membership, count and order. DOCX windows retain semantic paragraph
boundaries; work and real chapter modes keep their existing output. Markdown
emphasis stripping preserves underscores inside identifiers such as
`file_name_here`, including identifiers wrapped in balanced underscore emphasis.

Corrected paragraph separators and underscores intentionally change affected
cleaned text and content hashes. Existing identity baselines need explicit
re-acquisition and refreshed manifests to obtain this correction; existing text,
sidecars and manifest rows are not automatically migrated, removed or rewritten.
