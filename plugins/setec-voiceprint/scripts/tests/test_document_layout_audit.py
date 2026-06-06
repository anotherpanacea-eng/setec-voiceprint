#!/usr/bin/env python3
"""Tests for document_layout_audit.py — the non-voice layout profile."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import document_layout_audit as dla  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

_BODY = ("word " * 360)  # comfortably over the 300-word floor

SAMPLE = f"""# Title

Intro with a [link](https://example.com) and a bare url https://foo.bar/baz .

{_BODY}

## Section A

- one
- two
* three

1. first
2. second

> a quoted line

```
# not a heading inside a fence
- not a list item either
```

| a | b |
| - | - |

---

## Section B

More body. {_BODY}
"""


def test_task_surface_registered():
    assert dla.TASK_SURFACE == "document_layout"
    assert dla.TASK_SURFACE in VALID_TASK_SURFACES


def test_envelope_shape_validates():
    payload = dla.build_payload(
        dla.audit_layout(SAMPLE), target_path="x.md",
        word_count=dla.count_words(SAMPLE), available=True,
    )
    assert payload["task_surface"] == "document_layout"
    assert payload["available"] is True
    assert payload["claim_license"] is not None
    assert payload["tool"] == "document_layout_audit"
    for key in ("headings", "sections", "lists", "links"):
        assert key in payload["results"]


def test_no_verdict_keys():
    r = dla.audit_layout(SAMPLE)
    for forbidden in ("band", "verdict", "compression", "smoothed"):
        assert forbidden not in r


def test_claim_license_refuses_voice_and_ai():
    dn = dla._claim_license().does_not_license.lower()
    assert "voice" in dn and "authorship" in dn and "ai" in dn


def test_heading_and_level_counts():
    r = dla.audit_layout("# A\n\nbody\n\n## B\n\nbody\n\n## C\n\nbody\n")
    assert r["headings"]["count"] == 3
    assert r["headings"]["level_distribution"] == {"h1": 1, "h2": 2}
    assert r["headings"]["max_depth"] == 2
    assert r["headings"]["distinct_levels"] == 2


def test_section_length_variance():
    r = dla.audit_layout(SAMPLE)
    assert r["sections"]["count"] >= 2
    assert r["sections"]["word_count_mean"] is not None
    assert r["sections"]["word_count_sd"] is not None
    assert r["sections"]["coefficient_of_variation"] is not None


def test_list_and_bullet_detection():
    r = dla.audit_layout("- a\n* b\n+ c\n1. d\n2) e\n")
    assert r["lists"]["unordered_items"] == 3
    assert r["lists"]["ordered_items"] == 2
    assert r["lists"]["bullet_markers"] == {"*": 1, "+": 1, "-": 1}


def test_link_density():
    r = dla.audit_layout("see [x](https://a.com) and https://b.com here")
    assert r["links"]["markdown"] == 1
    assert r["links"]["bare_urls"] == 1
    assert r["links"]["total"] == 2


def test_code_fence_content_excluded():
    r = dla.audit_layout(SAMPLE)
    # the fenced "# not a heading" / "- not a list item" must NOT be counted
    assert r["headings"]["count"] == 3          # only the 3 real ATX headings
    assert r["code_blocks"]["fenced_count"] == 1
    assert r["lists"]["unordered_items"] == 3   # the three real bullets only


def test_table_and_thematic_break():
    r = dla.audit_layout(SAMPLE)
    assert r["tables"]["rows"] >= 2
    assert r["thematic_breaks"] >= 1


def test_too_short_is_unavailable(tmp_path):
    f = tmp_path / "short.md"
    f.write_text("# tiny\n\nonly a few words here.\n", encoding="utf-8")
    rc = dla.main([str(f), "--json"])
    assert rc == 0


def test_unavailable_payload_shape():
    payload = dla.build_payload(
        {}, target_path="x.md", word_count=10, available=False,
        warnings=["too short"],
    )
    assert payload["available"] is False
    assert payload["results"] == {}
    assert payload["claim_license"] is None
    assert payload["warnings"]


def test_cli_emits_envelope(tmp_path, capsys):
    f = tmp_path / "doc.md"
    f.write_text(SAMPLE, encoding="utf-8")
    rc = dla.main([str(f), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_surface"] == "document_layout"
    assert payload["available"] is True


def test_deterministic():
    assert dla.audit_layout(SAMPLE) == dla.audit_layout(SAMPLE)
