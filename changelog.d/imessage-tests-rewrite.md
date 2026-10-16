### Changed

Cut `test_acquire_imessage_sent_atomic.py` from 14,857 to 2,942 lines (659 to 197 tests). The kept tests cover the failures that would pass silently: message selection and content, Apple date and timezone conversion, group-chat policy, HMAC key and locator handling, schema preflight, private-file modes and links, email-in-body adjudication, byte-identical resume, rowid-stable candidate semantics and conserved aggregate counts. The bootstrap, snapshot, universe and promotion state-machine tests are cut to one representative per refusal class. Every test that the workflow names is kept. No production code changes.

Release bump class: PATCH (`chore`).
