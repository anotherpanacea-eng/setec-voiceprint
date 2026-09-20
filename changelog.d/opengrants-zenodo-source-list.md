### Added

- Add `build_opengrants_zenodo_source_list`, a metadata-only resolver for pinned Open Grants Git objects and Zenodo record JSON. It emits a candidate feed and provenance sidecar for later private `acquire_pdf_urls` review, without downloading PDF bytes or assigning corpus roles. This is a feature (MINOR-class) change.
