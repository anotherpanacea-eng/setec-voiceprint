### Changed

**The shared acquisition library moved to `setec.core.acquisition_core` (CB-5).**
Everything in the `acquisition_core` surface except its surface tag and
`pdf_text_from_bytes` moved verbatim into a core module: 40 names, including the
fetcher, manifest and write steps, the privacy checks and the baselines lookup.
`pdf_text_from_bytes` stays because it imports the `pdf_extract` surface. The
surface re-exports every moved name as the same object, so the legacy
`acquisition_core` import keeps working. Thirteen `acquire_*` surfaces and
`pdf_extract` now take the module from core, and their 14 layering exemptions
are deleted. `acquire_pdf_urls` still uses the surface for `pdf_text_from_bytes`.
