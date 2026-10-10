### Fixed

Close the Brysbaert converter's read-only workbook and owned XLSX input on success, validation failure and interruption. `fetch_brysbaert.py --keep-xlsx` can preserve the successful source on Windows, and failed conversions can discard it without relying on garbage collection. Existing workbook-format refusals, CSV validation and atomic replacement remain unchanged. PATCH.
