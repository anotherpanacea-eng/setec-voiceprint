### Changed

**Completed the `capabilities.d/` status audit (#196).** Eight fully curated
`voice_coherence_acquisition` tools are now discoverable as `structural_only`
operator tooling, a non-inferential status for which calibration is not applicable:
`acquire_courtlistener`, `acquire_everycrsreport`, `acquire_gmail_sent`,
`acquire_govinfo_chrg`, `acquire_imessage_sent`, `acquire_mirrulations`,
`acquire_openalex_core`, and `acquire_pdf_urls`.
The WIP `acquire_imessage_sent_atomic` contract remains hidden. Of the remaining
`status: todo` entries, 46 now name their exact unresolved auto-seeded operator
contract fields and the atomic entry names its live-smoke/durability/consumer gates.
The drift gate rejects the seeder default as well as missing/generic placeholders or
stale reasons retained after promotion. The generated readiness matrix groups
acquisition under tooling, shows the real required source, and distinguishes local
I/O from network acquisition.
Acquisition runtime behavior is unchanged.
