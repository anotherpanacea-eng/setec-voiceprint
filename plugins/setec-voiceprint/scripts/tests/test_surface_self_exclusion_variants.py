#!/usr/bin/env python3
"""Self-exclusion regression tests for seven baseline-audit surfaces that do
NOT share the row-per-surface shape used by ``test_baseline_self_exclusion.py``.

Each class below is one formerly-standalone
``test_<surface>_self_exclusion*.py`` file, consolidated here verbatim (only
renamed to a class-scoped namespace and re-pointed at module-level constants
prefixed per surface, to avoid top-level name collisions now that seven
files share one module). Nothing about a test's assertions, fixtures, or
scenario was changed.

These seven were kept OUT of the parametrized family in
``test_baseline_self_exclusion.py`` because each exercises a materially
different API shape:

  * aic_pattern_audit: ``baseline_density()`` returns a 5-tuple keyed by
    ``Path`` identity (not filenames-in-a-dict), plus a
    ``list_baseline_paths()`` path-guard test and a CLI end-to-end test.
  * crosslingual_voice_distance: ``_load_baseline()`` returns a 4-tuple; its
    matcher (``_normalize`` — NFC + whitespace-collapse, case/punctuation
    PRESERVED) makes a punctuation/case variant a *kept* baseline rather
    than the *excluded* one the other surfaces test, and it uniquely checks
    a whitespace-reformatted copy and CLI `--out` JSON warnings.
  * dialogue_voice_audit: the matcher-aligned unit is the extracted
    dialogue TURN sequence, not the whole file; it tests re-wrapping the
    target's dialogue in different narration, and a no-dialogue-target path
    that must disable the guard entirely (fingerprint is ``None``).
  * idiolect_detector: operates on ``TextEntry`` dataclass lists via
    ``run_idiolect_detector()``, not file paths in a directory; includes a
    fail-closed test where excluding every reference entry must raise
    ``CorpusLoadError`` rather than silently certify against an empty
    reference.
  * phraseological_signature_audit: fingerprints the exact string the audit
    scores (tracking a ``keep_quotes`` flag), with two opposite-outcome
    blockquote tests (excluded by default, kept under ``--keep-quotes``)
    that have no counterpart in the other surfaces.
  * productive_roughness_audit: fingerprints the scored text VERBATIM with
    NO NFC normalization (a deliberate surface-specific policy, the inverse
    of the whole-cleaned-text fingerprint the row-shaped family uses), and
    returns a ``stats`` object rather than a dict.
  * voice_distance: this file predates and is independent of the row-shaped
    family's own voice_distance schema coverage; it drives the CLI
    end-to-end (``main()`` via a patched ``sys.argv``) rather than calling
    the loader directly, and asserts on stderr diagnostic text.

Forcing these into the row-per-surface model used by
``test_baseline_self_exclusion.py`` would have meant either dropping
surface-specific assertions or building a generic self-exclusion-runner
abstraction over incompatible return shapes — exactly the kind of
framework/registry this consolidation pass is not meant to add. Merging
them into one file (this one) still removes the seven separate per-file
docstring/import preambles without touching any test's behavior.
"""

from __future__ import annotations

import sys

import pytest

import aic_pattern_audit as aic  # noqa: E402
import crosslingual_voice_distance as cvd  # noqa: E402
import dialogue_voice_audit as dva  # type: ignore
import phraseological_signature_audit as psa  # type: ignore
import productive_roughness_audit as pra  # type: ignore
import voice_distance as vd  # type: ignore
from idiolect_detector import CorpusLoadError, TextEntry, run_idiolect_detector  # noqa: E402
from conftest import _cleaned  # noqa: E402


# ===========================================================================
# aic_pattern_audit (formerly test_aic_pattern_audit_self_exclusion.py)
#
# Bug (HIGH): ``list_baseline_paths`` / ``baseline_density`` took no target
# and had ZERO exclusion guard, so a target present in ``--baseline-dir``
# (same file, or a content-duplicate at a different name) pools its OWN
# AIC-pattern hits into its own baseline density — understating the
# target's excess over baseline (the whole point of the comparison).
#
# Fix (sibling of the Codex self-exclusion sweep): a baseline path is
# dropped when its resolved path equals the target's (path guard) OR its
# content fingerprint equals the target's (content guard). The fingerprint
# is matcher-aligned: AIC density counts ``\w+`` words and matches frames
# case-insensitively, so the fingerprint is sha256 over the lowercased
# ``\w+`` token stream — a case/punctuation/whitespace variant of the
# target is AIC-equivalent and is self-excluded (fail-closed); a genuinely
# different baseline doc is kept.
# ===========================================================================

# A passage dense in AIC frames so pooling it visibly moves baseline density.
_AIC_TARGET = (
    "Not this. Not that. Research has shown that experts agree on the matter. "
    "We urge the committee to commit to reform. It is not a failure, but a lesson. "
    "There is a kind of clarity in restraint. Not loud. Not proud. "
    "Scholars have argued that the evidence is decisive and final."
)
_AIC_OTHER = (
    "The cat sat quietly by the window while rain fell across the garden. "
    "She counted the drops and lost her place somewhere near the middle. "
    "Later the sky cleared and the street smelled of wet stone and leaves."
)
_AIC_PATTERN_KEYS = list(aic.all_patterns(_AIC_TARGET, aic.split_sentences(_AIC_TARGET)).keys())


