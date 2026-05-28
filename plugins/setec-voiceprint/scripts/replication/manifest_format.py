#!/usr/bin/env python3
"""manifest_format.py — shared schema + IO for replication manifests.

Every stage in `scripts/replication/` emits one of three manifest
shapes, plus a sidecar `manifest.json` describing provenance. The
shapes are:

  * **PromptManifest** — A1 output. One row per (source_story_id,
    prompt_id). Fields: ``prompt_id``, ``source_story_id``,
    ``prompt_text``, ``target_words``, ``judge_identity``.
  * **StoryManifest** — A2 output (and the human-corpus input).
    One row per (prompt_id, model) for AI; per (story_id) for
    humans. Fields: ``story_id``, ``prompt_id``, ``model``,
    ``story_text``, ``label`` (``pre_ai_human`` / ``ai_generated``),
    ``judge_identity``, ``stop_reason``, ``word_count``.
  * **FeatureManifest** — B5 output. One row per story carrying
    the per-feature values. Fields: ``story_id``, ``prompt_id``,
    ``model``, ``label``, ``narrative_values``,
    ``judge_identity``. This is the format the Surface 6 polarity
    audit and the Stage-C analytics consume.

Every stage's sidecar `manifest.json` carries:

  * ``stage`` — A1 / A2 / B1 / ... / C7
  * ``tool`` — relative path to the producing script
  * ``version`` — script SCRIPT_VERSION
  * ``prompt_fingerprint_sha256`` — SHA-256 of the rendered prompt
    (system_preamble + user_prompt + schema JSON)
  * ``judge_identity`` — kind + model + revision
  * ``input_manifest_sha256`` — SHA-256 of the upstream JSONL (so
    downstream stages can fail fast on input change)
  * ``row_count``
  * ``completed_at_utc``
  * ``row_status`` — ``{ok, judge_error, validation_dropped}``

See ``narrative-decision-replication-spec.md`` for the full
pipeline design.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal

__all__ = [
    "PromptRow",
    "StoryRow",
    "FeatureRow",
    "StageSidecar",
    "load_jsonl",
    "write_jsonl",
    "sha256_path",
    "utc_now",
]


# ---------- row schemas --------------------------------------------

Label = Literal["pre_ai_human", "ai_generated"]


@dataclass
class PromptRow:
    prompt_id: str
    source_story_id: str
    prompt_text: str
    target_words: int
    judge_identity: dict[str, Any] = field(default_factory=dict)


@dataclass
class StoryRow:
    story_id: str
    prompt_id: str
    model: str  # "human" | "claude_sonnet_4_6" | ... per paper §2
    label: str  # "pre_ai_human" | "ai_generated"
    story_text: str
    judge_identity: dict[str, Any] = field(default_factory=dict)
    stop_reason: str | None = None
    word_count: int | None = None


@dataclass
class FeatureRow:
    story_id: str
    prompt_id: str
    model: str
    label: str
    narrative_values: dict[str, Any]
    judge_identity: dict[str, Any] = field(default_factory=dict)


# ---------- sidecar manifest ---------------------------------------

@dataclass
class StageSidecar:
    stage: str
    tool: str
    version: str
    prompt_fingerprint_sha256: str | None
    judge_identity: dict[str, Any]
    input_manifest_sha256: str | None
    row_count: int
    completed_at_utc: str
    row_status: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StageSidecar":
        return cls(
            stage=d["stage"],
            tool=d["tool"],
            version=d["version"],
            prompt_fingerprint_sha256=d.get("prompt_fingerprint_sha256"),
            judge_identity=dict(d.get("judge_identity", {})),
            input_manifest_sha256=d.get("input_manifest_sha256"),
            row_count=int(d.get("row_count", 0)),
            completed_at_utc=d["completed_at_utc"],
            row_status=dict(d.get("row_status", {})),
        )

    def write(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )


# ---------- IO ------------------------------------------------------

def load_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Stream-read a JSONL file. Yields one dict per non-empty line."""
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def write_jsonl(path: Path, rows: list[Any]) -> int:
    """Write JSONL. Accepts dataclass rows or plain dicts.

    Returns the number of rows written.
    """
    n = 0
    with Path(path).open("w", encoding="utf-8") as fh:
        for r in rows:
            if hasattr(r, "__dataclass_fields__"):
                d = asdict(r)
            else:
                d = dict(r)
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
            n += 1
    return n


def sha256_path(path: Path, *, chunk: int = 65536) -> str:
    """SHA-256 of a file's bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
