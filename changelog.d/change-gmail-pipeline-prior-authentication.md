### Changed

**`gmail_author_pipeline` authenticates predecessor stages by hash rather than
re-execution.** Driving the seven producer stages forward started 28 domain
child processes rather than 7, because every call re-ran each predecessor's
verifier; a full re-verification pass cost another 28, with stage 05
recomputing MinHash over the whole manifest each time. Predecessors are now
authenticated from their lineage receipts and real output artifacts. Both
numbers are 7, and a new test pins one child per call.
