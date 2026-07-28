### Fixed

**A test pinned a transient `changelog.d/` fragment path, so it broke on the
first release cut.** `changelog.d/` fragments are transient by design — a
release folds each one into `CHANGELOG.md` and deletes it — but
`test_changelog_fragment_names_the_capability_id_verbatim` read
`feat-register-composition-sweep-encoders.md` by name and raised
`FileNotFoundError` once v1.127.0 consumed it. The assertion now follows the
record to wherever it currently lives (the fragment before a release,
`CHANGELOG.md` after), so it keeps its teeth without pinning a path guaranteed
to disappear. Swept: this was the only test reading a real `changelog.d/`
fragment by name; the neighbouring capability assertions read
`capabilities.d/*.yaml`, which is permanent, and the assembler's own tests
build fragments under `tmp_path`.
