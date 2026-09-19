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

Supported physical-line speaker labels include Senator, Representative,
General, Admiral, Colonel, Captain, Secretary, Director, several two-word
military ranks, and Mr./Ms./Mrs./Dr., followed by one to four name words and
a period. The CHAIRMAN, The WITNESS, and The COUNSEL are also recognized.
Other all-capital roles after The or THE refuse the current candidate;
The Staff Director also refuses with a title-cased role. A lowercase the
or a generic title-cased noun can be a hard-wrapped prose continuation and
does not by itself count as a transcript label. A standalone opening
curly double quote followed by a speaker or other boundary cue before its
standalone close refuses that candidate; an unclosed cue also refuses it. These textual cues
do not parse every possible quotation or transcript format.

Standalone exhibit and table labels retain a compact identifier such as
[Exhibit A], [Exhibit A-1], or [Table 2]; a statute citation retains a
section number and optional subsections. Longer bracketed text such as
[Exhibit A admitted.] is unresolved and refuses the current candidate.
These recognizers are intentionally narrower than all valid source layouts.

Generic manifest consumers check draft schema shape, not pending sidecar
review fields; an operator must review the source before explicitly selecting
or promoting any draft entry.

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
