### Added

**`meaning_preservation` — MIS and SBERT meaning-preservation reads for rewrite pairs (VP-RS5).**
A new `craft_restoration` tool that scores each (source, rewrite) pair with the Mutual
Implication Score (`s-nlp/Mutual_Implication_Score` through the `mutual-implication-score`
package, loaded at run time; the weights are CC BY-NC-SA 4.0, are not vendored, and the license
is carried in the output) and with sentence-embedding cosine through the shared embedding backend
(default `mxbai`). It reports per-pair values and per-channel mean, median, minimum and quartiles,
drops a channel whose model cannot load with a warning, abstains (`missing_dependency`) only when
no channel loads, and warns when a pair is long enough to be truncated at 512 tokens.
