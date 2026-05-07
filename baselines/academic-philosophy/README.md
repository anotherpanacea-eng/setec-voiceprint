# Academic Philosophy Baseline

Reference corpus for academic philosophy register. Used by `variance_audit.py --baseline-dir baselines/academic-philosophy/`.

## What belongs here

Plain-text articles or chapters from scholarly philosophy. Analytic, continental, history-of-philosophy, applied ethics. The goal is range across the field's registers.

## Suggested compilation

**Open-access journals.** Philosophers' Imprint, Ergo, Analytic Philosophy, Analysis (some open content), Ethics (some open content), Mind. Most have plain-text or PDF-extractable archives.

**Pre-1990 analytic work.** W.V.O. Quine (Word and Object excerpts, From a Logical Point of View), Donald Davidson (Inquiries into Truth and Interpretation), Peter Strawson, John Rawls (A Theory of Justice excerpts), Bernard Williams. Many available in plain text via library digitization.

**Continental tradition.** Available open content from Continental Philosophy Review, Continental Philosophy of Science. Translated work has its own variance signature; prefer English-language original work where possible.

**History of philosophy.** Articles from Journal of the History of Philosophy, History of Philosophy Quarterly. The register is distinct from contemporary work.

## What does not belong

- Lecture notes or transcripts. Spoken philosophy has different variance signals than written.
- Anthology introductions. Often summarize and over-smooth.
- Encyclopedia articles (SEP, IEP). Compressed by editorial constraints.
- Translated work. Same caveat as for literary fiction.

## Minimum size

8-10 articles, each 5,000+ words. Philosophy articles run long, so total corpus easily reaches 50,000-100,000 words.

## Personal-baseline note

For a personal academic baseline, prefer the writer's own prior articles over the genre baseline. A writer's idiolect (function-word fingerprint, sentence-length distribution, FKGL std) is a stronger reference than the field aggregate. Keep those files in a private personal-baseline directory and run with that directory instead.
