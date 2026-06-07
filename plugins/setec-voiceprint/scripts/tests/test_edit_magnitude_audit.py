#!/usr/bin/env python3
"""Tests for edit_magnitude_audit.py + train_edit_magnitude.py (clean-room).

Pins the contract from `specs/13-editlens-edit-magnitude.md`:

  * the new `edit_magnitude` surface is registered in BOTH enums;
  * uncalibrated runs (no model) emit the score path with NO band;
  * the claim-license refuses an absolute "% AI" claim;
  * the envelope shape validates;
  * a stubbed model gives a deterministic score;
  * the trainer's smoke test runs on a tiny synthetic pair set with a
    stubbed model + stubbed target-proxy — no download, no fine-tune.

NO real model is loaded anywhere: every model-touching path is injected
with a stub. All tests are deterministic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_CALIB = _SCRIPTS / "calibration"
for _p in (str(_SCRIPTS), str(_CALIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import edit_magnitude_audit as ema  # type: ignore  # noqa: E402
import train_edit_magnitude as tem  # type: ignore  # noqa: E402
from claim_license import TASK_SURFACE_LABELS  # type: ignore  # noqa: E402
from output_schema import VALID_TASK_SURFACES  # type: ignore  # noqa: E402


# ============================================================
# Stubs — no real model is ever loaded.
# ============================================================


class StubModel:
    """Opaque calibrated-model stand-in. The audit only passes it through
    the injectable score_fn boundary, so it needs no real interface."""

    def __init__(self, path: str = "stub://model") -> None:
        self.path = path


def _const_score_fn(value: float):
    def score(model, text):
        return value
    return score


def _len_target_proxy(original: str, edited: str) -> float:
    """Deterministic stub proxy in [0, 1]: normalized length delta.

    Stands in for the BERTScore-family target without any model load."""
    o, e = len(original), len(edited)
    if max(o, e) == 0:
        return 0.0
    return abs(o - e) / max(o, e)


def _stub_train_model(*, base_model, examples, out_dir, epochs, seed):
    """Stub trainer: writes a fake checkpoint marker + returns metrics.
    No transformers, no torch, no fine-tune."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pytorch_model.bin.stub").write_text("stub", encoding="utf-8")
    # Trivial 'MSE' over the synthetic targets so the value is deterministic.
    mean = sum(ex["target"] for ex in examples) / len(examples)
    mse = sum((ex["target"] - mean) ** 2 for ex in examples) / len(examples)
    return {"mse": mse, "n_train": len(examples), "base_model": base_model}


_SAMPLE = "This is a sample document. " * 30  # ~150 words, over the floor


# ============================================================
# Named contract tests
# ============================================================


def test_surface_registered():
    assert ema.TASK_SURFACE == "edit_magnitude"
    assert ema.TASK_SURFACE in VALID_TASK_SURFACES
    assert ema.TASK_SURFACE in TASK_SURFACE_LABELS
    label = TASK_SURFACE_LABELS[ema.TASK_SURFACE].lower()
    assert "edit-magnitude" in label
    assert "% ai" in label  # explicitly NOT an absolute % AI


def test_uncalibrated_no_band():
    results = ema.audit(_SAMPLE)  # no model → uncalibrated
    assert results["calibrated"] is False
    assert results["band"] is None          # NO band when uncalibrated
    assert results["score"] is None         # nothing to score against
    assert results["corpus_provenance"] is None
    assert any("no_calibrated_model_supplied" in c for c in results["caveats"])
    # OOD caveat is load-bearing even uncalibrated.
    assert any("ood_collapse" in c for c in results["caveats"])


def test_claim_license_refuses_absolute_percent():
    results = ema.audit(_SAMPLE)
    lic = ema._claim_license(results)
    dn = lic.does_not_license.lower()
    assert "% ai" in dn
    assert "cross-corpus" in dn
    assert ("per-sentence" in dn) or ("localization" in dn)
    assert "ood" in dn or "out-of-distribution" in dn
    # And it licenses the same-corpus relative estimate.
    assert "calibrated on" in lic.licenses.lower()


def test_envelope_shape():
    results = ema.audit(_SAMPLE)
    env = ema.compose_envelope(target_path="x.txt", results=results)
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "edit_magnitude"
    assert env["tool"] == "edit_magnitude_audit"
    assert env["available"] is True
    assert env["claim_license"] is not None
    assert env["claim_license"]["task_surface"] == "edit_magnitude"
    assert "score" in env["results"]
    assert "band" in env["results"]
    # JSON-serializable end-to-end.
    json.dumps(env, default=str)


def test_stubbed_model_determinism():
    model = StubModel()
    fn = _const_score_fn(0.42)
    r1 = ema.audit(_SAMPLE, model=model, score_fn=fn)
    r2 = ema.audit(_SAMPLE, model=model, score_fn=fn)
    assert r1["score"] == r2["score"] == 0.42
    assert r1["calibrated"] is True
    assert r1 == r2  # fully deterministic results payload


def test_calibrated_emits_band_and_provenance():
    model = StubModel()
    fn = _const_score_fn(0.9)
    prov = {"corpus_name": "synthetic_pairs", "band_cutpoints": {"low": 0.2, "high": 0.6}}
    results = ema.audit(
        _SAMPLE, model=model, score_fn=fn,
        calibration_provenance=prov, band_low=0.2, band_high=0.6,
    )
    assert results["calibrated"] is True
    assert results["band"] == "high_edit_magnitude"  # 0.9 > 0.6
    assert results["corpus_provenance"]["corpus_name"] == "synthetic_pairs"


