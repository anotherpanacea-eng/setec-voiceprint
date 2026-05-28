"""LLM-driven replication stages.

Each stage script ships as a thin shell around the existing SETEC
judge interface. Operators wire their own model/credentials by
choosing a `--judge` backend at the command line; the stage scripts
manage manifest IO, checkpointing, and provenance.

Stages:

  A1  prompt_extraction      Human stories → writing prompts
  A2  story_generation       Prompts × 5 LLMs → mirrored stories
  B1  templating             Story → NarraBench JSON template
  B2  comparative_analysis   600-story discovery pool → comparisons
  B3  feature_discovery      Comparisons → 408 candidate features
  B5  feature_assignment     304 features × N stories → values

B4 (embedding-based feature dedup) is pure Python and lives at
``scripts/replication/feature_dedup.py``.

See `references/narrative-decision-replication-spec.md` for the
full pipeline design and per-stage prompt provenance.
"""
