### Changed

- Relocate the whole `binoculars_calibrate` implementation into `setec.surfaces` behind its permanent schema-1.x compatibility launcher. CLI, threshold derivation, output, progress and compatibility-bound checkpoint/resume behavior are unchanged; capability status remains `structural_only`. Count the required scripts-root launcher bootstrap in the packaging and sys.path gates. Bump class: PATCH. Refs Fleet #73 and #701.