def test_calibrated_without_cutpoints_no_label():
    model = StubModel()
    results = ema.audit(_SAMPLE, model=model, score_fn=_const_score_fn(0.5))
    # Calibrated but no band cut-points recorded → score in-corpus, no label.
    assert results["calibrated"] is True
    assert results["band"] == "uncalibrated_band"


def test_no_real_model_loaded_uncalibrated_cli(tmp_path, capsys):
    f = tmp_path / "doc.txt"
    f.write_text(_SAMPLE, encoding="utf-8")
    rc = ema.main([str(f), "--json"])
    assert rc == 0
    env = json.loads(capsys.readouterr().out)
    assert env["task_surface"] == "edit_magnitude"
    assert env["results"]["calibrated"] is False
    assert env["results"]["band"] is None


def test_missing_model_path_errors(tmp_path):
    with pytest.raises(ema.EditMagnitudeModelError):
        ema.default_load_model(str(tmp_path / "nonexistent_model_dir"))


# ============================================================
# Trainer smoke test — tiny synthetic pairs, stubbed model + proxy.
# ============================================================


def test_train_smoke_synthetic_pairs(tmp_path):
    pairs = [
        {"id": "a", "original": "the cat sat on the mat", "edited": "the cat sat on the mat quietly"},
        {"id": "b", "original": "hello world", "edited": "hello brave new world entirely rewritten"},
        {"id": "c", "original": "same text here", "edited": "same text here"},
    ]
    out_dir = tmp_path / "model"
    prov = tem.train(
        pairs,
        out_dir=out_dir,
        base_model="roberta-large",
        base_model_license="MIT",
        corpus_name="synthetic_smoke",
        accept_noncommercial_free=True,
        band_cutpoints={"low": 0.1, "high": 0.5},
        target_proxy=_len_target_proxy,     # stub proxy — no model
        train_model=_stub_train_model,      # stub trainer — no fine-tune
    )
    # Provenance written + readable, with the clean-room + corpus fields.
    prov_file = out_dir / "provenance.json"
    assert prov_file.exists()
    on_disk = json.loads(prov_file.read_text(encoding="utf-8"))
    assert on_disk["corpus_name"] == "synthetic_smoke"
    assert on_disk["base_model"] == "roberta-large"
    assert on_disk["base_model_license"] == "MIT"
    assert on_disk["n_pairs"] == 3
    assert "mse" in on_disk["metrics"]
    assert on_disk["band_cutpoints"] == {"low": 0.1, "high": 0.5}
    assert on_disk["corpus_noncommercial_free_attested"] is True
    assert prov == on_disk


def test_train_refuses_without_nc_attestation(tmp_path):
    pairs = [{"id": "a", "original": "x y z", "edited": "x y z w"}]
    with pytest.raises(tem.TrainEditMagnitudeError):
        tem.train(
            pairs,
            out_dir=tmp_path / "m",
            accept_noncommercial_free=False,   # not attested → refuse
            target_proxy=_len_target_proxy,
            train_model=_stub_train_model,
        )


def test_train_smoke_is_deterministic(tmp_path):
    pairs = [
        {"id": "a", "original": "alpha beta", "edited": "alpha beta gamma delta"},
        {"id": "b", "original": "one two three", "edited": "one two three"},
    ]
    prov1 = tem.train(
        pairs, out_dir=tmp_path / "m1", accept_noncommercial_free=True,
        target_proxy=_len_target_proxy, train_model=_stub_train_model,
    )
    prov2 = tem.train(
        pairs, out_dir=tmp_path / "m2", accept_noncommercial_free=True,
        target_proxy=_len_target_proxy, train_model=_stub_train_model,
    )
    assert prov1["metrics"]["mse"] == prov2["metrics"]["mse"]


def test_build_examples_uses_edited_text():
    pairs = [{"id": "a", "original": "abc", "edited": "abcdef"}]
    examples = tem.build_examples(pairs, target_proxy=_len_target_proxy)
    assert examples[0]["text"] == "abcdef"          # regressor scores edited doc
    assert 0.0 <= examples[0]["target"] <= 1.0


def test_load_pairs_rejects_malformed(tmp_path):
    f = tmp_path / "bad.jsonl"
    f.write_text('{"original": "x"}\n', encoding="utf-8")  # missing 'edited'
    with pytest.raises(tem.TrainEditMagnitudeError):
        tem.load_pairs(f)


def test_default_train_model_is_unimplemented_scaffold(tmp_path):
    """The training loop is an intentional scaffold: ``default_train_model``
    must NEVER silently produce a model. It raises whether or not torch /
    transformers are installed (ImportError branch without them; explicit
    not-implemented raise with them), so the shipped CLI cannot fabricate a
    checkpoint until an operator wires the loop or injects ``train_model``."""
    with pytest.raises(tem.TrainEditMagnitudeError):
        tem.default_train_model(
            base_model="roberta-large",
            examples=[{"id": "a", "text": "x y z", "target": 0.1}],
            out_dir=tmp_path / "m",
            epochs=1,
            seed=0,
        )
