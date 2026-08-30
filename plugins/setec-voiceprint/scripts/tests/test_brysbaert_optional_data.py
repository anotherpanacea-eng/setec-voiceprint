"""Missing Brysbaert data degrades only the dependent AIC-8 paths."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import aesthetic_authority_audit as aaa
import argmove_profile
import image_conjunction
import prestige_metaphor
import variance_audit


REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_RELATIVE_PATH = Path("plugins/setec-voiceprint/data/brysbaert_concreteness.csv")


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


def test_composite_vector_preserves_unrelated_signals_without_data():
    vector = argmove_profile.argmove_vector("Clearly, this may work because evidence matters.")
    assert "stance.hedge" in vector
    assert "agency.nominalization_per_1k" in vector
    assert "agd.discounting_per_1k" in vector
    assert "abstraction.mean_concreteness" not in vector


def test_direct_detectors_report_explicit_unavailability_without_parsing():
    image = image_conjunction.image_conjunction_density("text", nlp=None)
    prestige = prestige_metaphor.prestige_metaphor_density("text", nlp=None)
    aesthetic = aaa.aesthetic_authority_audit("text", nlp=None)
    for result in (image, prestige, aesthetic):
        assert result["available"] is False
        assert result["reason"] == "data_not_installed"
    assert "value" not in image and "conjunctions" not in image
    assert "value" not in prestige and "conjunctions" not in prestige
    assert "compound" not in aesthetic


def test_unavailable_compound_clis_honor_out_file(tmp_path, capsys):
    source = tmp_path / "source.txt"
    source.write_text("A short public test sentence.\n", encoding="utf-8")

    for main, filename, result_path in (
        (image_conjunction.main, "image.json", None),
        (prestige_metaphor.main, "prestige.json", "results"),
        (aaa.main, "aesthetic.json", "results"),
    ):
        destination = tmp_path / filename
        assert main([str(source), "--out", str(destination)]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Optional Brysbaert concreteness data" in captured.err
        assert "plugins/setec-voiceprint/scripts/fetch_brysbaert.py" in captured.err
        payload = json.loads(destination.read_text(encoding="utf-8"))
        result = payload if result_path is None else payload[result_path]
        assert result["available"] is False
        assert result["reason"] == "data_not_installed"


def test_variance_aic8_marks_only_aic8_unavailable():
    result = variance_audit.audit_text("This is a valid test sentence. " * 20, do_aic8=True)
    diagnostics = result["aic_8_9"]["diagnostics"]
    assert diagnostics["aic8_available"] is False
    assert diagnostics["aic8_unavailable_reason"] == "data_not_installed"
    assert "image_conjunction_density" not in result["aic_8_9"]
    assert "prestige_metaphor_density" not in result["aic_8_9"]


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
