### Fixed

**`triage_agreement` — refuse self-column comparisons.** Framework and human label selectors must be distinct, nonblank keys. Invalid selectors are refused before input access or report writes; distinct columns with identical labels still report perfect agreement.
