### Fixed

- Give the adapter's four nonbaseline-declaration refusal cases portable temporary directory names and require the actual refusal to relabel material as `identity_baseline`. Truncated JSON names contained Windows-invalid quotes, a native defect recorded in PR #340. Local synthetic module and guard-bypass checks cover the correction; fresh Windows before/after and full-suite parity remain owed. Production authority checks and the existing Windows CI deselection are unchanged.
