### Added

**`period_lm_contrast` — period-LM vs modern-LM likelihood contrast (VP-RS6).** A new
`period_register` tool that scores each passage in bits per UTF-8 byte under a period-trained LM
and its modern-trained twin and reports the contrast (negative means the period LM finds the
passage easier), with mean, median, the share of period-easier passages and a seeded bootstrap
interval, plus a candidate-minus-reference gap when real period passages are given. The default
pair is the Apache-2.0 talkie-lm 1930 and talkie-web 13B bases, which ship custom model code and
so need `--trust-remote-code`. The typewriter-ai 1913 7B base declares no license and is refused
(`policy_refused`) unless `--allow-undeclared-license` is passed. Precomputed scores run
model-free, and `--score-cache` resumes an interrupted GPU run.
