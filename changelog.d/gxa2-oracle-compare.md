### Changed

Relocate the oracle comparison reporter into `setec.oracle`, keeping the
historical script/module alias and all CSV/report paths under `scripts/oracle`.
Retain the complete report, numerical comparisons, caveats and missing-input
behavior. Extend the existing exact nested-launcher recognizer to `oracle`;
no formula, corpus, R dependency, capability or CI policy changes.
