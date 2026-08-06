### Added

**Packaging P1 (`specs/svp-packaging-conversion.md`) — zero-install package skeleton and CI gates, no production module moves.** Lands `scripts/setec/` (the empty `contract/`/`core/`/`surfaces/`/`calibration/`/`external_mirror/`/`replication/` package skeleton) and `setec.paths` (`find_plugin_root()` plus named stay-put data-path accessors, including `scripts_dir()`); `pytest.ini`'s single scripts-root `pythonpath` plus the AST codemod that drops redundant per-test-file `sys.path` bootstraps; `tools/check_packaging_migration.py` (the `__file__`-anchor migration checker, `--strict` mode with a shrink-only merge-base ratchet and ghost-row rejection, plus the single `packaging_migration_exemptions.yaml`); `tools/check_claim_license_guard.py` (the no-change claim-license deficit lock, with the P1 move map closed to the exact empty shape); and `tools/check_zero_install.py` (the bare-copy structural-reachability, direct-launcher-execution, and successful normalized-dispatch gate). All three new gates run in CI (`.github/workflows/tests.yml`).

The broader synthetic-backend consumer matrix remains in the separately queued hermetic-mode amendment and PR.

Also fixed in the same pass: `setec_run.py` resolves manifest paths from the actual copied plugin root, rejects escapes, and recognizes only the exact interpreter launch-error shape for the invoked script path.
