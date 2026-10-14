### Fixed

**`voice_distance` clusters and `idiolect_detector` rankings no longer depend on the Python hash seed (CB-6).**
`stylometry_distance.compute_clusters` walked each cluster's member set in set
order. That order follows `PYTHONHASHSEED`, so tied features in a cluster's
`top_features` (and the float sums behind its means) could come out differently
from run to run of `voice_distance`. It now walks the members in sorted order. A
zero-install consumer case for `voice_distance` (`--no-spacy`) pins the
envelope, and the ID leaves the pending list.

`idiolect_detector` had the same flaw in its keyness rankings and preservation
list: rows with equal scores kept their hash-seeded n-gram set order. Each
score sort now runs after a stable sort by phrase. Its zero-install case
(`--target-dir` holding the shared input, `--reference-dir` the baseline
folder) joins the table, and the ID leaves the pending list.
