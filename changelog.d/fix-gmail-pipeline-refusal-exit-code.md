### Fixed

**`gmail_author_pipeline` — policy refusals now exit 3, not 2.** The facade's
public contract distinguishes an invalid request (exit 2, `bad_input`) from a
policy or domain refusal (exit 3, `policy_refused`), but every refusal raised
after request validation — a repeated side effect on a completed stage, a
failed prior-lineage check, an unsafe log or receipt path, a declined approval —
escaped to `main()` and was reported as `bad_input`. Request parsing and stage
execution are now separate, with one refusal boundary each.
