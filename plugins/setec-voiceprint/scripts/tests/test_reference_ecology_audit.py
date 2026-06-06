#!/usr/bin/env python3
"""Tests for reference_ecology_audit.py — the non-voice reference-ecology profile."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import reference_ecology_audit as rea  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402

_BODY = ("word " * 360)

SAMPLE = f"""# On Things

As Smith argues, the field shifted (Smith, 2019). Others disagree (Doe et al., 2020).

{_BODY}

According to Jones, this matters. See the paper at https://example.com/p and
also https://example.com/q and a third at https://other.org/x .

> a quoted block line

He said "this is a direct quote" in the text.

A note[^1] and another[^2].

[^1]: first footnote.
[^2]: second footnote.

doi:10.1234/abcd.5678 and arXiv:2401.01234 for the curious.
"""


def test_task_surface_registered():
    assert rea.TASK_SURFACE == "reference_ecology"
    assert rea.TASK_SURFACE in VALID_TASK_SURFACES


def test_envelope_shape_validates():
    payload = rea.build_payload(
        rea.audit_references(SAMPLE), target_path="x.md",
        word_count=rea.count_words(SAMPLE), available=True,
    )
    assert payload["task_surface"] == "reference_ecology"
    assert payload["available"] is True
    assert payload["claim_license"] is not None
    for key in ("citations", "footnotes", "attribution", "quotation", "links"):
        assert key in payload["results"]


def test_no_verdict_keys():
    r = rea.audit_references(SAMPLE)
    for forbidden in ("band", "verdict", "compression", "smoothed"):
        assert forbidden not in r


def test_claim_license_refuses_voice_and_flags_topic():
    lic = rea._claim_license()
    dn = lic.does_not_license.lower()
    assert "voice" in dn and "authorship" in dn and "ai" in dn
    assert any("topic" in c.lower() for c in lic.additional_caveats)


def test_parenthetical_citation_count():
    r = rea.audit_references(
        "claim (Smith, 2019) and (Doe et al., 2020) but not (just a note) "
        "nor (lowercase, 2019)"
    )
    assert r["citations"]["parenthetical"] == 2


def test_doi_arxiv_etal():
    r = rea.audit_references("doi:10.1234/abcd and arXiv:2401.05678 by Smith et al. here")
    assert r["citations"]["doi"] == 1
    assert r["citations"]["arxiv"] == 1
    assert r["citations"]["et_al"] == 1


def test_footnote_refs_and_defs():
    r = rea.audit_references("text[^1] more[^2]\n\n[^1]: a\n[^2]: b\n")
    assert r["footnotes"]["refs"] == 2
    assert r["footnotes"]["definitions"] == 2


def test_attribution_constructions():
    r = rea.audit_references("According to Smith it holds. Jones argues otherwise.")
    assert r["attribution"]["phrases"] == 2


def test_quotation_pairs_and_blockquote():
    r = rea.audit_references('He said "a quote" today.\n> a block line\n')
    assert r["quotation"]["inline_pairs"] == 1
    assert r["quotation"]["blockquote_lines"] == 1


def test_link_domain_breadth():
    r = rea.audit_references(
        "see https://example.com/a and https://example.com/b and https://other.org/c"
    )
    assert r["links"]["total"] == 3
    assert r["links"]["distinct_domains"] == 2


def test_www_domain_normalized():
    r = rea.audit_references("a https://www.wired.com/x and https://wired.com/y")
    # www.wired.com and wired.com collapse to one domain
    assert r["links"]["distinct_domains"] == 1


def test_too_short_unavailable(tmp_path):
    f = tmp_path / "short.md"
    f.write_text("only a few words here, no references.\n", encoding="utf-8")
    assert rea.main([str(f), "--json"]) == 0


def test_unavailable_payload_shape():
    payload = rea.build_payload(
        {}, target_path="x.md", word_count=10, available=False, warnings=["short"],
    )
    assert payload["available"] is False
    assert payload["results"] == {}
    assert payload["claim_license"] is None


def test_cli_emits_envelope(tmp_path, capsys):
    f = tmp_path / "doc.md"
    f.write_text(SAMPLE, encoding="utf-8")
    assert rea.main([str(f), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_surface"] == "reference_ecology"
    assert payload["available"] is True


def test_deterministic():
    assert rea.audit_references(SAMPLE) == rea.audit_references(SAMPLE)
