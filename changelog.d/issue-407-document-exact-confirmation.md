### Fixed

**Made `near_dup_dedup` document deletion exact-confirmed (#407).**
MinHash-LSH now generates candidates only; every candidate edge must meet the
configured threshold under exact Jaccard over the repository's normalized
shingle sets before it can join a destructive cluster. Reports bind the actual
datasketch version, MinHash scheme and seed, normalization, and
ephemeral-rebuild-only index policy. LSH candidate false negatives can still
leave duplicates retained, so the optional pass explicitly does not claim
exhaustive uniqueness. Passage Stage A/B behavior is unchanged.
