### Fixed

- Restrict the Tanner acquire_pdf_urls fi-glyph correction to one reviewed
  PDF byte hash and two verified font/CMap signatures, retaining ordinary page
  text if inspection or visitor reconstruction fails. Record the fetched PDF
  hash and artifact profile in preprocessing provenance.
- Default direct PDF acquisition to language_status unknown; operators can
  provide a validated --language-status with affirmative evidence for native.
