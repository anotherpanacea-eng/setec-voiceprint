"""StoryScope replication layer.

See `references/narrative-decision-replication-spec.md` for the
full pipeline specification. This package wires the pure-Python
analytics stages (C1–C7) and provides LLM-stage stubs (A1–A3, B1–B5)
that route through the existing SETEC judge interface.
"""
