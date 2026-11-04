### Changed

Move the complete `evidence_pack` and `explain` report renderers into
`setec.surfaces` with their permanent historical launchers. Preserve Explain's
scripts-root label lookup and both tools' existing report content, licensing,
formatting, warnings and input/output behavior.

Recognize the documented no-TASK launcher in the existing layering admission
check, while retaining exact launcher matching and rejecting unsupported TASK
declarations.
