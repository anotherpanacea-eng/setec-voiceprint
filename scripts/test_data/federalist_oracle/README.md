# Federalist Papers fixture corpus (stylometry oracle)

Six papers from *The Federalist Papers* (1787-1788), public domain via
Project Gutenberg eBook #18. Used to validate SETEC's voice-distance
feature extraction and Burrows-style Delta computation against the R
`stylo` package's reference implementations.

Three Hamilton, three Madison. Single-author papers only (joint and
disputed-authorship papers excluded). The Mosteller-Wallace 1964
study established Hamilton vs. Madison as the canonical stylometric
binary classification benchmark; this fixture is a tiny subset usable
for distance-method correctness checks.

Source: https://www.gutenberg.org/ebooks/18
License: Public domain in the US (and most other jurisdictions, but
the US is the relevant one for Project Gutenberg redistribution).

Each file is the prose body of one paper. Headers ("No. X.", author
tag, salutation "To the People of the State of New York:") and the
trailing "PUBLIUS." sign-off have been preserved as part of the
document; both R `stylo` and SETEC will tokenize them identically as
incidental front matter, so they don't bias the comparison.
