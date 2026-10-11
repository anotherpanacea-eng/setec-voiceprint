### Fixed

- Emit canonical UTF-8 LF JSON from the standalone consumer reference fake,
  including on Windows pipes with a non-UTF-8 default encoding. Preserve
  logical output for text captures and verify raw bytes for every golden.
