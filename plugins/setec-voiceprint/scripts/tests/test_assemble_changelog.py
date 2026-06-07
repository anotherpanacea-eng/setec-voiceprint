#!/usr/bin/env python3
"""Tests for the changelog.d/ drop-in + assembler (#170, PR3).

CHANGELOG.md is no longer hand-edited per PR; each PR drops a `changelog.d/
<slug>.md` fragment, and `tools/assemble_changelog.py` cuts a release section
from them. Pinned guarantees:

  * Order-independent + idempotent: output depends only on the fragment set
    (not merge/filename order); zero fragments is a no-op.
  * Faithful render: every token in a fragment body (esp. capability `id`s)
    survives verbatim into CHANGELOG.md, so freshness coverage holds after a cut.
  * Category grouping in Keep-a-Changelog order; new section precedes existing
    versions; malformed fragments are rejected; README.md is ignored.
  * Freshness coverage counts changelog.d/ fragments (a just-shipped capability
    is covered by its fragment until release).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
for p in (str(ROOT / "tools"), str(ROOT / "plugins" / "setec-voiceprint" / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

import assemble_changelog as asm  # type: ignore  # noqa: E402
import check_docs_freshness as cdf  # type: ignore  # noqa: E402

_CHANGELOG_STUB = "# Changelog\n\npreamble\n\n## Unreleased\n\nptr\n\n## [1.0.0] - 2026-01-01\n\nold\n"


def _frag(d: Path, name: str, text: str) -> None:
    (d / name).write_text(text, encoding="utf-8")


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    fdir = tmp_path / "changelog.d"
    fdir.mkdir()
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(_CHANGELOG_STUB, encoding="utf-8")
    return fdir, cl


def test_order_independent(tmp_path):
    """Output depends only on the fragment SET, not the order they're encountered
    (i.e. not on merge order). render_section sorts internally."""
    frags = [("z-one", "Added", "alpha `cap_a`"), ("a-two", "Added", "bravo `cap_b`")]
    s1 = asm.render_section("1.1.0", "2026-02-02", frags)
    s2 = asm.render_section("1.1.0", "2026-02-02", list(reversed(frags)))
    assert s1 == s2
    # deterministic intra-category order is by slug (a-two before z-one)
    assert s1.index("bravo") < s1.index("alpha")


def test_idempotent_noop(tmp_path):
    fdir, cl = _setup(tmp_path)
    before = cl.read_text()
    rc = asm.main(["--version", "9.9.9", "--changelog", str(cl), "--fragment-dir", str(fdir)])
    assert rc == 0
    assert cl.read_text() == before  # no fragments → CHANGELOG untouched


def test_faithful_id_preservation(tmp_path):
    fdir, cl = _setup(tmp_path)
    _frag(fdir, "feat.md", "### Added\n\n**`my_audit.py`** adds `my_capability_id` (additive).\n")
    asm.main(["--version", "1.1.0", "--date", "2026-02-02",
              "--changelog", str(cl), "--fragment-dir", str(fdir)])
    text = cl.read_text()
    assert "my_capability_id" in text  # token survived verbatim
    assert not list(fdir.glob("*.md"))  # fragment consumed


def test_category_grouping_and_order(tmp_path):
    fdir, cl = _setup(tmp_path)
    _frag(fdir, "f.md", "### Fixed\n\nfixed thing\n")
    _frag(fdir, "a.md", "### Added\n\nadded thing\n")
    asm.main(["--version", "1.1.0", "--date", "2026-02-02",
              "--changelog", str(cl), "--fragment-dir", str(fdir)])
    text = cl.read_text()
    assert text.index("### Added") < text.index("### Fixed")  # KaC order
    assert text.index("## [1.1.0]") < text.index("## [1.0.0]")  # new precedes old


def test_bad_fragments_rejected(tmp_path):
    fdir, cl = _setup(tmp_path)
    _frag(fdir, "noheader.md", "just prose, no header\n")
    assert asm.main(["--version", "1.1.0", "--changelog", str(cl), "--fragment-dir", str(fdir)]) == 2
    (fdir / "noheader.md").unlink()
    _frag(fdir, "badcat.md", "### Bogus\n\nbody\n")
    assert asm.main(["--version", "1.1.0", "--changelog", str(cl), "--fragment-dir", str(fdir)]) == 2
    (fdir / "badcat.md").unlink()
    _frag(fdir, "empty.md", "### Added\n\n\n")
    assert asm.main(["--version", "1.1.0", "--changelog", str(cl), "--fragment-dir", str(fdir)]) == 2
    (fdir / "empty.md").unlink()
    # two headers in one fragment → second section would be silently mis-filed
    _frag(fdir, "multi.md", "### Added\n\na\n\n### Fixed\n\nb\n")
    assert asm.main(["--version", "1.1.0", "--changelog", str(cl), "--fragment-dir", str(fdir)]) == 2
    (fdir / "multi.md").unlink()
    # prose before the header → would be silently dropped
    _frag(fdir, "lead.md", "intro prose\n\n### Added\n\nbody\n")
    assert asm.main(["--version", "1.1.0", "--changelog", str(cl), "--fragment-dir", str(fdir)]) == 2


def test_readme_is_ignored(tmp_path):
    fdir, cl = _setup(tmp_path)
    _frag(fdir, "README.md", "# how to write fragments\n")  # not a valid fragment, must be skipped
    _frag(fdir, "feat.md", "### Added\n\nreal `cap_x`\n")
    rc = asm.main(["--version", "1.1.0", "--date", "2026-02-02",
                   "--changelog", str(cl), "--fragment-dir", str(fdir)])
    assert rc == 0
    assert (fdir / "README.md").exists()  # README not consumed


def test_freshness_counts_fragment_coverage(tmp_path):
    """A capability covered only by a changelog.d/ fragment passes; one covered
    nowhere is flagged. Exercises the 2-arg-signature rework."""
    fdir, cl = _setup(tmp_path)
    manifest = tmp_path / "m.yaml"
    manifest.write_text(
        "schema_version: '0.3.0'\nentries:\n"
        "  - id: frag_only_cap\n    surface: validation\n    status: heuristic\n",
        encoding="utf-8",
    )
    # No mention in CHANGELOG, no fragment → flagged missing.
    assert cdf.changelog_coverage(manifest, cl) == ["frag_only_cap"]
    # Add a fragment naming it → covered.
    _frag(fdir, "feat.md", "### Added\n\nships `frag_only_cap`\n")
    assert cdf.changelog_coverage(manifest, cl) == []
