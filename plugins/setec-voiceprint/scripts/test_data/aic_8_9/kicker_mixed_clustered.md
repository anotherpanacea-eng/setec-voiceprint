The framework's documentation of the AIC-9 detector includes a working set of test fixtures, each one structured to exercise a particular code path in the detector's classifier and density computation logic.

The first three paragraphs of this fixture are intentionally long-winded with no kicker shape at their endings, which the detector should classify as paragraph-final-not-a-kicker for the purposes of the surrounding density computation that aggregates per-paragraph verdicts into a document-level rate.

These opening paragraphs establish that kicker density is not uniform across the document and that the detector's spacing-variance signal needs a clustered fixture to test the spacing math against rather than relying solely on the uniformly-distributed positive fixture that lives alongside this one in the test_data directory.

The next two paragraphs cluster the kickers. The cluster is the diagnostic. Closure compresses.

Both of these short paragraphs end with kicker-shaped sentences. The shape lands. The pattern emerges.

After the cluster, the document returns to normal paragraph-ending behavior, with longer terminal sentences that resolve a specific question and lead into the next section rather than performing the aphoristic landing that defines the kicker pattern that AIC-9 measures.

The eighth and final paragraph closes out the fixture with a similarly-long terminal sentence that the detector should classify as paragraph-final-not-a-kicker for the same reasons as the opening paragraphs, returning the document's overall kicker-density to a number that reflects the clustered rather than distributed pattern in the middle.
