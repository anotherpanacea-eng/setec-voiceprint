### Fixed

- `gen_contract_fixtures.py --check` no longer reports a **passing** tree as a failure on a non-UTF-8 console. Its success line carries `✔`, so on Windows `cp1252` with `PYTHONUTF8` unset the `print()` raised `UnicodeEncodeError` after the check had already passed and the gate exited 1 — indistinguishable from real drift. `main()` now forces UTF-8 stdio first, as the `tools/` CLIs already do via `tools/_console.py`.
