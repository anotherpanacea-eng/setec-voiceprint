### Fixed

- Guard the HTML whitespace output test with its required lxml dependency, retaining pure-normalizer and historical html.parser coverage when lxml is absent. Production extraction and parser-refusal behavior are unchanged. PATCH.
