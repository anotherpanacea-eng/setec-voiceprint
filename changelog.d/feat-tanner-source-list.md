### Added

- Add `build_tanner_source_list`, a deterministic and per-page-checkpointed
  public-metadata crawler that produces an `acquire_pdf_urls` feed for the
  private `academic_philosophy` impostor pool while enforcing Tanner's
  ten-second crawl floor and never fetching PDF bodies.
