#!/usr/bin/env python3
"""Tests for acquire_corpus_template.py + corpus-acquisition skill.

The template ships as a SCAFFOLD, not a working script. Its tests
are different in shape from the per-source acquisition tests:
they verify the structural contract (CLI surface, the four
``TODO(LLM)`` markers, importability without raising on import,
the skill's discoverability, the reference doc's existence) but
do NOT exercise the discovery/extract path — those raise
NotImplementedError until a copy of the template is filled in.

Tests verify:

  * The template imports cleanly (no syntax errors, no missing
    deps from acquisition_core).
  * The four required ``TODO(LLM)`` markers are present and the
    stubs raise NotImplementedError so a forgotten fill-in doesn't
    silently produce a no-op script.
  * The CLI surface matches the standard acquisition flag set
    (the same flags every shipped acquisition script honors).
  * ProcessOptions and ItemMeta dataclasses exist with the
    documented fields.
  * The reference doc exists and the skill markdown exists and
    references both the template and the reference.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import acquire_corpus_template as tmpl  # type: ignore


PLUGIN_ROOT = ROOT.parent
TEMPLATE_PATH = ROOT / "acquire_corpus_template.py"
REFERENCE_DOC_PATH = (
    PLUGIN_ROOT / "references" / "acquire-corpus-pattern.md"
)
SKILL_MD_PATH = PLUGIN_ROOT / "skills" / "corpus-acquisition" / "SKILL.md"


# ------------------- Template structure --------------------------


def test_template_file_exists():
    assert TEMPLATE_PATH.is_file()


def test_template_imports_cleanly():
    """If the template fails to import, every adaptation also fails.
    The import-time test catches regressions in shared
    `acquisition_core` helpers that the template depends on."""
    # Import already happened at the top of this file. Just assert
    # the module is loaded and exposes the documented API.
    assert hasattr(tmpl, "discover_items")
    assert hasattr(tmpl, "extract_one")
    assert hasattr(tmpl, "build_arg_parser")
    assert hasattr(tmpl, "parse_options")
    assert hasattr(tmpl, "run")


def test_template_has_todo_llm_markers():
    """The template's adaptation surface is documented via four
    ``TODO(LLM)`` markers. Removing them silently lets a
    mis-adapted copy ship as no-op; pin that they're present in
    the template source."""
    source = TEMPLATE_PATH.read_text(encoding="utf-8")
    todo_count = source.count("TODO(LLM)")
    # Four primary markers (discover_items, extract_one,
    # build_arg_parser additions, parse_options additions) plus
    # the SOURCE_NAME edit-before-use marker. Allow ≥ 4.
    assert todo_count >= 4, (
        f"expected at least 4 TODO(LLM) markers, found {todo_count}. "
        "Adaptation guidance lives in those markers."
    )


def test_template_marks_source_name_for_replacement():
    """SOURCE_NAME and TOOL_NAME are placeholder strings the user
    must replace. Pin that they're loud-enough to notice."""
    assert "TODO" in tmpl.SOURCE_NAME or "todo" in tmpl.SOURCE_NAME.lower()


def test_discover_items_stub_raises_not_implemented():
    """Forgotten fill-in must fail loudly, not silently."""
    options = _make_minimal_options()
    if pytest is not None:
        with pytest.raises(NotImplementedError):
            list(tmpl.discover_items("dummy_source", options))


def test_extract_one_stub_raises_not_implemented():
    options = _make_minimal_options()
    item = tmpl.ItemMeta(locator="dummy")
    if pytest is not None:
        with pytest.raises(NotImplementedError):
            tmpl.extract_one(item, "dummy_source", options)


# ------------------- Dataclass shapes ----------------------------


def test_item_meta_has_documented_fields():
    item = tmpl.ItemMeta(
        locator="path/or/url",
        title="Some Title",
        author="Some Author",
    )
    assert item.locator == "path/or/url"
    assert item.title == "Some Title"
    assert item.author == "Some Author"
    assert item.date is None
    assert isinstance(item.extra, dict)


def test_process_options_has_required_fields():
    options = _make_minimal_options()
    # Fields documented in the pattern reference.
    expected = {
        "persona", "impostor_for", "register", "register_match",
        "topic_match", "consent_status", "era", "since", "until",
        "output_dir", "manifest_path", "max_items", "dry_run",
        "allow_non_prose", "strip_rules", "strip_aggressive",
        "acquired_via", "source_extras",
    }
    actual = set(options.__dataclass_fields__.keys())
    missing = expected - actual
    assert not missing, f"ProcessOptions missing fields: {missing}"


def _make_minimal_options() -> tmpl.ProcessOptions:
    return tmpl.ProcessOptions(
        persona="test_personal",
        impostor_for=["fiction"],
        register="blog_essay",
        register_match="high",
        topic_match="medium",
        consent_status="fair_use_research",
        era="pre_chatgpt",
        since=None,
        until=None,
        output_dir=Path("/tmp/x"),
        manifest_path=Path("/tmp/x/draft.jsonl"),
        max_items=10,
        dry_run=False,
        allow_non_prose=False,
        strip_rules=None,
        strip_aggressive=False,
        acquired_via="acquire_template_test",
    )


# ------------------- CLI surface ---------------------------------