class TestAicPatternAuditSelfExclusion:
    def test_baseline_density_excludes_content_duplicate(self, tmp_path):
        copy = tmp_path / "sneaky.txt"
        copy.write_text(_AIC_TARGET, encoding="utf-8")  # a copy of the target under a different name
        other = tmp_path / "genuine.txt"
        other.write_text(_AIC_OTHER, encoding="utf-8")
        fp = aic._content_fingerprint(_AIC_TARGET)
        density, words, loaded, skipped, self_excluded = aic.baseline_density(
            [copy, other], _AIC_PATTERN_KEYS, target_fingerprint=fp)
        assert copy not in loaded          # the target's own copy is not pooled
        assert other in loaded             # the genuinely-different doc is kept
        assert len(self_excluded) == 1

    def test_baseline_density_excludes_case_punct_variant(self, tmp_path):
        # AIC density is case-insensitive and word-token based; a case/punctuation variant is
        # AIC-equivalent -> fail-closed self-exclusion.
        variant = tmp_path / "variant.txt"
        variant.write_text(_AIC_TARGET.upper().replace(".", " . "), encoding="utf-8")
        fp = aic._content_fingerprint(_AIC_TARGET)
        density, words, loaded, skipped, self_excluded = aic.baseline_density(
            [variant], _AIC_PATTERN_KEYS, target_fingerprint=fp)
        assert variant not in loaded
        assert len(self_excluded) == 1

    def test_baseline_density_keeps_distinct_doc(self, tmp_path):
        other = tmp_path / "genuine.txt"
        other.write_text(_AIC_OTHER, encoding="utf-8")
        fp = aic._content_fingerprint(_AIC_TARGET)
        density, words, loaded, skipped, self_excluded = aic.baseline_density(
            [other], _AIC_PATTERN_KEYS, target_fingerprint=fp)
        assert other in loaded
        assert self_excluded == []

    def test_list_baseline_paths_excludes_target_by_path(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        tgt = bdir / "target.md"
        tgt.write_text(_AIC_TARGET, encoding="utf-8")
        (bdir / "genuine.md").write_text(_AIC_OTHER, encoding="utf-8")
        paths = aic.list_baseline_paths(bdir, target_resolved=tgt.resolve())
        names = {p.name for p in paths}
        assert "target.md" not in names
        assert "genuine.md" in names

    def test_end_to_end_self_exclusion_warns(self, tmp_path, capsys):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(_AIC_OTHER, encoding="utf-8")
        (bdir / "copy.txt").write_text(_AIC_TARGET, encoding="utf-8")
        tgt = tmp_path / "target.txt"
        tgt.write_text(_AIC_TARGET, encoding="utf-8")
        rc = aic.main([str(tgt), "--baseline-dir", str(bdir), "--json"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "self-exclusion" in err.lower()


# ===========================================================================
# crosslingual_voice_distance (formerly test_crosslingual_self_exclusion.py)
#
# A content-duplicate (or same-path copy) of the target planted in the
# baseline corpus must be dropped from the baseline BEFORE the distance is
# computed. Otherwise the target pools its own char-n-gram profile into its
# own baseline centroid, deflating `delta`/cosine toward zero (a false
# "on-voice" result).
#
# The fingerprint here is matcher-aligned: crosslingual builds char n-grams
# over ``_normalize`` (NFC + whitespace-collapse + strip, punctuation/case
# PRESERVED), so the self-exclusion fingerprint is sha256 over
# ``_normalize`` — two texts equal under it produce identical char n-grams
# (matcher-equivalent) and are self-excluded; a punctuation/case variant is
# a genuinely different profile to this surface and is correctly KEPT.
# ===========================================================================

def _crosslingual_text(seed: str, n: int = 600) -> str:
    # >= LENGTH_FLOOR_WORDS words so the surface actually produces a distance.
    return " ".join(f"{seed}{i % 37}" for i in range(n))


_XL_TARGET = _crosslingual_text("alpha")
_XL_OTHER = _crosslingual_text("bravo")


class TestCrosslingualVoiceDistanceSelfExclusion:
    def test_content_duplicate_at_other_path_is_excluded(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(_XL_OTHER, encoding="utf-8")
        (bdir / "sneaky_copy.txt").write_text(_XL_TARGET, encoding="utf-8")  # a copy of the target
        fp = cvd._content_fingerprint(_XL_TARGET)
        texts, loaded, words, self_excluded = cvd._load_baseline(str(bdir), target_fingerprint=fp)
        names = {p.name for p in loaded}
        assert "sneaky_copy.txt" not in names  # the target's own copy is dropped
        assert "genuine.txt" in names          # the genuinely-different doc is kept
        assert self_excluded == 1

    def test_target_inside_baseline_dir_excluded_by_path(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        tgt = bdir / "target.txt"
        tgt.write_text(_XL_TARGET, encoding="utf-8")
        (bdir / "genuine.txt").write_text(_XL_OTHER, encoding="utf-8")
        texts, loaded, words, self_excluded = cvd._load_baseline(
            str(bdir), target_resolved=tgt.resolve(),
            target_fingerprint=cvd._content_fingerprint(_XL_TARGET))
        names = {p.name for p in loaded}
        assert "target.txt" not in names
        assert "genuine.txt" in names
        assert self_excluded == 1

    def test_whitespace_variant_is_matcher_equivalent_and_excluded(self, tmp_path):
        # _normalize collapses runs of whitespace; a whitespace-only variant yields identical char
        # n-grams (matcher-equivalent) -> must be self-excluded (fail-closed against a reformatted copy).
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "ws_copy.txt").write_text(_XL_TARGET.replace(" ", "   \n "), encoding="utf-8")
        (bdir / "genuine.txt").write_text(_XL_OTHER, encoding="utf-8")
        texts, loaded, words, self_excluded = cvd._load_baseline(
            str(bdir), target_fingerprint=cvd._content_fingerprint(_XL_TARGET))
        names = {p.name for p in loaded}
        assert "ws_copy.txt" not in names
        assert self_excluded == 1

    def test_distinct_docs_not_over_excluded(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "a.txt").write_text(_XL_OTHER, encoding="utf-8")
        (bdir / "b.txt").write_text(_crosslingual_text("charlie"), encoding="utf-8")
        texts, loaded, words, self_excluded = cvd._load_baseline(
            str(bdir), target_fingerprint=cvd._content_fingerprint(_XL_TARGET))
        assert self_excluded == 0
        assert len(loaded) == 2

    def test_end_to_end_warns_on_self_exclusion(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(_XL_OTHER, encoding="utf-8")
        (bdir / "copy.txt").write_text(_XL_TARGET, encoding="utf-8")
        tgt = tmp_path / "target.txt"
        tgt.write_text(_XL_TARGET, encoding="utf-8")
        out = tmp_path / "out.json"
        rc = cvd.main([
            str(tgt), "--baseline-dir", str(bdir), "--lang", "en", "--json", "--out", str(out),
        ])
        assert rc == 0
        import json
        payload = json.loads(out.read_text())
        warns = payload.get("warnings") or []
        assert any("self-exclusion" in w.lower() for w in warns)

    def test_existing_signature_returns_four_tuple(self, tmp_path):
        # backward-compat: no target params -> nothing excluded, still a valid load.
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "good.txt").write_text(_XL_OTHER, encoding="utf-8")
        texts, loaded, words, self_excluded = cvd._load_baseline(str(bdir))
        assert len(texts) == 1 and words > 0 and self_excluded == 0


# ===========================================================================
# dialogue_voice_audit (formerly test_dialogue_self_exclusion.py)
#
# A content-duplicate of the target's DIALOGUE planted in the baseline dir
# under a DIFFERENT filename must be dropped before the baseline character
# profiles are built. Otherwise the target pools its own per-character
# profiles into the baseline and collapses the divergence matrix toward a
# false "characters converge" result. The path-only guard misses a copy at
# a different path; the content-fingerprint guard closes it.
#
# The matcher-aligned unit is the extracted TURN sequence, not the whole
# file: profiles are built from ``extract_dialogue`` turns and NARRATION is
# ignored, so the fingerprint hashes the ``(speaker, tag_verb, attributed,
# text)`` turn stream. A copy of the target's dialogue — even re-wrapped in
# different narration — is turn-equivalent and self-excluded; a genuinely
# different dialogue is KEPT. A text with no turns fingerprints to ``None``
# (guard disabled — no over-exclusion).
# ===========================================================================

_DIALOGUE_TARGET = (
    '"I won\'t do it," said Mary. "You have to," John replied. '
    '"Says who?" she asked. "Everyone," he answered. '
    '"Then everyone is wrong," Mary said. "Perhaps," John admitted.\n'
) * 3
_DIALOGUE_OTHER = (
    '"Look at the sky," Anna whispered. "It is going to storm," Ben warned. '
    '"We should go inside," she urged. "Not yet," he said. '
    '"Please," Anna begged. "Fine," Ben agreed.\n'
) * 3


def _dialogue_names(loaded):
    return {p.name for p in loaded}


class TestDialogueVoiceAuditSelfExclusion:
    def test_content_duplicate_at_other_path_is_excluded(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(_DIALOGUE_OTHER, encoding="utf-8")
        (bdir / "sneaky_copy.txt").write_text(_DIALOGUE_TARGET, encoding="utf-8")  # a copy of the target
        fp = dva._content_fingerprint(_DIALOGUE_TARGET)
        profiles, words, loaded, skipped = dva.aggregate_baseline(
            bdir, min_turns=1, target_path=None, target_fingerprint=fp,
        )
        names = _dialogue_names(loaded)
        assert "sneaky_copy.txt" not in names   # the target's own dialogue is dropped
        assert "genuine.txt" in names           # the genuinely-different dialogue is kept
        assert any(p.name == "sneaky_copy.txt" for p in skipped)

    def test_same_dialogue_rewrapped_in_narration_is_excluded(self, tmp_path):
        # Narration is ignored by the turn matcher, so a copy wrapped in extra prose (that touches no
        # dialogue tag) extracts the SAME turns and must be self-excluded (fail-closed vs the matcher).
        rewrapped = "the long grey week wore on and nothing seemed to change at all.\n\n" + _DIALOGUE_TARGET
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "rewrapped.txt").write_text(rewrapped, encoding="utf-8")
        (bdir / "genuine.txt").write_text(_DIALOGUE_OTHER, encoding="utf-8")
        fp = dva._content_fingerprint(_DIALOGUE_TARGET)
        profiles, words, loaded, skipped = dva.aggregate_baseline(
            bdir, min_turns=1, target_path=None, target_fingerprint=fp,
        )
        names = _dialogue_names(loaded)
        assert "rewrapped.txt" not in names
        assert "genuine.txt" in names

    def test_distinct_dialogue_not_over_excluded(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "a.txt").write_text(_DIALOGUE_OTHER, encoding="utf-8")
        (bdir / "b.txt").write_text(
            '"Where did they go?" Clara asked. "North," Dan said. '
            '"Why north?" she pressed. "The maps," he shrugged.\n' * 3,
            encoding="utf-8",
        )
        fp = dva._content_fingerprint(_DIALOGUE_TARGET)
        profiles, words, loaded, skipped = dva.aggregate_baseline(
            bdir, min_turns=1, target_path=None, target_fingerprint=fp,
        )
        assert _dialogue_names(loaded) == {"a.txt", "b.txt"}

    def test_no_dialogue_target_disables_content_guard(self, tmp_path):
        # A narration-only target has no turns -> fingerprint None -> the guard must NOT mass-exclude
        # every narration-only baseline file.
        assert dva._content_fingerprint("Just plain narration, no quotes anywhere here.") is None
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "plain.txt").write_text("Plain narration with no dialogue at all in it.", encoding="utf-8")
        profiles, words, loaded, skipped = dva.aggregate_baseline(
            bdir, min_turns=1, target_path=None, target_fingerprint=None,
        )
        assert _dialogue_names(loaded) == {"plain.txt"}


# ===========================================================================
# idiolect_detector (formerly test_idiolect_self_exclusion.py)
#
# Bug (MEDIUM): ``load_target_entries`` / ``load_reference_entries`` draw
# from INDEPENDENT sources with no cross-check. If a target document also
# sits in the reference corpus (same path, or a content duplicate at a
# different path / inline manifest row), the target's own idiolectic words
# appear in the reference too, so keyness (target vs reference) is
# deflated — the writer's distinctive phrases look LESS distinctive than
# they are.
#
# Fix (sibling of the Codex self-exclusion sweep): before scoring, a
# reference entry is dropped when its resolved path equals a target
# entry's (path guard) OR its content fingerprint equals a target entry's
# (content guard). The fingerprint is matcher-aligned: keyness counts
# ``word_tokens`` (lowercased ``[A-Za-z']+``) n-grams, so the fingerprint
# is sha256 over that token stream — a case/punctuation variant of a
# target doc is keyness-equivalent and is self-excluded (fail-closed); a
# genuinely different reference doc is kept.
#
# The fingerprint is computed on the ``strip_non_prose``-cleaned text (the
# same preprocessing ``build_corpus`` scores with), so a reference copy
# that differs from the target only in stripped material (YAML front
# matter, code fences, footers) is still recognized as a duplicate and
# dropped (PR #306) — fingerprinting the raw text would have kept it while
# the matcher scored it identically.
# ===========================================================================

# A target passage with a distinctive repeated phrase ("moral weather").
_IDIOLECT_TARGET_TEXT = (
    "The moral weather shifted again this morning. Moral weather is how I track the room. "
    "When the moral weather turns, I keep a quiet calculus of who stayed and who left. "
    "The moral weather and the quiet calculus are the two instruments I trust."
)
# A genuinely different reference doc (ordinary prose, none of the target's habits).
_IDIOLECT_OTHER_REF = (
    "The train left the station at dawn and rolled north through empty fields. "
    "Passengers dozed against the glass while the conductor called each stop by name. "
    "By noon the mountains rose ahead and the valley fell away behind us."
)


def _idiolect_target():
    return [TextEntry(id="t", path="/corpus/target/t.txt", text=_IDIOLECT_TARGET_TEXT)]


class TestIdiolectDetectorSelfExclusion:
    def test_content_duplicate_of_target_dropped_from_reference(self):
        reference = [
            TextEntry(id="ref_ok", path="/corpus/ref/ok.txt", text=_IDIOLECT_OTHER_REF),
            # a copy of the target planted in the reference at a DIFFERENT path
            TextEntry(id="ref_leak", path="/corpus/ref/leak.txt", text=_IDIOLECT_TARGET_TEXT),
        ]
        result = run_idiolect_detector(_idiolect_target(), reference, n_values=(1, 2))
        assert result["self_exclusion"]["n_reference_dropped"] == 1
        assert result["reference_summary"]["n_files"] == 1
        ref_ids = {f["id"] for f in result["reference_summary"]["files"]}
        assert "ref_leak" not in ref_ids and "ref_ok" in ref_ids

    def test_case_variant_of_target_dropped(self):
        reference = [
            TextEntry(id="ref_ok", path="/corpus/ref/ok.txt", text=_IDIOLECT_OTHER_REF),
            TextEntry(id="ref_leak", path="/corpus/ref/leak.txt", text=_IDIOLECT_TARGET_TEXT.upper()),
        ]
        result = run_idiolect_detector(_idiolect_target(), reference, n_values=(1, 2))
        assert result["self_exclusion"]["n_reference_dropped"] == 1
        assert "ref_leak" not in {f["id"] for f in result["reference_summary"]["files"]}

    def test_front_matter_copy_of_target_dropped(self):
        # PR #306: a reference copy of the target wrapped in YAML front matter. The raw token streams
        # differ (front-matter words), but build_corpus strips the front matter before word_tokens, so
        # the scored inputs are identical -> the guard (now computed on the cleaned text) must drop it.
        fm_copy = (
            "---\ntitle: Not the target\nauthor: Someone Else\ntags: [a, b]\n---\n"
            + _IDIOLECT_TARGET_TEXT
        )
        reference = [
            TextEntry(id="ref_ok", path="/corpus/ref/ok.txt", text=_IDIOLECT_OTHER_REF),
            TextEntry(id="ref_leak", path="/corpus/ref/leak.txt", text=fm_copy),
        ]
        result = run_idiolect_detector(_idiolect_target(), reference, n_values=(1, 2))
        assert result["self_exclusion"]["n_reference_dropped"] == 1
        assert "ref_leak" not in {f["id"] for f in result["reference_summary"]["files"]}

    def test_front_matter_over_distinct_body_not_over_excluded(self):
        # A reference doc that also carries front matter but a genuinely DIFFERENT body must survive:
        # stripping front matter does not collapse distinct prose into the target.
        fm_other = "---\ntitle: Something else\n---\n" + _IDIOLECT_OTHER_REF
        reference = [
            TextEntry(id="ref_a", path="/corpus/ref/a.txt", text=fm_other),
            TextEntry(
                id="ref_b", path="/corpus/ref/b.txt",
                text="A separate essay with its own diction and its own recurring turns of phrase.",
            ),
        ]
        result = run_idiolect_detector(_idiolect_target(), reference, n_values=(1, 2))
        assert result["self_exclusion"]["n_reference_dropped"] == 0
        assert result["reference_summary"]["n_files"] == 2

    def test_reference_at_same_path_dropped(self):
        reference = [
            TextEntry(id="ref_ok", path="/corpus/ref/ok.txt", text=_IDIOLECT_OTHER_REF),
            # same path as the target entry but different text -> path guard still drops it
            TextEntry(id="ref_samepath", path="/corpus/target/t.txt", text=_IDIOLECT_OTHER_REF),
        ]
        result = run_idiolect_detector(_idiolect_target(), reference, n_values=(1, 2))
        assert result["self_exclusion"]["n_reference_dropped"] == 1
        assert "ref_samepath" not in {f["id"] for f in result["reference_summary"]["files"]}

    def test_distinct_reference_not_over_excluded(self):
        reference = [
            TextEntry(id="ref_a", path="/corpus/ref/a.txt", text=_IDIOLECT_OTHER_REF),
            TextEntry(
                id="ref_b", path="/corpus/ref/b.txt",
                text="A different essay entirely, with its own cadence and its own concerns.",
            ),
        ]
        result = run_idiolect_detector(_idiolect_target(), reference, n_values=(1, 2))
        assert result["self_exclusion"]["n_reference_dropped"] == 0
        assert result["reference_summary"]["n_files"] == 2

    def test_reference_emptied_by_exclusion_fails_closed(self):
        # every reference entry is a copy of a target entry -> reference empties -> refuse, never
        # certify a (meaningless) idiolect against an empty reference.
        reference = [TextEntry(id="ref_leak", path="/corpus/ref/leak.txt", text=_IDIOLECT_TARGET_TEXT)]
        with pytest.raises(CorpusLoadError):
            run_idiolect_detector(_idiolect_target(), reference, n_values=(1, 2))


# ===========================================================================
# phraseological_signature_audit (formerly test_phraseological_self_exclusion.py)
#
# A content-duplicate of the target planted in the baseline dir under a
# DIFFERENT filename must be dropped before phrase-frame mining. Otherwise
# the target pools its own frames into its own baseline, inflating every
# reuse / hapax-survival rate toward a false "on-frame" result.
#
# The fingerprint is sha256 over the exact string ``audit_phraseology``
# scores. The scored input tracks ``keep_quotes``: under the default the
# audit strips blockquote lines before tokenizing, so the fingerprint
# strips them too; with ``--keep-quotes`` the quote lines are scored, so a
# quote-bearing variant is KEPT.
# ===========================================================================

_PHRASE_TARGET = (
    " ".join(f"alpha{i % 41} beta{i % 29}" for i in range(120)) + ". What matters is the plan."
)
_PHRASE_OTHER = (
    " ".join(f"gamma{i % 41} delta{i % 29}" for i in range(120)) + ". Consider the river instead."
)
# Same words, punctuation/case changed: the old _tokenize fingerprint collapsed it into the target;
# the whole-text fingerprint keeps it (a distinct scoring input to the slot-frame templates).
_PHRASE_PUNCT_VARIANT = _PHRASE_TARGET.replace(".", "?").replace("What matters", "what matters")
# A copy of the target with an added blockquote line. Under the default keep_quotes=False the audit
# strips ``>`` lines before scoring, so this has the SAME scored input as the target.
_PHRASE_BLOCKQUOTE_COPY = "> a quoted line the audit strips before scoring\n" + _PHRASE_TARGET


class TestPhraseologicalSignatureAuditSelfExclusion:
    def test_content_duplicate_at_other_path_is_excluded(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(_PHRASE_OTHER, encoding="utf-8")
        (bdir / "sneaky_copy.txt").write_text(_PHRASE_TARGET, encoding="utf-8")  # a copy of the target
        fp = psa._content_fingerprint(_PHRASE_TARGET)
        texts, loaded, skipped = psa._walk_baseline(bdir, None, target_fingerprint=fp)
        names = {p.name for p in loaded}
        assert "sneaky_copy.txt" not in names          # the target's own copy is dropped
        assert "genuine.txt" in names                  # the genuinely-different doc is kept
        assert any(p.name == "sneaky_copy.txt" for p in skipped)

    def test_punctuation_and_case_variant_not_over_excluded(self, tmp_path):
        # PR #307 regression: the old _tokenize fingerprint dropped this; whole-text keeps it.
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "variant.txt").write_text(_PHRASE_PUNCT_VARIANT, encoding="utf-8")
        (bdir / "genuine.txt").write_text(_PHRASE_OTHER, encoding="utf-8")
        fp = psa._content_fingerprint(_PHRASE_TARGET)
        texts, loaded, skipped = psa._walk_baseline(bdir, None, target_fingerprint=fp)
        names = {p.name for p in loaded}
        assert "variant.txt" in names                  # NOT over-excluded
        assert "genuine.txt" in names

    def test_blockquote_variant_copy_is_excluded(self, tmp_path):
        # Under default keep_quotes=False the audit strips blockquote lines before scoring, so a copy of
        # the target with added ``>`` lines has the SAME scored input and must be dropped.
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(_PHRASE_OTHER, encoding="utf-8")
        (bdir / "quoted_copy.txt").write_text(_PHRASE_BLOCKQUOTE_COPY, encoding="utf-8")
        fp = psa._content_fingerprint(_PHRASE_TARGET)  # default keep_quotes=False
        texts, loaded, skipped = psa._walk_baseline(bdir, None, target_fingerprint=fp)
        names = {p.name for p in loaded}
        assert "quoted_copy.txt" not in names          # same scored input -> dropped
        assert "genuine.txt" in names
        assert any(p.name == "quoted_copy.txt" for p in skipped)

    def test_blockquote_variant_kept_with_keep_quotes(self, tmp_path):
        # With --keep-quotes the audit scores the quote lines, so the quote-bearing variant is a DISTINCT
        # scored input and must be KEPT. The fingerprint tracks keep_quotes on both sides.
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(_PHRASE_OTHER, encoding="utf-8")
        (bdir / "quoted_copy.txt").write_text(_PHRASE_BLOCKQUOTE_COPY, encoding="utf-8")
        fp = psa._content_fingerprint(_PHRASE_TARGET, keep_quotes=True)
        texts, loaded, skipped = psa._walk_baseline(
            bdir, None, target_fingerprint=fp, keep_quotes=True,
        )
        names = {p.name for p in loaded}
        assert "quoted_copy.txt" in names              # distinct scored input under keep_quotes -> kept
        assert "genuine.txt" in names

    def test_distinct_docs_not_over_excluded(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "a.txt").write_text(_PHRASE_OTHER, encoding="utf-8")
        (bdir / "b.txt").write_text(" ".join(f"epsilon{i}" for i in range(120)), encoding="utf-8")
        fp = psa._content_fingerprint(_PHRASE_TARGET)
        texts, loaded, skipped = psa._walk_baseline(bdir, None, target_fingerprint=fp)
        assert {p.name for p in loaded} == {"a.txt", "b.txt"}
        assert len(texts) == 2

    def test_no_fingerprint_is_backward_compatible(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "copy.txt").write_text(_PHRASE_TARGET, encoding="utf-8")
        texts, loaded, skipped = psa._walk_baseline(bdir, None)
        assert {p.name for p in loaded} == {"copy.txt"}


# ===========================================================================
# productive_roughness_audit (formerly test_productive_roughness_self_exclusion.py)
#
# A content-duplicate of the target planted in the productive-roughness
# baseline dir under a DIFFERENT filename must be dropped before the
# per-feature mean/SD is built. The rates are per-SENTENCE and depend on
# segmentation + spaCy + words, so the fingerprint is sha256 over the WHOLE
# scored text VERBATIM — the exact string ``extract_features`` reads, with
# NO NFC normalization (PR #307 Codex review of the sibling ``voice_distance``
# fix). Its equivalence class is the string itself: an exact-byte copy is
# dropped, and any text the surface would segment/tokenize/score
# differently — including a Unicode-composition variant — is KEPT.
#
# Runs without spaCy: ``aggregate_baseline`` is called directly and
# ``extract_features`` degrades gracefully (fragment/aside signals simply do
# not fire); the loader still loads/excludes/counts.
# ===========================================================================

_ROUGHNESS_TARGET = (
    "The kettle sang. She let it. Outside, a dog barked at nothing much. "
    "And then the rain, sudden and hard, drummed on the tin roof above. "
    "She didn't move. Couldn't, maybe. The moment held her where she sat."
) * 3
_ROUGHNESS_OTHER = (
    "The report concluded that the measures were adequate for the stated "
    "purpose. It recommended a review after twelve months. The committee "
    "accepted the recommendation and adjourned the meeting until the spring."
) * 3
_ROUGHNESS_ACCENTED = (
    "Café mornings begin quietly. The résumé sat unread on the table. "
    "A naïve hope, perhaps, but hers to keep. She paused at the façade. "
    "Then the séance guests filed in, and the café door closed softly."
) * 3


def _roughness_names(stats):
    return {p.name for p in stats.files_loaded}


class TestProductiveRoughnessAuditSelfExclusion:
    def test_content_duplicate_at_other_path_is_excluded(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(_ROUGHNESS_OTHER, encoding="utf-8")
        (bdir / "sneaky_copy.txt").write_text(_ROUGHNESS_TARGET, encoding="utf-8")  # a copy of the target
        fp = pra._content_fingerprint(_ROUGHNESS_TARGET)
        stats = pra.aggregate_baseline(bdir, target_fingerprint=fp)
        names = _roughness_names(stats)
        assert "sneaky_copy.txt" not in names   # the target's own copy is dropped
        assert "genuine.txt" in names           # the genuinely-different doc is kept
        assert stats.n_files == 1

    def test_distinct_docs_not_over_excluded(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "a.txt").write_text(_ROUGHNESS_OTHER, encoding="utf-8")
        (bdir / "b.txt").write_text(
            "A wholly different draft. Long, unbroken, careful sentences that "
            "never fragment and never lean on a contraction of any kind at all." * 3,
            encoding="utf-8",
        )
        fp = pra._content_fingerprint(_ROUGHNESS_TARGET)
        stats = pra.aggregate_baseline(bdir, target_fingerprint=fp)
        assert _roughness_names(stats) == {"a.txt", "b.txt"}
        assert stats.n_files == 2

    def test_unicode_composition_variant_not_over_excluded(self, tmp_path):
        # A baseline file that is the target's text in a DIFFERENT Unicode composition
        # (NFD vs NFC) is a distinct byte string that this surface's word tokenizer
        # splits differently (word counts diverge). The exact-string policy makes the
        # fingerprint's equivalence class the string itself, so the variant is KEPT;
        # the prior NFC-folded fingerprint collapsed the two and could OVER-EXCLUDE it.
        import unicodedata

        nfc = unicodedata.normalize("NFC", _ROUGHNESS_ACCENTED)
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfc.encode("utf-8") != nfd.encode("utf-8")
        assert pra.count_words(nfc) != pra.count_words(nfd)  # tokenized differently
        assert pra._content_fingerprint(nfc) != pra._content_fingerprint(nfd)  # no NFC fold

        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "genuine.txt").write_text(_ROUGHNESS_OTHER, encoding="utf-8")
        (bdir / "nfd_variant.txt").write_text(nfd, encoding="utf-8")
        stats = pra.aggregate_baseline(bdir, target_fingerprint=pra._content_fingerprint(nfc))
        names = _roughness_names(stats)
        assert "nfd_variant.txt" in names   # distinct composition is KEPT, not over-excluded
        assert "genuine.txt" in names
        assert stats.n_files == 2

    def test_no_fingerprint_is_backward_compatible(self, tmp_path):
        bdir = tmp_path / "b"
        bdir.mkdir()
        (bdir / "copy.txt").write_text(_ROUGHNESS_TARGET, encoding="utf-8")
        stats = pra.aggregate_baseline(bdir)
        assert _roughness_names(stats) == {"copy.txt"}


# ===========================================================================
# voice_distance (formerly test_voice_distance_self_exclusion.py)
#
# A content-duplicate of the target placed in the voice-distance baseline
# under a DIFFERENT filename must be dropped before the distance is
# computed. Otherwise the target pools its own function-word vector into
# its own baseline centroid, collapsing the cosine min / Burrows Delta
# toward 0 (a false "on-voice" result).
#
# The fingerprint is matcher-aligned to ALL scored families, not just the
# function-word tokenizer: it is sha256 over the WHOLE
# ``strip_non_prose``-cleaned text — the single string every family
# (function words, char n-grams, POS, dependencies) reads before its own
# normalization. So the equivalence class is a strict SUBSET of every
# family's class: an exact copy (even one wrapped in front matter the
# preprocessing strips) is dropped, while a punctuation-/case-distinct
# baseline the char-n-gram/POS families treat as distinct is KEPT rather
# than over-excluded (PR #307).
# ===========================================================================

_VD_TARGET = (
    "Officials noted that the process had followed the established guidelines, "
    "and that the review would continue through the winter into the early spring. "
) * 12
_VD_G1 = "The committee deliberated through the long grey afternoon and into the evening. " * 12
_VD_G2 = "Members reviewed the budget on Tuesday and again, more carefully, on the Thursday. " * 12

# Same words as TARGET with the sentence punctuation removed. The char-n-gram / POS families
# score this differently from TARGET, so the guard must NOT collapse it into the target (PR #307).
_VD_TARGET_NO_PUNCT = _VD_TARGET.replace(",", "").replace(".", "")
# An exact copy of TARGET wrapped in YAML front matter under a different apparent identity. The
# default strip rules remove the front matter, so the cleaned scoring input is identical to the
# target's and the entry must be dropped — self-exclusion computed on the preprocessed input.
_VD_TARGET_WITH_FRONT_MATTER = f"---\ntitle: Not The Target\nauthor: Someone Else\n---\n{_VD_TARGET}"


def _run_vd_main(argv):
    orig = sys.argv
    sys.argv = argv
    try:
        return vd.main()
    finally:
        sys.argv = orig


class TestVoiceDistanceSelfExclusion:
    def test_content_fingerprint_keys_on_whole_cleaned_text(self):
        # Fingerprint is over the cleaned string itself, so it is stable and distinguishes distinct texts.
        assert vd._content_fingerprint(_cleaned(_VD_TARGET)) == vd._content_fingerprint(_cleaned(_VD_TARGET))
        assert vd._content_fingerprint(_cleaned(_VD_TARGET)) != vd._content_fingerprint(_cleaned(_VD_G1))

    def test_fingerprint_does_not_collapse_punctuation_variants(self):
        # PR #307: a word-only fingerprint folded punctuation and treated these as identical, dropping a
        # baseline the actual matcher considers distinct. The whole-cleaned-text fingerprint keeps them apart.
        assert _cleaned(_VD_TARGET) != _cleaned(_VD_TARGET_NO_PUNCT)
        assert vd._content_fingerprint(_cleaned(_VD_TARGET)) != vd._content_fingerprint(
            _cleaned(_VD_TARGET_NO_PUNCT)
        )

    def test_front_matter_copy_shares_target_fingerprint(self):
        # Self-exclusion is computed on the preprocessed input: front matter is stripped, so an exact copy
        # wrapped in front matter has the same cleaned string as the target and the same fingerprint.
        assert vd._content_fingerprint(_cleaned(_VD_TARGET_WITH_FRONT_MATTER)) == vd._content_fingerprint(
            _cleaned(_VD_TARGET)
        )

    def test_content_duplicate_at_other_path_is_dropped(self, tmp_path, capsys):
        bdir = tmp_path / "baseline"
        bdir.mkdir()
        (bdir / "genuine1.md").write_text(_VD_G1, encoding="utf-8")
        (bdir / "genuine2.md").write_text(_VD_G2, encoding="utf-8")
        (bdir / "sneaky_copy.md").write_text(_VD_TARGET, encoding="utf-8")  # a copy of the target, other name
        target = tmp_path / "target.md"
        target.write_text(_VD_TARGET, encoding="utf-8")

        rc = _run_vd_main([
            "voice_distance.py", str(target),
            "--baseline-dir", str(bdir), "--no-spacy", "--json",
        ])
        err = capsys.readouterr().err
        assert rc == 0
        # The differently-named copy was dropped by the content guard (target is OUTSIDE bdir, so the
        # only possible reason for a drop is a content match).
        assert "content-duplicate" in err

    def test_front_matter_copy_at_other_path_is_dropped(self, tmp_path, capsys):
        # PR #307 / #306 alignment: a copy that differs only in stripped front matter is still a copy once
        # preprocessing runs, so it must be dropped — the guard fingerprints the cleaned scoring input.
        bdir = tmp_path / "baseline"
        bdir.mkdir()
        (bdir / "genuine1.md").write_text(_VD_G1, encoding="utf-8")
        (bdir / "disguised_copy.md").write_text(_VD_TARGET_WITH_FRONT_MATTER, encoding="utf-8")
        target = tmp_path / "target.md"
        target.write_text(_VD_TARGET, encoding="utf-8")

        rc = _run_vd_main([
            "voice_distance.py", str(target),
            "--baseline-dir", str(bdir), "--no-spacy", "--json",
        ])
        err = capsys.readouterr().err
        assert rc == 0
        assert "content-duplicate" in err

    def test_punctuation_variant_baseline_not_over_excluded(self, tmp_path, capsys):
        # PR #307 regression: a baseline that is the target with punctuation removed is a genuinely distinct
        # document to the char-n-gram / POS families and must survive the guard (not dropped as a duplicate).
        bdir = tmp_path / "baseline"
        bdir.mkdir()
        (bdir / "genuine1.md").write_text(_VD_G1, encoding="utf-8")
        (bdir / "punct_variant.md").write_text(_VD_TARGET_NO_PUNCT, encoding="utf-8")
        target = tmp_path / "target.md"
        target.write_text(_VD_TARGET, encoding="utf-8")

        rc = _run_vd_main([
            "voice_distance.py", str(target),
            "--baseline-dir", str(bdir), "--no-spacy", "--json",
        ])
        err = capsys.readouterr().err
        assert rc == 0
        # Neither baseline is the target or an exact-cleaned copy of it -> nothing dropped.
        assert "Dropped target file" not in err

    def test_distinct_baseline_not_over_excluded(self, tmp_path, capsys):
        bdir = tmp_path / "baseline"
        bdir.mkdir()
        (bdir / "genuine1.md").write_text(_VD_G1, encoding="utf-8")
        (bdir / "genuine2.md").write_text(_VD_G2, encoding="utf-8")
        target = tmp_path / "target.md"
        target.write_text(_VD_TARGET, encoding="utf-8")

        rc = _run_vd_main([
            "voice_distance.py", str(target),
            "--baseline-dir", str(bdir), "--no-spacy", "--json",
        ])
        err = capsys.readouterr().err
        assert rc == 0
        # No entry is the target or a content-duplicate of it -> nothing is dropped.
        assert "Dropped target file" not in err
