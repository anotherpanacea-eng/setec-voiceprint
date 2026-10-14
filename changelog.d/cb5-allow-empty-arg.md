### Changed

**`gmail_locator_map` takes its two acquisition helpers from `setec.core` (CB-5).**
`add_allow_empty_arg` moved verbatim from the `acquisition_core` surface into
`setec.core.acquisition_primitives`, beside `check_output_privacy`; the surface
re-exports it as the same object. `gmail_locator_map` used only those two, so
its module alias now points at core and its layering exemption is deleted.
