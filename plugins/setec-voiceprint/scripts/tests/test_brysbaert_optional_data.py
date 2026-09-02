"""Missing Brysbaert data degrades only the dependent AIC-8 paths."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import aesthetic_authority_audit as aaa
import argmove_profile
import concreteness
import image_conjunction
import output_schema
import prestige_metaphor
import setec_run
import variance_audit


REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_RELATIVE_PATH = Path("plugins/setec-voiceprint/data/brysbaert_concreteness.csv")


@pytest.fixture
def no_local_data(tmp_path, monkeypatch):
    """Drive absence through the loader's default-path seam.

    These tests used to hard-assume the repo checkout had no CSV, so they
    FAILED (not skipped) for any user who had legitimately fetched the
    publisher's file — the reviewer's P2-4 repro. Overriding
    ``concreteness._DEFAULT_DATA_PATH`` (as tests/test_concreteness.py
    already does) makes absence a property of the test, not of the machine.
    """
    concreteness._load_concreteness_dict.cache_clear()
    monkeypatch.setattr(
        concreteness, "_DEFAULT_DATA_PATH", tmp_path / "absent" / "brysbaert.csv",
    )
    yield
    concreteness._load_concreteness_dict.cache_clear()


def test_production_csv_is_absent_ignored_and_excluded_from_source_archives():
    assert subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", str(DATA_RELATIVE_PATH)],
        cwd=REPO_ROOT,
        check=False,
    ).returncode == 0
    assert subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(DATA_RELATIVE_PATH)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    ).returncode != 0
    assert subprocess.run(
        ["git", "archive", "--format=tar", "HEAD", str(DATA_RELATIVE_PATH)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
    ).returncode != 0


def test_synthetic_local_csv_reenables_image_detector_path(tmp_path, monkeypatch):
    data = tmp_path / "concreteness.csv"
    data.write_text(
        "word,conc_mean\nmachinery,4.5\ngrieF,1.5\n", encoding="utf-8",
    )
    monkeypatch.setattr(
        image_conjunction.embeddings, "model_identifier", lambda: "synthetic-test",
    )
    result = image_conjunction.image_conjunction_density(
        "", nlp=None, concreteness_path=data,
    )
    assert "available" not in result
    assert result["value"] == 0.0
    monkeypatch.setattr(
        image_conjunction.embeddings, "cosine_similarity", lambda *_: 0.1,
    )
    pair = image_conjunction.evaluate_pair(
        "machinery", "grief", "prep_of", t1=2.0, concreteness_path=data,
    )
    assert pair is not None
    assert pair["concreteness_gap"] == 3.0


def test_composite_vector_preserves_unrelated_signals_without_data(no_local_data):
    vector = argmove_profile.argmove_vector("Clearly, this may work because evidence matters.")
    assert "stance.hedge" in vector
    assert "agency.nominalization_per_1k" in vector
    assert "agd.discounting_per_1k" in vector
    assert "abstraction.mean_concreteness" not in vector


def test_direct_detectors_report_explicit_unavailability_without_parsing(no_local_data):
    image = image_conjunction.image_conjunction_density("text", nlp=None)
    prestige = prestige_metaphor.prestige_metaphor_density("text", nlp=None)
    aesthetic = aaa.aesthetic_authority_audit("text", nlp=None)
    for result in (image, prestige, aesthetic):
        assert result["available"] is False
        assert result["reason"] == "data_not_installed"
    assert "value" not in image and "conjunctions" not in image
    assert "value" not in prestige and "conjunctions" not in prestige
    assert "compound" not in aesthetic


def test_unavailable_compound_clis_honor_out_file(tmp_path, capsys, no_local_data):
    source = tmp_path / "source.txt"
    source.write_text("A short public test sentence.\n", encoding="utf-8")

    for main, filename in (
        (image_conjunction.main, "image.json"),
        (prestige_metaphor.main, "prestige.json"),
        (aaa.main, "aesthetic.json"),
    ):
        destination = tmp_path / filename
        assert main([str(source), "--out", str(destination)]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Optional Brysbaert concreteness data" in captured.err
        assert "plugins/setec-voiceprint/scripts/fetch_brysbaert.py" in captured.err
        payload = json.loads(destination.read_text(encoding="utf-8"))
        assert payload["available"] is False


# ---- P1-3: the envelope surfaces say "unavailable" at the TOP level ----
#
# Reviewer repro: with the data absent, prestige_metaphor and
# aesthetic_authority_audit emitted top-level `available: true`,
# `warnings: []`, a full claim_license, and an ad-hoc nested
# `results.available: false` — the one place a consumer branching on the
# R3 contract never looks. prestige additionally reported `target.words`
# 0 for a 90-word document while aesthetic reported 90.

_NINETY_WORDS = "word " * 90


@pytest.mark.parametrize("main_fn", [prestige_metaphor.main, aaa.main])
def test_absent_data_is_a_top_level_r3_refusal(
    tmp_path, capsys, no_local_data, main_fn,
):
    source = tmp_path / "source.txt"
    source.write_text(_NINETY_WORDS.strip() + "\n", encoding="utf-8")
    destination = tmp_path / "out.json"
    assert main_fn([str(source), "--out", str(destination)]) == 0
    capsys.readouterr()
    payload = json.loads(destination.read_text(encoding="utf-8"))

    assert payload["available"] is False
    assert payload["warnings"], "available:false MUST be explained by warnings"
    assert "data_not_installed" in payload["warnings"][0]
    assert payload["reason_category"] == "missing_dependency"
    assert payload["claim_license"] is None
    # target.words is the document's REAL count, not 0.
    assert payload["target"]["words"] == 90
    # No nested contradiction: `results` no longer carries its own
    # `available` flag alongside a top-level one.
    assert "available" not in payload["results"]


@pytest.mark.parametrize("main_fn", [prestige_metaphor.main, aaa.main])
def test_dispatcher_branches_on_the_unavailable_envelope(
    tmp_path, capsys, no_local_data, main_fn,
):
    """setec_run._emit_surface_envelope must recognize the refusal and map
    it to the contract exit code — not synthesize an `internal_error`
    because `reason_category` was missing (which is what an
    `available: true` envelope with a nested flag would have produced)."""
    source = tmp_path / "source.txt"
    source.write_text(_NINETY_WORDS.strip() + "\n", encoding="utf-8")
    destination = tmp_path / "out.json"
    assert main_fn([str(source), "--out", str(destination)]) == 0
    capsys.readouterr()
    envelope = json.loads(destination.read_text(encoding="utf-8"))

    code = setec_run._emit_surface_envelope("prestige_metaphor", envelope)
    emitted = json.loads(capsys.readouterr().out)
    assert code == setec_run.EXIT_CONTRACT
    assert emitted == envelope  # re-emitted verbatim, not replaced


def test_variance_aic8_marks_only_aic8_unavailable(no_local_data):
    result = variance_audit.audit_text("This is a valid test sentence. " * 20, do_aic8=True)
    diagnostics = result["aic_8_9"]["diagnostics"]
    assert diagnostics["aic8_available"] is False
    assert diagnostics["aic8_unavailable_reason"] == "data_not_installed"
    assert "image_conjunction_density" not in result["aic_8_9"]
    assert "prestige_metaphor_density" not in result["aic_8_9"]


@pytest.mark.skipif(
    concreteness.is_available(),
    reason=(
        "a locally acquired Brysbaert CSV is installed; the subprocess CLI "
        "cannot be reached by a path override"
    ),
)
def test_variance_human_cli_names_optional_data_and_fetch_command(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("This is a valid synthetic test sentence. " * 60, encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "plugins/setec-voiceprint/scripts/variance_audit.py"),
            str(source),
            "--aic8",
            "--no-tier2",
            "--no-tier3",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "Optional Brysbaert concreteness data" in combined
    assert "plugins/setec-voiceprint/scripts/fetch_brysbaert.py" in combined


def test_variance_summary_names_every_unavailable_aic8_reason(no_local_data):
    """P3-12 repro: the human summary line was gated on
    `aic8_unavailable_reason == "data_not_installed"`, so a malformed or
    unreadable local CSV, an unimportable AIC-8 module, or a missing spaCy
    model left the operator with a silent, AIC-8-less report."""
    audit = variance_audit.audit_text(
        "This is a valid synthetic test sentence. " * 30, do_aic8=True,
    )
    diagnostics = audit["aic_8_9"]["diagnostics"]
    assert diagnostics["aic8_available"] is False
    compression = variance_audit.classify_compression(audit)

    for reason, expected in (
        (concreteness.DATA_NOT_INSTALLED, "is not installed"),
        (concreteness.DATA_MALFORMED, "present but unusable"),
        (concreteness.DATA_UNREADABLE, "could not be read"),
        ("AIC-8 modules unimportable: no module named 'embeddings'",
         "AIC-8 modules unimportable"),
    ):
        diagnostics["aic8_unavailable_reason"] = reason
        summary = variance_audit.format_summary(audit, compression)
        assert "AIC-8 unavailable" in summary, reason
        assert expected in summary, reason


# ---- re-review P1: bad VALUES must not reach a detector -------------
#
# Repro: a table whose ratings were 1000000 / -1000000 passed the
# structural validator and produced a fully valid `available: true`,
# `status: provisional` AIC-8 envelope at ~125 conjunctions per 1000
# tokens against a 5-7/1000 register baseline — a fabricated damning
# result. "nan" parsed as a float and escaped as an unhandled
# OutputValidityError traceback out of these same entry points. The table
# is full-length so the row-count floor passes and the VALUE check is what
# is under test.


@pytest.fixture
def poisoned_local_data(request, tmp_path, monkeypatch):
    rows = "\n".join(
        f"gralnet{i},4.60" for i in range(concreteness.MIN_USABLE_ROWS)
    )
    path = tmp_path / "brysbaert_concreteness.csv"
    path.write_text(
        f"word,conc_mean\n{rows}\nvurnish,{request.param}\n", encoding="utf-8",
    )
    concreteness._load_concreteness_dict.cache_clear()
    monkeypatch.setattr(concreteness, "_DEFAULT_DATA_PATH", path)
    yield path
    concreteness._load_concreteness_dict.cache_clear()


@pytest.mark.parametrize(
    "poisoned_local_data", ["1000000", "-1000000", "nan", "inf"], indirect=True,
)
def test_out_of_scale_data_yields_no_detector_value(
    tmp_path, capsys, poisoned_local_data,
):
    assert concreteness.availability_reason() == concreteness.DATA_MALFORMED

    source = tmp_path / "source.txt"
    source.write_text(_NINETY_WORDS.strip() + "\n", encoding="utf-8")
    for main_fn, name in (
        (prestige_metaphor.main, "prestige.json"),
        (aaa.main, "aesthetic.json"),
    ):
        destination = tmp_path / name
        assert main_fn([str(source), "--out", str(destination)]) == 0
        capsys.readouterr()
        payload = json.loads(destination.read_text(encoding="utf-8"))
        assert payload["available"] is False
        assert payload["reason_category"] == "missing_dependency"
        assert concreteness.DATA_MALFORMED in payload["warnings"][0]
        assert "value" not in payload["results"]
        assert "conjunctions" not in payload["results"]

    audit = variance_audit.audit_text(
        "This is a valid synthetic test sentence. " * 30, do_aic8=True,
    )
    diagnostics = audit["aic_8_9"]["diagnostics"]
    assert diagnostics["aic8_available"] is False
    assert diagnostics["aic8_unavailable_reason"] == concreteness.DATA_MALFORMED
    assert "image_conjunction_density" not in audit["aic_8_9"]
    assert "prestige_metaphor_density" not in audit["aic_8_9"]


def test_aic9_diagnostics_are_symmetric_with_aic8(monkeypatch):
    """Nit: the AIC-8 branch wrote `aic8_available: False` alongside its
    reason while AIC-9 wrote only `aic9_unavailable_reason`, so a consumer
    had to branch on the presence of a string for one and on a boolean for
    the other."""
    monkeypatch.setattr(
        variance_audit, "_aic9_kicker_block",
        lambda text: {"available": False, "reason": "kicker_density unimportable"},
    )
    audit = variance_audit.audit_text(
        "This is a valid synthetic test sentence. " * 30, do_aic9=True,
    )
    diagnostics = audit["aic_8_9"]["diagnostics"]
    assert diagnostics["aic9_available"] is False
    assert diagnostics["aic9_unavailable_reason"] == "kicker_density unimportable"


def test_unavailable_envelope_warning_carries_the_specific_finding(
    tmp_path, capsys, monkeypatch,
):
    """Fold-in (a) at the envelope boundary: the warnings entry must say
    WHICH content made the file unusable, not only that it is."""
    rows = "\n".join(
        f"gralnet{i},4.60" for i in range(concreteness.MIN_USABLE_ROWS)
    )
    path = tmp_path / "brysbaert_concreteness.csv"
    path.write_text(f"word,conc_mean\n{rows}\nvurnish,1000000\n", encoding="utf-8")
    concreteness._load_concreteness_dict.cache_clear()
    monkeypatch.setattr(concreteness, "_DEFAULT_DATA_PATH", path)
    try:
        source = tmp_path / "source.txt"
        source.write_text(_NINETY_WORDS.strip() + "\n", encoding="utf-8")
        destination = tmp_path / "out.json"
        assert prestige_metaphor.main([str(source), "--out", str(destination)]) == 0
        captured = capsys.readouterr()
        payload = json.loads(destination.read_text(encoding="utf-8"))
        assert "1000000" in payload["warnings"][0]
        assert "vurnish" in payload["warnings"][0]
        assert "1000000" in captured.err

        audit = variance_audit.audit_text(
            "This is a valid synthetic test sentence. " * 30, do_aic8=True,
        )
        diagnostics = audit["aic_8_9"]["diagnostics"]
        assert "1000000" in diagnostics["aic8_unavailable_detail"]
        summary = variance_audit.format_summary(
            audit, variance_audit.classify_compression(audit),
        )
        assert "1000000" in summary
    finally:
        concreteness._load_concreteness_dict.cache_clear()


@pytest.mark.parametrize("builder", [
    prestige_metaphor.build_unavailable_payload,
    aaa.build_unavailable_payload,
])
def test_unavailable_payload_validates_its_reason_category(tmp_path, builder):
    """Fold-in (b): the R3 category was a hardcoded literal, so a typo would
    have shipped an envelope the dispatcher rewrites into an internal_error
    instead of the refusal the script meant. It is now checked against
    output_schema.REASON_CATEGORIES at the point of construction."""
    kwargs = dict(
        reason=concreteness.DATA_NOT_INSTALLED,
        guidance="guidance",
        target_path=tmp_path / "x.txt",
        text="one two three",
    )
    good = builder(**kwargs)
    assert good["reason_category"] in output_schema.REASON_CATEGORIES
    assert good["target"]["words"] == 3
    with pytest.raises(ValueError, match="Unknown reason_category"):
        builder(**kwargs, reason_category="missing_dependancy")
