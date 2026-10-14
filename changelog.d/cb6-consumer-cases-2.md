### Added

**Repetition consumer case in the zero-install gate (CB-6).** `repetition_audit` now
runs in the bare copy against a small synthetic baseline folder that the gate
writes. Each row carries its full argument list. On Windows, path strings in an
envelope are compared in POSIX form.
