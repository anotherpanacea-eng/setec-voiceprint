### Fixed

- Keep Unicode `--help` output intact under cp1252 consoles for `pan_replay`, `adversarial_robustness_card`, `bigram_diff`, and `cosine_explanation` using a plugin-local UTF-8 stdout/stderr helper. This partially addresses #428; runtime writer cases remain separate.
