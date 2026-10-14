### Fixed

**Feature-cluster output no longer depends on the Python hash seed (CB-6).**
`stylometry_distance.compute_clusters` walked each cluster's member set in set
order. That order follows `PYTHONHASHSEED`, so tied features in a cluster's
`top_features` (and the float sums behind its means) could come out differently
from run to run of `voice_distance`. It now walks the members in sorted order. A
zero-install consumer case for `voice_distance` (`--no-spacy`) pins the
envelope, and the ID leaves the pending list.
