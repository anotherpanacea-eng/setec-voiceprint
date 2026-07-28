### Fixed

**The manifest validator no longer aborts on a non-string `register` or `use`
tag.** Membership tests hash their left operand, so a structurally invalid
field reached a `hash()` and raised `TypeError` out of `validate_manifest` —
one malformed row in a manifest ended the whole run with a traceback and lost
every other row's issues, on exactly the input class the validator exists to
report. Three sites were affected: the private-dyadic profile-only check
(`register in PROFILE_ONLY_REGISTERS`, a regression from the register-tier
work, whose predecessor compared with `==` and was total over all types), and
the pre-existing `voice_profile`/`idiolect` privacy-ratchet intersection and
impostor-relevance set build, both of which hash `use` elements.

A present-but-non-string `register`, and any non-string `use` tag, are now
reported as errors rather than passing silently, and non-string tags are
dropped before any set operation. Both fields previously slipped through every
type-gated check without producing an issue at all.
