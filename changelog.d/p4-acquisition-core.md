### Changed

PATCH: Relocate the whole acquisition_core implementation behind its permanent compatibility alias, preserving acquisition behavior, lazy helper imports, baselines lookup and privacy refusals. Existing fragment and discovery status stay unchanged. The layering checker admits the existing no-main surface alias template with its strict ownership and edge checks.
