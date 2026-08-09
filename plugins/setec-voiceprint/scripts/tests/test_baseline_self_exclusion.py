#!/usr/bin/env python3
"""Self-exclusion regression for the six baseline-audit surfaces that share
one shape: a content-duplicate of the target planted in the baseline dir
under a DIFFERENT filename must be dropped before the baseline mean/SD is
built. Otherwise the target pulls its own profile into its own baseline,
deflating every z-score toward a false "in-distribution" result. The
path-only guard misses a copy at a different path; the content-fingerprint
guard closes it.

PR #307 Codex review (whole-cleaned-text, NOT a folded token stream): each
surface's fingerprint is matcher-aligned — sha256 over the WHOLE
``strip_non_prose``-cleaned text, not a lowercased/punctuation-stripped
token stream. An earlier folded fingerprint would OVER-EXCLUDE a baseline
differing only in punctuation/case/composition, which several of these
surfaces score differently (passive/light-verb regexes, sentence
terminators, multi-word markers, per-sentence structure, per-thousand
densities). The cleaned-text hash drops only an EXACT cleaned-text copy
(including one wrapped in stripped front matter) and KEEPS any baseline the
audit scores differently.

Consolidated from six formerly-separate files (each pinned an identical
class/test skeleton for one surface, differing only in the module, the
audit entry point, and the TARGET/OTHER/variant text): agency, discourse,
function_word_grammar, paragraph, punctuation, stance. Every original test
scenario is preserved as a parametrize row below; see the per-surface
comments for provenance. Two surfaces (agency, and the paragraph/
punctuation pair) diverge from the other four in ways real enough to keep
as separate rows/branches rather than force-fit:

  * agency has no backward-compatibility (no-target-fingerprint) test in
    the original suite, and uniquely tests that a RE-CASED copy is kept
    (agency's proper-noun rate is case-sensitive) — preserved below as
    ``test_recased_document_is_kept_case_is_a_signal`` (agency-only, not
    parametrized, matching its original standalone shape).
  * paragraph and punctuation test a Unicode-composition (NFC vs NFD)
    variant instead of a punctuation variant, because their audited signal
    (paragraph/sentence structure; punctuation over the raw character
    sequence) doesn't have a punctuation-insertion regression the way the
    other four do. ``test_variant_not_over_excluded`` below branches on
    ``variant_kind`` to cover both regressions with their original
    (non-interchangeable) assertions intact.

The other seven ``*_self_exclusion*.py`` files (aic_pattern_audit,
crosslingual, dialogue, idiolect, phraseological, productive_roughness,
voice_distance) are NOT part of this family: each exercises a materially
different API shape (different tuple arities, dataclass-based entries,
CLI-level assertions, or extra scenarios with no counterpart here) and
staged forcing them into this row shape would have papered over real
differences rather than removing duplication. They keep their original
per-file test bodies, consolidated instead into
``test_surface_self_exclusion_variants.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pytest

import agency_abstraction_audit as aaa  # type: ignore
import discourse_move_signature as dms  # type: ignore
import function_word_grammar_audit as fwg  # type: ignore
import paragraph_audit as pa  # type: ignore
import punctuation_cadence_audit as pca  # type: ignore
import stance_modality_audit as sma  # type: ignore
from conftest import _cleaned, _names  # noqa: E402


def _front_matter_copy(target: str) -> str:
    return f"---\ntitle: Not The Target\nauthor: Someone Else\n---\n{target}"


@dataclass(frozen=True)
class Surface:
    id: str
    target: str
    other: str
    distinct_b: str
    content_fingerprint: Callable[[str], str]
    run: Callable[..., dict]
    has_backward_compat: bool
    variant_kind: str  # "punct" or "unicode"
    variant_text: str  # punct-variant string, or the NFC-source text for "unicode"
    word_count_fn: Callable[[str], int] | None = None
    check_n_files_on_dup: bool = False


# ---------------------------------------------------------------------------
# agency (formerly test_agency_self_exclusion.py)
# ---------------------------------------------------------------------------
_AGENCY_TARGET = (
    "The transfer was approved by the board and the plan was completed on time. "
    "Katherine Powell reviewed it. The Committee accepted the resolution in March "
    "and the report was filed before the deadline had passed."
) * 5
_AGENCY_OTHER = (
    "Something was decided somewhere by someone, and the matter was closed. "
    "A wall was painted. A field was mown. The results were tabulated quietly "
    "and the whole business was forgotten before the week was out."
) * 5
# Same words, a comma inserted after each "was" ("was approved" -> "was, approved"): the old
# case-preserved token-stream fingerprint dropped the comma and collapsed it into the target; the
# passive regex (`was\s+approved`) no longer matches, so it is a distinct baseline that must be KEPT.
_AGENCY_PUNCT_VARIANT = _AGENCY_TARGET.replace("was ", "was, ")
_AGENCY_DISTINCT_B = (
    "William Hart signed the deed. Chicago confirmed the arrangement. "
    "The Bureau documented the transaction and archived the correspondence."
) * 5

# ---------------------------------------------------------------------------
# discourse (formerly test_discourse_self_exclusion.py)
# ---------------------------------------------------------------------------
_DISCOURSE_TARGET = (
    "However, the point is that the argument holds. Therefore we accept it. "
    "For example, consider the first case; that is, the simplest one. "
    "In other words, the claim is modest, though perhaps still contestable."
) * 5
_DISCOURSE_OTHER = (
    "The river moved slowly under the bridge while the town slept on. "
    "No one watched it go. The lamps burned low and the streets stayed empty "
    "until a grey light crept in from the east and the birds began."
) * 5
_DISCOURSE_PUNCT_VARIANT = _DISCOURSE_TARGET.replace(".", "?")
_DISCOURSE_DISTINCT_B = (
    "A third voice, terse and declarative, states its claims and stops. "
    "It concedes nothing and connects little. Each line ends where it began."
) * 5

# ---------------------------------------------------------------------------
# function_word_grammar (formerly test_function_word_grammar_self_exclusion.py)
# ---------------------------------------------------------------------------
_FWG_TARGET = (
    "The house on the hill was where they had lived for years, and it "
    "was there that the two of them first learned to be still. When the "
    "wind came through, it moved the curtains but not the quiet."
) * 4
_FWG_OTHER = (
    "Beyond the harbor a ship waited under a grey sky, though nobody "
    "aboard could say whether it would sail. If the tide turned, they "
    "would go; if not, they would wait as they always had."
) * 4
_FWG_PUNCT_VARIANT = _FWG_TARGET.replace(".", "?")
_FWG_DISTINCT_B = (
    "A different voice entirely, terse and clipped, with none of the "
    "long clauses the others favored, only short blunt lines."
) * 4

# ---------------------------------------------------------------------------
# stance (formerly test_stance_self_exclusion.py)
# ---------------------------------------------------------------------------
_STANCE_TARGET = (
    "Perhaps this is right, though I suspect the truth is subtler. "
    "Clearly the evidence points one way, but arguably it could point another. "
    "We ought to proceed, and we need to move more or less at once."
) * 5
_STANCE_OTHER = (
    "The cart rolled down the lane and stopped beside the well. "
    "A dog barked twice and went quiet. The afternoon stretched long and "
    "flat over the fields, and nothing at all seemed likely to change."
) * 5
# Same words, a comma inserted mid-marker ("ought to" -> "ought, to", "need to" -> "need, to"): the
# old token-stream fingerprint collapsed it into the target; the markers now match differently, so it
# is a distinct baseline that must be KEPT.
_STANCE_PUNCT_VARIANT = (
    _STANCE_TARGET.replace("ought to", "ought, to").replace("need to", "need, to")
)
_STANCE_DISTINCT_B = (
    "This is certain and requires no hedging. The result is definite. "
    "We assert it plainly and move on without qualification of any kind."
) * 5

# ---------------------------------------------------------------------------
# paragraph (formerly test_paragraph_self_exclusion.py)
# ---------------------------------------------------------------------------
def _paragraph_doc(seed: str) -> str:
    paras = [
        f"{seed} opening paragraph that runs on for a good while so the "
        f"segmentation has something real to chew on and measure here.",
        f"{seed} a shorter second block, still several words long.",
        f"{seed} the third and final paragraph closes the little document "
        f"with a clause or two more and then it simply stops.",
    ]
    return "\n\n".join(paras)


_PARAGRAPH_TARGET = _paragraph_doc("Alpha")
_PARAGRAPH_OTHER = _paragraph_doc("Bravo")
_PARAGRAPH_DISTINCT_B = _paragraph_doc("Charlie")
_PARAGRAPH_UNICODE_BASE = _paragraph_doc("Café résumé naïve façade")

# ---------------------------------------------------------------------------
# punctuation (formerly test_punctuation_self_exclusion.py)
# ---------------------------------------------------------------------------
_PUNCTUATION_TARGET = (
    "The room was quiet — too quiet, perhaps; nobody spoke. She waited "
    "(as one does), counting the seconds. Then: a knock! Who could it be? "
    "The door opened slowly... and there he stood, dripping, silent, unsure."
) * 4
_PUNCTUATION_OTHER = (
    "Rain fell all day and the gutters ran full. The children stayed inside "
    "and read their books and drew their pictures and waited for the sun to "
    "come back out again over the long flat empty fields beyond the town."
) * 4
_PUNCTUATION_DISTINCT_B = (
    "Plain declarative prose. No dashes. No parentheses. Short sentences. "
    "Every line ends with a period and nothing else at all happens here."
) * 4
_PUNCTUATION_UNICODE_BASE = (
    "The café — too quiet, perhaps; nobody spoke. She left her résumé "
    "(naïve, unsigned) on the façade. Then: a knock! Who could it be?"
) * 4


SURFACES = [
    Surface(
        id="agency",
        target=_AGENCY_TARGET,
        other=_AGENCY_OTHER,
        distinct_b=_AGENCY_DISTINCT_B,
        content_fingerprint=aaa._content_fingerprint,
        run=lambda bdir, **kw: aaa.audit_baseline_agency(
            str(bdir), include_filenames=True, **kw
        ),
        has_backward_compat=False,
        variant_kind="punct",
        variant_text=_AGENCY_PUNCT_VARIANT,
    ),
    Surface(
        id="discourse",
        target=_DISCOURSE_TARGET,
        other=_DISCOURSE_OTHER,
        distinct_b=_DISCOURSE_DISTINCT_B,
        content_fingerprint=dms._content_fingerprint,
        run=lambda bdir, **kw: dms.audit_baseline_discourse(
            str(bdir), include_filenames=True, **kw
        ),
        has_backward_compat=True,
        variant_kind="punct",
        variant_text=_DISCOURSE_PUNCT_VARIANT,
    ),
    Surface(
        id="function_word_grammar",
        target=_FWG_TARGET,
        other=_FWG_OTHER,
        distinct_b=_FWG_DISTINCT_B,
        content_fingerprint=fwg._content_fingerprint,
        run=lambda bdir, **kw: fwg.audit_baseline_function_grammar(
            str(bdir), include_filenames=True, **kw
        ),
        has_backward_compat=True,
        variant_kind="punct",
        variant_text=_FWG_PUNCT_VARIANT,
    ),
    Surface(
        id="stance",
        target=_STANCE_TARGET,
        other=_STANCE_OTHER,
        distinct_b=_STANCE_DISTINCT_B,
        content_fingerprint=sma._content_fingerprint,
        run=lambda bdir, **kw: sma.audit_baseline_stance(
            str(bdir), include_filenames=True, **kw
        ),
        has_backward_compat=True,
        variant_kind="punct",
        variant_text=_STANCE_PUNCT_VARIANT,
    ),
    Surface(
        id="paragraph",
        target=_PARAGRAPH_TARGET,
        other=_PARAGRAPH_OTHER,
        distinct_b=_PARAGRAPH_DISTINCT_B,
        content_fingerprint=pa._content_fingerprint,
        run=lambda bdir, **kw: pa.audit_baseline_paragraphs(
            str(bdir), include_filenames=True, **kw
        ),
        has_backward_compat=True,
        variant_kind="unicode",
        variant_text=_PARAGRAPH_UNICODE_BASE,
        word_count_fn=pa.word_count,
        check_n_files_on_dup=True,
    ),
    Surface(
        id="punctuation",
        target=_PUNCTUATION_TARGET,
        other=_PUNCTUATION_OTHER,
        distinct_b=_PUNCTUATION_DISTINCT_B,
        content_fingerprint=pca._content_fingerprint,
        run=lambda bdir, **kw: pca.audit_baseline_punctuation(
            str(bdir), include_filenames=True, **kw
        ),
        has_backward_compat=True,
        variant_kind="unicode",
        variant_text=_PUNCTUATION_UNICODE_BASE,
        word_count_fn=pca._word_count,
        check_n_files_on_dup=True,
    ),
]

BACKWARD_COMPAT_SURFACES = [s for s in SURFACES if s.has_backward_compat]


def _fp(surface: Surface, text: str | None = None) -> str:
    return surface.content_fingerprint(_cleaned(text if text is not None else surface.target))


@pytest.mark.parametrize("surface", SURFACES, ids=lambda s: s.id)
class TestBaselineSelfExclusionFamily:
    def test_content_duplicate_at_other_path_is_excluded(self, tmp_path, surface):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(surface.other, encoding="utf-8")
        (bdir / "sneaky_copy.txt").write_text(surface.target, encoding="utf-8")
        fp = _fp(surface)
        block = surface.run(bdir, target_fingerprint=fp)
        names = _names(block)
        assert "sneaky_copy.txt" not in names  # the target's own copy is dropped
        assert "genuine.txt" in names  # the genuinely-different doc is kept
        if surface.check_n_files_on_dup:
            assert surface.run(bdir, target_fingerprint=fp)["n_files"] == 1

    def test_front_matter_wrapped_copy_is_excluded(self, tmp_path, surface):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(surface.other, encoding="utf-8")
        (bdir / "disguised.txt").write_text(
            _front_matter_copy(surface.target), encoding="utf-8"
        )
        fp = _fp(surface)
        names = _names(surface.run(bdir, target_fingerprint=fp))
        assert "disguised.txt" not in names  # stripped to the target's cleaned text -> dropped
        assert "genuine.txt" in names

    def test_distinct_docs_not_over_excluded(self, tmp_path, surface):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "a.txt").write_text(surface.other, encoding="utf-8")
        (bdir / "b.txt").write_text(surface.distinct_b, encoding="utf-8")
        fp = _fp(surface)
        assert _names(surface.run(bdir, target_fingerprint=fp)) == {"a.txt", "b.txt"}

    def test_variant_not_over_excluded(self, tmp_path, surface):
        if surface.variant_kind == "punct":
            # PR #307 regression: the old folded-token-stream fingerprint over-excluded this;
            # the whole-cleaned-text fingerprint keeps it (a distinct scoring input).
            bdir = tmp_path / "b"
            bdir.mkdir()
            (bdir / "variant.txt").write_text(surface.variant_text, encoding="utf-8")
            (bdir / "genuine.txt").write_text(surface.other, encoding="utf-8")
            fp = _fp(surface)
            names = _names(surface.run(bdir, target_fingerprint=fp))
            assert "variant.txt" in names  # NOT over-excluded
            assert "genuine.txt" in names
        else:
            # Unicode-composition (NFC vs NFD) regression: dropping the prior NFC fold, an NFD
            # copy of the target is a DISTINCT cleaned scoring input (the word tokenizer splits
            # the accented words differently), so it must be KEPT, not over-collapsed.
            import unicodedata

            nfc = unicodedata.normalize("NFC", surface.variant_text)
            nfd = unicodedata.normalize("NFD", nfc)
            assert _cleaned(nfc).encode("utf-8") != _cleaned(nfd).encode("utf-8")
            assert surface.word_count_fn(_cleaned(nfc)) != surface.word_count_fn(_cleaned(nfd))
            assert surface.content_fingerprint(_cleaned(nfc)) != surface.content_fingerprint(
                _cleaned(nfd)
            )

            bdir = tmp_path / "b"
            bdir.mkdir()
            (bdir / "genuine.txt").write_text(surface.other, encoding="utf-8")
            (bdir / "nfd_variant.txt").write_text(nfd, encoding="utf-8")
            block = surface.run(
                bdir, target_fingerprint=surface.content_fingerprint(_cleaned(nfc))
            )
            assert _names(block) == {"genuine.txt", "nfd_variant.txt"}


@pytest.mark.parametrize("surface", BACKWARD_COMPAT_SURFACES, ids=lambda s: s.id)
def test_no_fingerprint_is_backward_compatible(tmp_path, surface):
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "copy.txt").write_text(surface.target, encoding="utf-8")
    block = surface.run(bdir)
    assert _names(block) == {"copy.txt"}


def test_recased_document_is_kept_case_is_a_signal(tmp_path):
    # agency-only: agency's proper-noun rate is case-SENSITIVE, so an all-lowercase copy scores
    # a different profile and is a genuinely different document — it must NOT be over-excluded.
    bdir = tmp_path / "b"
    bdir.mkdir()
    (bdir / "lowercased.txt").write_text(_AGENCY_TARGET.lower(), encoding="utf-8")
    fp = aaa._content_fingerprint(_cleaned(_AGENCY_TARGET))
    result = aaa.audit_baseline_agency(str(bdir), target_fingerprint=fp, include_filenames=True)
    assert "lowercased.txt" in _names(result)
