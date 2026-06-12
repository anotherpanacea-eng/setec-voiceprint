"""Tests for argmove_profile (ArgScope deterministic B3/B4 + AGD aggregator)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argmove_profile as amp  # noqa: E402


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


def test_concreteness_orders_concrete_above_abstract():
    assert (amp.mean_concreteness("table chair stone house dog") or 0) \
        > (amp.mean_concreteness("freedom justice essence concept theory") or 0)
