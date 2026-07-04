### Fixed

**P3/LOW review follow-ups across specs 16 / 25 / 28 / 35 (PATCH; behavior-conservative except the two documented `fast_detect_curvature` items).**

- **`judge_backends` host-delegated judge (spec 35).** The `SETEC_HOST_JUDGE_CMD` subprocess transport now runs under a timeout (`SETEC_HOST_JUDGE_TIMEOUT`, default 120s); a hung operator command surfaces as a clean judge-family error instead of hanging the judge forever. `shell=True` is retained intentionally (the command is trusted operator config; only the request crosses via stdin). Added resolved-transport tests (entrypoint `module:function`, subprocess command, and the timeout wrap) alongside the existing unresolved/override coverage.

- **`voice_verifier` (spec 35).** Closed the CLI half-wiring: `--judge` now offers `agent_host` (the `build_verifier` factory already accepted it), so the key-free host-delegated backend is reachable from the command line. The resolved host is already recorded in `judge_identity` (`host` + `delegated`); `voice_verifier` has no judge-kind-keyed caveat block, so only the choice was added. Test asserts `agent_host` is in the choices and that a stubbed `agent_host` run surfaces `host` in the envelope.

- **`fast_detect_curvature` (spec 25, T-Detect).** Hoisted the Student-t `t_df <= 2` guard above the degenerate-variance check so an invalid `nu` is rejected on every `--tail student-t` input (previously skipped when the reference variance was degenerate), and now emit the `tail`/`t_df` mode marker even on a degenerate-variance run (with `curvature_t` correctly absent), keying the T-Detect caveat on `tail == "student-t"` rather than on `curvature_t`. The Gaussian/default path is byte-identical (no `tail`/`t_df`/`curvature_t` keys, no caveat).

- **`conformal_gate` (spec 28).** Disambiguated the `threshold is None` abstain reason: when scores are fully distinct but `floor(fpr_bound * n) == 0` the message now names the small-calibration cause (`need n_calibration >= ceil(1/fpr_bound)`) instead of the misleading "too many tied scores"; the genuinely-tied case keeps its original message. `available: False` and all other keys are unchanged.

- **`paraphrase_ladder` (spec 16).** Added a parity test pinning `paraphrase_ladder._score_text` byte-equal to `pan_replay._score_text` on a shared fixture (the spec says "reused, not reimplemented" but nothing enforced it); no code change, so no behavior risk.
