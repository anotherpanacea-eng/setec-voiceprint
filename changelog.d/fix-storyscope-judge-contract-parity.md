### Fixed

**StoryScope judge robustness brought up to ArgScope parity (`narrative_judge`, `narrative_decision_audit`).** Three failure paths that escaped the `JudgeError`/exit-code contract are now handled, mirroring the hardening that landed for `argument_judge` in #193:

- **`narrative_judge._manifest_judge`** read + parsed the manifest with a bare `json.loads(Path(...).read_text())` — a missing file (`FileNotFoundError`) or malformed JSON (`JSONDecodeError`) escaped as a raw traceback past `main()`'s `except JudgeError`. Both are now wrapped as `JudgeError` ("cannot read" / "invalid JSON"), and a non-object top-level manifest is rejected explicitly.
- **`narrative_judge._extract_json`** returned `json.loads(...)` directly, so a model emitting a bare top-level `[...]` array slipped through as a non-dict (a latent `AttributeError` downstream). It now raises `ValueError` on a non-object top level, which the API backends repackage as a clean `JudgeError`.
- **`narrative_decision_audit`** judge-*construction* failures (missing `--judge-manifest`, missing `--judge-model`, missing API key) `print(...)`+`return 2`, which `setec_run` maps to `reason_category: policy_refused` (the privacy ratchet) because a bare exit-2 carries no `usage:` line. These are bad *setup* input, not a policy refusal, and consumers branch on the category — so they now route through `parser.error()` (emits `usage:`, exit 2) which the dispatcher recognizes as `bad_input`. Judge *execution* failures stay exit 3 → `internal_error`.

Adds regression tests: manifest missing-file / invalid-JSON / non-object → `JudgeError`; bare-array extraction → `ValueError`; and `--judge=manifest` with no manifest → exit 2 with a `usage:` line.
