# GovInfo CHRG prepared-block boundaries

The acquirer reads each whole-hearing HTM granule, decodes its text, and
finds physical-line `Prepared Statement of ...` headings. Each accepted
output is a **candidate block closed by a detected boundary; completeness
review pending**. The CLI writes a private draft reference-pool manifest,
not a production corpus registration.

A supported speaker label, a recognized procedural ending, a paired oral
`STATEMENT OF ...` heading, or the next prepared heading can close a
candidate. Supported bracketed content such as `[1]`, `[Exhibit A]`,
`[Table 2]`, and inline citations stays in the body. Unknown standalone
bracketed transitions and unsupported transcript-shaped speaker labels
refuse the current candidate. The next prepared heading is still evaluated
independently. End-of-hearing text without a detected close and bodies over
the safety cap are refused. Refusals appear in the local `--out` summary's
`skip_log` and increment `skipped_parse_error`.

The sidecar records a SHA-256 of the decoded hearing text, exact body
character offsets, boundary kind, and pending role, rights, and completeness
statuses. Offsets reproduce the retained raw body only when the **identical
decoded hearing text** is available; the sidecar does not contain that full
source. HTML structure can disappear during decoding, so a speaker-shaped
line in a quotation can look identical to an oral turn. Unmarked oral prose
can also escape this bounded recognizer. Review the source before treating
any candidate as a complete prepared statement or verifying official-duty
authorship and third-party material rights.

The `public_record` manifest value describes source accessibility. GovInfo
publication does not itself clear third-party material or establish that a
witness authored the statement in official federal duties.
