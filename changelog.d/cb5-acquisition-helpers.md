### Changed

**Five importers use the privacy guard from core (CB-5).** `generate_voice_report`,
`pdf_inventory`, `general_imposters`, `author_corpus_export` and
`fetch_pan24_voightkampff` imported `acquisition_core` only for
`check_output_privacy` and `is_private_safe_path`. Both already live in
`setec.core.acquisition_primitives`, so these modules now import them from there,
and their five layering exemptions are deleted. Behavior is unchanged.
