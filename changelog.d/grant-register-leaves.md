### Added

Added the `grant_proposal_academic` and `grant_proposal_nonprofit` register
leaves to the `acquire_pdf_urls` and `argmove_profile` capability metadata.
The legacy bare `grant_proposal` input remains accepted during the warning
window and now emits an actionable validator deprecation warning naming both
replacement leaves. Normal validation accepts the compatibility input with
exit code 0; `--strict` promotes the warning to exit code 1. Bump class:
`feat` (MINOR).
