"""Tests for argmove_profile (ArgScope deterministic B3/B4 + AGD aggregator)."""
import sys
from pathlib import Path

import argmove_profile as amp  # noqa: E402
import concreteness  # noqa: E402


def test_self_test_passes():
    assert amp.run_self_test() == 0


def test_agd_markers_count_and_zero():
    m = amp.agd_markers("Although it rains we go. Therefore stay, because reasons. "
                        "Of course, as everyone knows, this is fine.")
    assert m["discounting_per_1k"] > 0       # although
    assert m["argument_marker_per_1k"] > 0   # therefore + because
    assert m["abusive_assuring_per_1k"] > 0  # of course / everyone knows
    clean = amp.agd_markers("The cat sat on the warm stone wall under a clear sky.")
    assert clean["abusive_assuring_per_1k"] == 0.0


def test_cliffs_delta_sign_and_bounds():
    # A strictly greater than B -> delta == 1.0; reversed -> -1.0; identical -> 0.0
    assert amp.cliffs_delta([5, 6, 7], [1, 2, 3]) == 1.0
    assert amp.cliffs_delta([1, 2, 3], [5, 6, 7]) == -1.0
    assert amp.cliffs_delta([1, 2, 3], [1, 2, 3]) == 0.0


def test_vector_contract_keys_present():
    vec = amp.argmove_vector("This clearly works, but it might be somewhat wrong. Studies show "
                             "the implementation indicates progress, although most agree.")
    for k in ("stance.hedge", "stance.booster", "agency.nominalization_per_1k",
              "agd.discounting_per_1k", "agd.abusive_assuring_per_1k"):
        assert k in vec


def test_vector_omits_optional_concreteness_when_data_is_absent(tmp_path, monkeypatch):
    """P2-4: absence is driven through the loader's default-path seam, so
    this FAILS only on a real regression — not on a machine where the user
    has legitimately fetched the publisher's file."""
    concreteness._load_concreteness_dict.cache_clear()
    monkeypatch.setattr(
        concreteness, "_DEFAULT_DATA_PATH", tmp_path / "absent" / "brysbaert.csv",
    )
    vec = amp.argmove_vector("This clearly works, but it might be somewhat wrong.")
    assert "abstraction.mean_concreteness" not in vec
    concreteness._load_concreteness_dict.cache_clear()


# P2-8: the "concrete words rank above abstract words" behavior the train
# deleted, restored against a test-owned CSV of INVENTED words and ratings
# (spec §4: no upstream rating row is copied into tests). This is the
# ordering property B3 actually claims; without it `mean_concreteness`
# could return a constant and every other test would still pass.
_INVENTED_CSV = """word,is_bigram,conc_mean,conc_sd,unknown_count,total_raters,percent_known,subtlex_freq
plimber,0,4.80,0.30,0,30,1.000000,2400
gralnet,0,4.60,1.10,0,28,1.000000,250
korvane,0,4.40,0.95,0,28,1.000000,310
drennock,0,4.20,0.90,0,28,1.000000,120
morvane,0,4.05,0.85,0,28,1.000000,140
themblish,0,1.55,1.05,2,30,0.933333,420
vurnish,0,1.70,1.25,0,29,1.000000,180
sprellik,0,1.90,1.15,0,29,1.000000,160
quilth,0,2.05,1.20,0,29,1.000000,150
brastine,0,2.20,1.30,0,29,1.000000,130
"""


def test_concreteness_orders_concrete_above_abstract(tmp_path):
    data = tmp_path / "invented_concreteness.csv"
    data.write_text(_INVENTED_CSV, encoding="utf-8")
    concreteness._load_concreteness_dict.cache_clear()
    try:
        concrete = amp.mean_concreteness(
            "plimber gralnet korvane drennock morvane", data_path=data,
        )
        abstract = amp.mean_concreteness(
            "themblish vurnish sprellik quilth brastine", data_path=data,
        )
        assert concrete is not None and abstract is not None
        assert concrete > abstract
    finally:
        concreteness._load_concreteness_dict.cache_clear()


def test_mean_concreteness_is_none_without_data(tmp_path):
    missing = tmp_path / "absent" / "brysbaert.csv"
    concreteness._load_concreteness_dict.cache_clear()
    try:
        assert amp.mean_concreteness("plimber gralnet", data_path=missing) is None
    finally:
        concreteness._load_concreteness_dict.cache_clear()


def test_self_test_passes_with_an_installed_dataset(tmp_path, monkeypatch):
    """Fold-in (c): `run_self_test` used to assert concrete > abstract
    against whatever concreteness data the user had installed, so a real
    dataset that lacked the probe words (or rated them unexpectedly) made
    the self-check report FAIL for something that is not a regression. It
    now asserts against its own invented probe table through the data_path
    seam; an installed dataset is reported, never asserted on. This drives
    that branch with a deliberately hostile 'installed' file."""
    # A hostile-but-legal "user dataset": it CONTAINS the words the old
    # self-check probed with, and rates them the other way round. The old
    # code asserted concrete > abstract on exactly these values and
    # reported FAIL; nothing about that is a regression in this module.
    inverted = {
        "table": 1.10, "chair": 1.20, "stone": 1.30, "house": 1.40, "dog": 1.50,
        "freedom": 4.90, "justice": 4.80, "essence": 4.70,
        "concept": 4.60, "theory": 4.50,
    }
    installed = tmp_path / "brysbaert_concreteness.csv"
    installed.write_text(
        "word,conc_mean\n"
        + "".join(f"{w},{v:.2f}\n" for w, v in inverted.items())
        + "".join(f"filler{i},3.00\n" for i in range(concreteness.MIN_USABLE_ROWS)),
        encoding="utf-8",
    )
    concreteness._load_concreteness_dict.cache_clear()
    monkeypatch.setattr(concreteness, "_DEFAULT_DATA_PATH", installed)
    try:
        assert concreteness.is_available() is True
        assert amp.run_self_test() == 0
    finally:
        concreteness._load_concreteness_dict.cache_clear()
