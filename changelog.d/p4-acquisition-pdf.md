### Changed

- Move the complete `acquire_blog`, `acquire_blogger_takeout`, `acquire_epub`, `acquire_magazine`, `acquire_manuscript`, `pdf_inventory` and `pdf_extract` surfaces into `setec.surfaces` behind their permanent compatibility launchers, preserving acquisition records, extraction, inventories, licenses, reports and CLI behavior. `acquisition_core` stays flat for the P5 audit; the bootstrap count stays unchanged.
