### Changed

**CI gates spec-anchor drift on changed specs.** `tools/spec_anchor_lint.py`
existed and worked but was never wired into CI, so nothing ran it — a spec could
assert a symbol, `file:line`, sibling spec, or env var that does not exist and
merge clean. The `tests` workflow now lints every spec **changed in a pull
request** and fails on HIGH-confidence absences; MEDIUM-confidence hits stay
advisory. NUL-delimited change discovery safely handles every UTF-8 Git path,
and deletions/renames fail if a surviving spec retains the removed path or spec
number. The gate is diff-scoped rather than repo-wide because 19 of the 51 specs
on `main` currently gate, and those are overwhelmingly shorthand-path false
positives rather than real phantoms — a repo-wide gate would fail on day one
and train everyone to ignore it.
