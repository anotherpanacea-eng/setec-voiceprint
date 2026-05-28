# StoryScope prompts (vendored from Russell et al. 2026)

These prompts are reproduced verbatim from the paper's TeX source
(arXiv:2604.03136v4, `prompts_display/` directory) for offline
replication of the StoryScope pipeline inside SETEC.

## Vendored

| File | Stage | Paper source |
|---|---|---|
| `prompt_generation.md` | A1 — writing-prompt extraction from human stories | `prompts_display/prompt_generation.md` |
| `story_generation_example.md` | A2 — representative story-generation prompt (the actual story prompts are operator-supplied) | `prompts_display/story_generation_example.md` |
| `template.md` | B1 — NarraBench template extraction | `prompts_display/template.md` |

## Not vendored

The paper's repository at `github.com/jenna-russell/storyscope`
carries the comparative-analysis (B2), per-dimension feature-
discovery (B3, 10 prompts), and per-dimension feature-assignment
(B5, 10 prompts) prompt sets. The SETEC repo does **not** redistribute
those prompts; for L3 replication, fetch them from the paper's GitHub
release and place them at:

- `b2_comparative_analysis.md`
- `b3_feature_discovery_<dimension>.md` (10 files: agent, social_network,
  events, plot, structure, setting, time, revelation, perspective, style)
- `b5_feature_assignment_<dimension>.md` (10 files, same dimensions)

The replication stage stubs in `scripts/replication/stages/` read
from this directory; missing files produce a clear error pointing
the operator at the paper's release.

## License posture

Per the paper's `00README.json`, the TeX source carries the COLM 2026
review-copy posture. The prompts in `prompts_display/` are part of
that source. Vendoring them in SETEC for academic-purpose replication
matches the same posture as `references/laundering-vocabulary.md` and
the other paper-anchored reference docs in this directory.
