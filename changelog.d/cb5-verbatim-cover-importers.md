### Changed

**Verbatim-cover callers import `setec.core.verbatim_cover` directly (CB-5).**
`corpus_novelty_audit`, `skeleton_overlap_audit`, `reconstructibility_probe_set`
and the AITDNA benchmark's originality scorer used only names that the
`originality_audit` surface re-exports from `setec.core.verbatim_cover`. They now
import core, and their four layering exemptions are deleted. No code moved.
