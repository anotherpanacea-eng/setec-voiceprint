# Author-language status for EveryCRS and Mirrulations acquisition

The ordinary `acquire_everycrsreport` and `acquire_mirrulations` draft manifests
record an author-language status relative to the language of each emitted text.
Their `--language-status` option accepts `native`, `non_native_advanced`,
`non_native_intermediate`, `learner`, and `unknown`. The default is `unknown`.
Both Mirrulations metadata modes use the same value. The option applies to the
whole acquisition batch, not separately to individual records.

Every non-`unknown` value is an operator assertion that needs appropriate
evidence for every affected author and text. `native` is an affirmative claim
about the author's language background. English prose, original composition,
federal or institutional affiliation, nationality, source-reported dates, and
verified metadata custody do not establish that claim. `unknown` means the
required language evidence has not been established; it is not a claim that
the writer is a language learner. Keep heterogeneous or unsupported batches
at `unknown`, or separately curate records with evidence before assigning a
more specific status. This option does not collect evidence or decide corpus
admission.

For EveryCRS, the option governs only ordinary draft-manifest emission.
`--historical-versions` still writes private candidates and receipts without
a manifest or language-status annotation. Supplying the option in that mode
does not establish evidence about a candidate's author. The shared manifest
composer's compatibility default and other acquirers are unchanged.
