"""Shared, stdlib-only contracts for the spec-78 StoryScope polarity work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import narrative_longform_segment as nls  # type: ignore

__all__ = [
    "FRAME_DOMAINS",
    "framed_digest",
    "framed_file_digest",
    "framed_json_digest",
    "source_work_sha256",
    "count_source_words",
]

# Frozen: each domain names exactly one payload schema in spec 78.
FRAME_DOMAINS = frozenset({
    "setec.voiceprint.spec78.thresholds-file.v1",
    "setec.voiceprint.spec78.registration-file.v1",
    "setec.voiceprint.spec78.manifest-file.v1",
    "setec.voiceprint.spec78.source-envelope-file.v1",
    "setec.voiceprint.spec78.prompt-file.v1",
    "setec.voiceprint.spec78.content-text.v1",
    "setec.voiceprint.spec78.derivation-json.v1",
    "setec.voiceprint.spec78.signal-id-set-json.v1",
    "setec.voiceprint.spec78.work-id-set-json.v1",
    "setec.voiceprint.spec78.design-projection-json.v1",
    "setec.voiceprint.spec78.source-envelope-set-json.v1",
    "setec.voiceprint.spec78.source-work-content.v1",
})


def framed_digest(domain: str, payload: bytes) -> str:
    """Return a domain-separated digest for a registered bytes payload."""
    if domain not in FRAME_DOMAINS:
        raise ValueError(f"unknown spec-78 digest domain: {domain!r}")
    if not isinstance(payload, bytes):
        raise TypeError("framed digest payload must be bytes")
    try:
        domain_bytes = domain.encode("ascii")
    except UnicodeEncodeError as exc:  # defensive; registry is ASCII.
        raise ValueError("digest domain must be ASCII") from exc
    preimage = domain_bytes + b"\n" + len(payload).to_bytes(8, "big") + payload
    return "sha256:" + hashlib.sha256(preimage).hexdigest()


def framed_file_digest(domain: str, path: Path) -> str:
    """Digest exact file bytes under ``domain``."""
    return framed_digest(domain, path.read_bytes())


def framed_json_digest(domain: str, value: Any) -> str:
    """Digest canonical JSON bytes (stable key order and compact encoding)."""
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return framed_digest(domain, payload)


def source_work_sha256(text: str) -> str:
    """Digest exact UTF-8 source-work text under the source-work domain."""
    return framed_digest(
        "setec.voiceprint.spec78.source-work-content.v1", text.encode("utf-8")
    )


def count_source_words(text: str) -> int:
    """Use the shared long-form segmenter's canonical source-word counter."""
    return nls.count_words(text)
