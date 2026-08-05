### Fixed

**The old per-consumer version parser silently dropped malformed suffixes,
letting a prerelease build satisfy a numerically-equal stable version
floor** (`_parse_version("1.129.0-rc.1") -> (1, 129, 0, 1)`, which compared
as *greater than* the stable floor `(1, 129, 0)`). The shared client's
`parse_version`/`meets_floor` raise on a malformed or four-component
version instead of silently truncating, and implement explicit SemVer
prerelease precedence, so `1.129.0-rc.1 < 1.129.0` as it should.

**The permanent consumer warning classifier's unmatched-warning fallback
was `"cosmetic"` (silent).** It is now `"reliability"` — unknown producer
warning prose fails upward instead of being presumed harmless.
