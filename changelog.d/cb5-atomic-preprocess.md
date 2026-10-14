### Changed

**`acquire_imessage_sent_atomic` takes `preprocess_text` from `setec.core` (CB-5).**
Its default preprocessor lazily imported the `acquisition_core` surface alias only
to call `preprocess_text`, which now lives in `setec.core.acquisition_core`. The
lazy import points at core, and the layering exemption is deleted.
