"""Self-exclusion regression for idiolect_detector.

Bug (MEDIUM): ``load_target_entries`` / ``load_reference_entries`` draw from INDEPENDENT sources with
no cross-check. If a target document also sits in the reference corpus (same path, or a content
duplicate at a different path / inline manifest row), the target's own idiolectic words appear in the
reference too, so keyness (target vs reference) is deflated — the writer's distinctive phrases look
LESS distinctive than they are.

Fix (sibling of the Codex self-exclusion sweep): before scoring, a reference entry is dropped when its
resolved path equals a target entry's (path guard) OR its content fingerprint equals a target entry's
(content guard). The fingerprint is matcher-aligned: keyness counts ``word_tokens`` (lowercased
``[A-Za-z']+``) n-grams, so the fingerprint is sha256 over that token stream — a case/punctuation
variant of a target doc is keyness-equivalent and is self-excluded (fail-closed); a genuinely
different reference doc is kept.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from idiolect_detector import CorpusLoadError, TextEntry, run_idiolect_detector  # noqa: E402

# A target passage with a distinctive repeated phrase ("moral weather").
TARGET_TEXT = (
    "The moral weather shifted again this morning. Moral weather is how I track the room. "
    "When the moral weather turns, I keep a quiet calculus of who stayed and who left. "
    "The moral weather and the quiet calculus are the two instruments I trust."
)
# A genuinely different reference doc (ordinary prose, none of the target's habits).
OTHER_REF = (
    "The train left the station at dawn and rolled north through empty fields. "
    "Passengers dozed against the glass while the conductor called each stop by name. "
    "By noon the mountains rose ahead and the valley fell away behind us."
)


def _target():
    return [TextEntry(id="t", path="/corpus/target/t.txt", text=TARGET_TEXT)]


def test_content_duplicate_of_target_dropped_from_reference():
    reference = [
        TextEntry(id="ref_ok", path="/corpus/ref/ok.txt", text=OTHER_REF),
        # a copy of the target planted in the reference at a DIFFERENT path
        TextEntry(id="ref_leak", path="/corpus/ref/leak.txt", text=TARGET_TEXT),
    ]
    result = run_idiolect_detector(_target(), reference, n_values=(1, 2))
    assert result["self_exclusion"]["n_reference_dropped"] == 1
    assert result["reference_summary"]["n_files"] == 1
    ref_ids = {f["id"] for f in result["reference_summary"]["files"]}
    assert "ref_leak" not in ref_ids and "ref_ok" in ref_ids


def test_case_variant_of_target_dropped():
    reference = [
        TextEntry(id="ref_ok", path="/corpus/ref/ok.txt", text=OTHER_REF),
        TextEntry(id="ref_leak", path="/corpus/ref/leak.txt", text=TARGET_TEXT.upper()),
    ]
    result = run_idiolect_detector(_target(), reference, n_values=(1, 2))
    assert result["self_exclusion"]["n_reference_dropped"] == 1
    assert "ref_leak" not in {f["id"] for f in result["reference_summary"]["files"]}


def test_reference_at_same_path_dropped():
    reference = [
        TextEntry(id="ref_ok", path="/corpus/ref/ok.txt", text=OTHER_REF),
        # same path as the target entry but different text -> path guard still drops it
        TextEntry(id="ref_samepath", path="/corpus/target/t.txt", text=OTHER_REF),
    ]
    result = run_idiolect_detector(_target(), reference, n_values=(1, 2))
    assert result["self_exclusion"]["n_reference_dropped"] == 1
    assert "ref_samepath" not in {f["id"] for f in result["reference_summary"]["files"]}


def test_distinct_reference_not_over_excluded():
    reference = [
        TextEntry(id="ref_a", path="/corpus/ref/a.txt", text=OTHER_REF),
        TextEntry(id="ref_b", path="/corpus/ref/b.txt",
                  text="A different essay entirely, with its own cadence and its own concerns."),
    ]
    result = run_idiolect_detector(_target(), reference, n_values=(1, 2))
    assert result["self_exclusion"]["n_reference_dropped"] == 0
    assert result["reference_summary"]["n_files"] == 2


def test_reference_emptied_by_exclusion_fails_closed():
    # every reference entry is a copy of a target entry -> reference empties -> refuse, never
    # certify a (meaningless) idiolect against an empty reference.
    reference = [TextEntry(id="ref_leak", path="/corpus/ref/leak.txt", text=TARGET_TEXT)]
    with pytest.raises(CorpusLoadError):
        run_idiolect_detector(_target(), reference, n_values=(1, 2))