def test_cli_help_lists_standard_acquisition_flags():
    """The template's --help must list every flag the existing
    acquisition scripts share. The pattern reference documents
    these as canonical."""
    parser = tmpl.build_arg_parser()
    help_text = parser.format_help()
    standard_flags = (
        "--persona", "--impostor-for", "--register", "--register-match",
        "--topic-match", "--consent-status", "--era",
        "--since", "--until", "--max-items",
        "--output-dir", "--emit-manifest", "--out",
        "--rate-limit", "--user-agent",
        "--dry-run", "--allow-public-output",
        "--allow-non-prose", "--strip-rules", "--strip-aggressive",
    )
    for flag in standard_flags:
        assert flag in help_text, f"--help missing {flag}"


def test_cli_requires_impostor_for_register_consent_status():
    parser = tmpl.build_arg_parser()
    if pytest is not None:
        with pytest.raises(SystemExit):
            parser.parse_args(["dummy_source"])  # missing required flags


def test_cli_accepts_minimal_required_args():
    parser = tmpl.build_arg_parser()
    args = parser.parse_args([
        "dummy_source",
        "--impostor-for", "fiction",
        "--register", "blog_essay",
        "--consent-status", "fair_use_research",
    ])
    assert args.source == "dummy_source"
    assert args.impostor_for == ["fiction"]


def test_parse_options_resolves_default_output_dir(tmp_path):
    """When --output-dir isn't given, parse_options falls back to
    ``acquisition_core.default_output_dir`` so the script lands in
    the right place."""
    parser = tmpl.build_arg_parser()
    args = parser.parse_args([
        "dummy_source",
        "--persona", "test_personal",
        "--impostor-for", "fiction",
        "--register", "blog_essay",
        "--consent-status", "fair_use_research",
    ])
    options = tmpl.parse_options(args)
    # Default output_dir should be under the configured baselines
    # root and follow the impostors/<register>/<persona>/ convention.
    assert "impostors" in str(options.output_dir)
    assert "blog_essay" in str(options.output_dir)
    assert "test_personal" in str(options.output_dir)


def test_acquired_via_tag_includes_source_and_date():
    tag = tmpl.build_acquired_via_tag()
    assert "acquire_" in tag
    # Today's date stamp.
    import datetime as _dt
    assert _dt.date.today().isoformat() in tag


# ------------------- Reference doc + skill -----------------------


def test_reference_doc_exists():
    assert REFERENCE_DOC_PATH.is_file(), \
        f"reference doc not found at {REFERENCE_DOC_PATH}"


def test_reference_doc_documents_pipeline():
    """The reference's section structure is what an LLM grounds
    from. Pin the canonical headings."""
    text = REFERENCE_DOC_PATH.read_text(encoding="utf-8")
    expected_headings = (
        "## When to reach for this",
        "## The pipeline",
        "## What `acquisition_core.py` gives you",
        "## The CLI conventions every acquisition script follows",
        "## What the source-specific code has to implement",
        "## Testing pattern",
        "## Working with an LLM",
    )
    for h in expected_headings:
        assert h in text, f"reference missing heading: {h}"


def test_reference_doc_lists_acquisition_core_helpers():
    """The reference must enumerate the helpers a new script will
    consume — slugify, content_hash_already_present, html_to_text,
    AcquiredPiece, RunSummary, etc. — so an LLM has the helper
    names it needs."""
    text = REFERENCE_DOC_PATH.read_text(encoding="utf-8")
    helper_names = (
        "slugify", "compute_content_hash", "is_private_safe_path",
        "check_output_privacy", "Fetcher", "FixtureFetcher",
        "make_requests_fetcher", "preprocess_text", "html_to_text",
        "AcquiredPiece", "RunSummary", "write_piece",
        "content_hash_already_present", "compose_manifest_entry",
        "append_manifest_entry",
    )
    for name in helper_names:
        assert name in text, f"reference missing helper: {name}"


def test_skill_md_exists_and_references_template_and_reference():
    """The skill must point an LLM at both the reference doc and the
    template — those are the two artifacts an adaptation
    consumes."""
    assert SKILL_MD_PATH.is_file(), f"skill md not found at {SKILL_MD_PATH}"
    text = SKILL_MD_PATH.read_text(encoding="utf-8")
    assert "acquire-corpus-pattern.md" in text
    assert "acquire_corpus_template.py" in text
    assert "${CLAUDE_PLUGIN_ROOT}" in text


def test_skill_md_walks_through_six_workflow_steps():
    """The skill's documented workflow has six numbered steps. Pin
    that the headings exist in the right order."""
    text = SKILL_MD_PATH.read_text(encoding="utf-8")
    steps = [
        "### Step 1:",
        "### Step 2:",
        "### Step 3:",
        "### Step 4:",
        "### Step 5:",
        "### Step 6:",
    ]
    last_idx = -1
    for s in steps:
        idx = text.find(s)
        assert idx > last_idx, f"step out of order or missing: {s}"
        last_idx = idx


def test_skill_md_lists_consent_status_options():
    """The consent-status decision is required. Pin that the skill
    enumerates the five options so an LLM doesn't invent a sixth."""
    text = SKILL_MD_PATH.read_text(encoding="utf-8")
    for status in (
        "public_record", "cc_licensed", "fair_use_research",
        "author_consent", "undocumented",
    ):
        assert status in text


def test_skill_md_includes_concrete_example():
    """Walking through a concrete adaptation makes the abstract
    pattern grounded. Pin that at least one example workflow is
    present."""
    text = SKILL_MD_PATH.read_text(encoding="utf-8")
    # At least one of the example sources mentioned in the
    # 'Common patterns' or 'Example' section.
    assert any(
        keyword in text
        for keyword in ("Slack", "Obsidian", "mbox", "Notion", "Discord")
    )


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
