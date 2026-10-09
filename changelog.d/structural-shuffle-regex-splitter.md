### Fixed

`setec-voiceprint`: `structural_shuffle_audit.split_sentences` no longer prefers a spaCy sentencizer when spaCy happens to be importable. The spaCy branch (`spacy.blank("en")` plus the rule-based sentencizer) and the stdlib regex disagreed on about 40% of test strings, and CI ran the regex, so the audit's sentence units and shuffle results depended on the host. The function now always uses the regex that CI already exercised. Hosts without spaCy see no change; hosts with spaCy now match CI.
