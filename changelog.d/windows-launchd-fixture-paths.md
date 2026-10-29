### Fixed

**`test_setup_launchd.py` passes on native Windows (CB-2).** The good-config fixture and the CLI
dry-run test now build `python_bin` and `shard_runner` under `tmp_path` instead of POSIX-only
literals, which Windows treats as rooted but not absolute. Command-builder tests get a stand-in
uid where `os.getuid` is missing, execute-bit checks run only on POSIX, and the quote round-trip
test expects the host's own path separators. Test-only; `setup_launchd.py` is unchanged.
