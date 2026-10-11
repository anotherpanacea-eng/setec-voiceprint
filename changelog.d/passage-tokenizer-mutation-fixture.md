### Fixed

- Keep the existing passage-tokenizer commitment-mutation test fixture in
  canonical LF JSON on Windows, so it exercises the intended commitment refusal
  instead of stopping at newline serialization refusal. All mutation inputs and
  assertions, frozen data and production behavior are unchanged. PATCH.
