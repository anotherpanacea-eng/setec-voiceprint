# ESL / L2 fairness slice fixture (spec 05-esl-fairness-slice)

Small **synthetic** prose fixture for the unit tests of the
validation-harness ESL/L2 fairness slice
(`tests/test_validation_harness_esl_slice.py`).

Each `.txt` here is hand-written for the test only. They carry a
`language_status` label (via the manifest the test constructs) so the
harness can exercise its per-`language_status` FPR/TPR slice, the
"don't pool" guard, the native-only annotation, and the
empty/underpowered caveat path:

| file | language_status | role |
|---|---|---|
| `native_human_01.txt`     | `native`              | control (human) |
| `native_human_02.txt`     | `native`              | control (human) |
| `native_ai_01.txt`        | `native`              | positive (AI-style, smoothed) |
| `non_native_human_01.txt` | `non_native_intermediate` | control (human, L2) |
| `non_native_human_02.txt` | `non_native_advanced` | control (human, L2) |
| `learner_human_01.txt`    | `learner`             | control (human, L2 learner) |
| `non_native_ai_01.txt`    | `non_native_advanced` | positive (AI-style, smoothed) |

## These are NOT operator-sourced corpora

Per the spec's **Open questions** (gating item), the real fixtures are
operator- or public-domain-sourced L2/translated text under appropriate
terms. That sourcing is a documented follow-up; this directory exists
only so the slice's *logic* (shape, guard, caveats, license) can be
pinned by tests without shipping external corpora. Do not draw
empirical conclusions about SETEC's real ESL false-positive rate from
these synthetic samples — they are written to be plausibly human / AI,
not measured to be.
