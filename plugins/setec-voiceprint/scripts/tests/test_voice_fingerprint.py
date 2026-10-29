#!/usr/bin/env python3
"""Tests for voice_fingerprint.py (spec 02 — voice_fingerprint).

The eight tests are the spec's test contract. They run against a
DETERMINISTIC STUB encoder (monkeypatched onto ``_load_encoder``) —
no real model weights are downloaded or loaded, no GPU, no network.
The stub returns unit-normalized vectors derived from the input text
so identical text yields cosine ~= 1 and structurally different text
yields a stable, lower cosine.

Spec test contract:
  * test_envelope_shape
  * test_claim_license_present_and_refuses_verdict
  * test_cosine_distribution_keys
  * test_identical_text_high_similarity
  * test_two_corpus_mode_requires_baseline
  * test_missing_transformers_graceful
  * test_window_strategy_parity
  * test_capabilities_entry_present
"""

from __future__ import annotations

import builtins
import hashlib
import json
from pathlib import Path

import pytest

from conftest import write_utf8_text as _write

import voice_fingerprint as vf  # type: ignore

# Capture the genuine loader BEFORE any fixture patches it, so the
# missing-transformers test can exercise the real dependency-gate path.
_REAL_LOAD_ENCODER = vf._load_encoder


# --------------- Deterministic stub encoder ---------------------


class _StubEncoder:
    """Deterministic stand-in for a real style encoder.

    Each passage maps to a fixed-dimension unit vector seeded by a
    hash of its normalized token content. Identical text → identical
    vector → cosine 1.0. The mapping is content-based (not positional)
    so the same window produces the same vector regardless of where it
    appears — which is what makes ``test_identical_text_high_
    similarity`` meaningful.
    """

    DIM = 16

    def __init__(self, model_id: str = "stub-style-encoder",
                 device=None) -> None:
        self.model_id = model_id
        self.device = device

    def _vec(self, text: str):
        import numpy as np

        # Content-based seed: normalized whitespace-token signature.
        sig = " ".join(text.split()).lower().encode("utf-8")
        digest = hashlib.sha256(sig).digest()
        # Build a DIM-length float vector from the digest bytes.
        raw = np.frombuffer(
            (digest * ((self.DIM // len(digest)) + 1))[: self.DIM * 4],
            dtype=np.uint8,
        )[: self.DIM].astype("float32")
        # Center so vectors aren't all in the positive orthant (which
        # would make every pair of distinct texts highly similar).
        raw = raw - 127.5
        norm = float(np.linalg.norm(raw))
        if norm == 0.0:
            raw[0] = 1.0
            norm = 1.0
        return raw / norm

    def encode(self, texts):
        import numpy as np

        if not texts:
            return np.empty((0, 0), dtype="float32")
        return np.asarray([self._vec(t) for t in texts], dtype="float32")


@pytest.fixture(autouse=True)
def _stub_encoder(monkeypatch: pytest.MonkeyPatch):
    """Replace the real encoder loader with the stub for every test.
    Guarantees no real weights are touched."""
    monkeypatch.setattr(
        vf, "_load_encoder",
        lambda model, device=None: _StubEncoder(model_id=model),
    )


# --------------- Helpers ----------------------------------------


def _multi_para_text(n_paras: int = 5, words_per: int = 60) -> str:
    """Produce text with distinct, paragraph-sized windows so the
    shared paragraph windowing yields >= n_paras windows (each above
    MIN_PARA_TOKENS = 25)."""
    paras = []
    for i in range(n_paras):
        # Distinct content per paragraph so windows differ.
        body = " ".join([f"word{i}_{j}" for j in range(words_per)])
        paras.append(f"Paragraph number {i}. {body}.")
    return "\n\n".join(paras)


def _write_dir(tmp_path: Path, dirname: str, files: dict[str, str]) -> Path:
    d = tmp_path / dirname
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


# --------------- 1. Envelope shape ------------------------------


def test_envelope_shape(tmp_path: Path):
    """Output validates against output_schema; task_surface is
    authorship_embedding; required results keys present."""
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    out_path = tmp_path / "fp.json"
    rc = vf.main([str(src), "--json", "--out", str(out_path)])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "1.0"
    assert payload["task_surface"] == "authorship_embedding"
    assert payload["tool"] == "voice_fingerprint"
    assert payload["available"] is True
    results = payload["results"]
    assert results["mode"] == "single"
    assert results["model_id"] == "rrivera1849/LUAR-MUD"  # luar alias resolved
    assert "n_windows" in results
    assert "cosine_distribution" in results
    # task_surface must be registered in output_schema.
    from output_schema import VALID_TASK_SURFACES  # type: ignore
    assert "authorship_embedding" in VALID_TASK_SURFACES


# --------------- 2. Claim license present + refuses verdict -----


def test_claim_license_present_and_refuses_verdict(tmp_path: Path):
    """Claim-license block exists; rendered text contains no
    'same person' / 'is AI' assertion as a licensed claim, and
    carries the content-control caveat."""
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    out_path = tmp_path / "fp.json"
    vf.main([str(src), "--json", "--out", str(out_path)])
    payload = json.loads(out_path.read_text(encoding="utf-8"))

    cl = payload["claim_license"]
    assert cl is not None
    assert cl["task_surface"] == "authorship_embedding"

    rendered = payload["claim_license_rendered"]
    assert rendered

    does_not = cl["does_not_license"].lower()
    # The refusals must be explicit in does_not_license.
    assert "same person" in does_not
    assert "different author" in does_not
    assert "ai" in does_not  # refuses AI/human

    # No binary verdict leaked into what the result LICENSES.
    licenses = cl["licenses"].lower()
    assert "same person" not in licenses
    assert "is ai" not in licenses

    # Content-control caveat must be present (LUAR register skew /
    # Wegmann punctuation-casing / short-text fragility).
    caveat_blob = " ".join(cl["additional_caveats"]).lower()
    assert "register skew" in caveat_blob or "content control" in caveat_blob
    assert "luar" in caveat_blob
    assert "short" in caveat_blob  # short-text fragility


# --------------- 3. Cosine distribution keys --------------------


def test_cosine_distribution_keys(tmp_path: Path):
    """cosine_distribution carries mean/sd/min/p10/p50/p90."""
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    out_path = tmp_path / "fp.json"
    vf.main([str(src), "--json", "--out", str(out_path)])
    payload = json.loads(out_path.read_text(encoding="utf-8"))

    dist = payload["results"]["cosine_distribution"]
    for key in ("mean", "sd", "min", "p10", "p50", "p90"):
        assert key in dist, f"missing distribution key {key!r}"


# --------------- 4. Identical text → high similarity ------------


def test_identical_text_high_similarity(tmp_path: Path):
    """A document whose windows are identical yields cosine ~= 1
    (the stub returns content-deterministic vectors)."""
    para = "Identical paragraph content. " + " ".join(
        [f"token{j}" for j in range(60)]
    )
    text = "\n\n".join([para, para, para, para])
    src = _write(tmp_path, "same.txt", text)
    out_path = tmp_path / "fp.json"
    rc = vf.main([str(src), "--json", "--out", str(out_path)])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))

    dist = payload["results"]["cosine_distribution"]
    assert dist["mean"] == pytest.approx(1.0, abs=1e-6)
    assert dist["min"] == pytest.approx(1.0, abs=1e-6)


# --------------- 5. Two-corpus requires baseline ----------------


def test_two_corpus_mode_requires_baseline(tmp_path: Path):
    """Two-corpus / n-way framing: --impostor-dir without
    --baseline-dir errors clearly (n-way needs a candidate). And
    supplying --baseline-dir routes into two_corpus mode cleanly."""
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    impostors = _write_dir(
        tmp_path, "impostors",
        {"i1.txt": _multi_para_text(n_paras=4)},
    )

    # --impostor-dir alone (no --baseline-dir) must error, not crash.
    rc = vf.main([
        str(src), "--impostor-dir", str(impostors), "--json",
        "--out", str(tmp_path / "x.json"),
    ])
    assert rc == 3  # clean VoiceFingerprintError exit, not a traceback

    # With a baseline dir, two_corpus mode runs and emits the
    # two_corpus envelope.
    baseline = _write_dir(
        tmp_path, "baseline",
        {"b1.txt": _multi_para_text(n_paras=4),
         "b2.txt": _multi_para_text(n_paras=3)},
    )
    out_path = tmp_path / "two.json"
    rc2 = vf.main([
        str(src), "--baseline-dir", str(baseline), "--json",
        "--out", str(out_path),
    ])
    assert rc2 == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["results"]["mode"] == "two_corpus"
    assert payload["baseline"] is not None
    assert payload["baseline"]["n_files"] == 2


# --------------- 5b. N-way mode end-to-end (regression) ---------


def test_n_way_mode_emits_envelope(tmp_path: Path):
    """N-way mode (--baseline-dir + --impostor-dir) runs end-to-end and
    emits a valid envelope.

    Regression for the n-way paths that double-wrapped each impostor's
    text list (the contract is ``dict[str, list[str]]``): the metadata
    path fed the list object to ``_approx_token_count`` (``.split`` on a
    list) and the scoring path fed it to ``_window_corpus`` ->
    ``split_windows`` (``re.split`` on a list), so any real
    ``--impostor-dir`` run raised AttributeError/TypeError before
    emitting its envelope.
    """
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    baseline = _write_dir(
        tmp_path, "baseline",
        {"b1.txt": _multi_para_text(n_paras=4)},
    )
    impostors = _write_dir(
        tmp_path, "impostors",
        {"i1.txt": _multi_para_text(n_paras=4),
         "i2.txt": _multi_para_text(n_paras=3)},
    )
    out_path = tmp_path / "nway.json"
    rc = vf.main([
        str(src),
        "--baseline-dir", str(baseline),
        "--impostor-dir", str(impostors),
        "--json", "--out", str(out_path),
    ])
    assert rc == 0  # an uncaught AttributeError/TypeError would fail here
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["results"]["mode"] == "n_way"
    # Both impostor files were scored (per-impostor centroids built).
    assert payload["results"]["n_impostors"] == 2
    # The metadata path counted impostor words without crashing.
    assert payload["baseline"] is not None
    assert payload["baseline"]["n_impostor_files"] == 2
    assert payload["baseline"]["impostor_words"] > 0


# --------------- 6. Missing transformers graceful ---------------


def test_missing_transformers_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """With transformers absent, the REAL _load_encoder path exits
    with the dependency_check-style install hint, NOT a traceback."""
    src = _write(tmp_path, "draft.txt", _multi_para_text())

    # Restore the GENUINE loader (the autouse fixture replaced it with
    # the stub). We want the real dependency-gate code path here.
    monkeypatch.setattr(vf, "_load_encoder", _REAL_LOAD_ENCODER)

    # Force `import transformers` to fail inside the real loader,
    # simulating an environment without the style-embedding tier.
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "transformers" or name.startswith("transformers."):
            raise ImportError("No module named 'transformers'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    # Directly calling the real loader raises the typed error with the
    # dependency_check-style install hint.
    with pytest.raises(vf.VoiceFingerprintError) as excinfo:
        vf._load_encoder("luar")
    msg = str(excinfo.value)
    assert "transformers" in msg
    assert "pip install" in msg
    assert "Traceback" not in msg

    # And the CLI exits 3 (clean), not a traceback.
    rc = vf.main([str(src), "--json"])
    assert rc == 3


# --------------- 7. Window-strategy parity ----------------------


def test_window_strategy_parity(tmp_path: Path):
    """paragraph vs fixed-token produce the same envelope SHAPE."""
    src = _write(tmp_path, "draft.txt", _multi_para_text(n_paras=6))

    para_out = tmp_path / "para.json"
    ft_out = tmp_path / "ft.json"
    rc1 = vf.main([
        str(src), "--window-strategy", "paragraph", "--json",
        "--out", str(para_out),
    ])
    rc2 = vf.main([
        str(src), "--window-strategy", "fixed-token", "--window-size", "40",
        "--json", "--out", str(ft_out),
    ])
    assert rc1 == 0 and rc2 == 0

    p = json.loads(para_out.read_text(encoding="utf-8"))
    f = json.loads(ft_out.read_text(encoding="utf-8"))

    # Same top-level envelope keys.
    assert set(p.keys()) == set(f.keys())
    # Same results keys (shape parity).
    assert set(p["results"].keys()) == set(f["results"].keys())
    assert p["results"]["mode"] == f["results"]["mode"] == "single"
    assert set(p["results"]["cosine_distribution"].keys()) == set(
        f["results"]["cosine_distribution"].keys()
    )
    # windowing block records the strategy actually used.
    assert p["results"]["windowing"]["strategy"] == "paragraph"
    assert f["results"]["windowing"]["strategy"] == "fixed-token"


# --------------- 8. Capabilities entry present ------------------




# --------------- STAR encoder (VP-RS3) ---------------------------


def _fake_transformers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `import transformers` succeed without the real package, so the
    real loader reaches its license gate. No weights load: encoders are lazy."""
    import sys
    import types

    monkeypatch.setitem(sys.modules, "transformers", types.ModuleType("transformers"))


def test_star_alias_refused_without_license_acknowledgement(monkeypatch):
    monkeypatch.setattr(vf, "_load_encoder", _REAL_LOAD_ENCODER)
    _fake_transformers(monkeypatch)
    with pytest.raises(vf.VoiceFingerprintError, match="declares no license"):
        vf._load_encoder("star")
    with pytest.raises(vf.VoiceFingerprintError, match="declares no license"):
        vf._load_encoder("AIDA-UPM/star")


def test_star_loads_lazily_once_acknowledged(monkeypatch):
    monkeypatch.setattr(vf, "_load_encoder", _REAL_LOAD_ENCODER)
    _fake_transformers(monkeypatch)
    enc = vf._load_encoder("star", allow_undeclared_license=True)
    assert enc.model_id == "AIDA-UPM/star"
    assert type(enc).__name__ == "_STAREncoder"


def test_star_cli_exits_cleanly_without_acknowledgement(tmp_path, monkeypatch):
    monkeypatch.setattr(vf, "_load_encoder", _REAL_LOAD_ENCODER)
    _fake_transformers(monkeypatch)
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    assert vf.main([str(src), "--model", "star", "--json"]) == 3


def test_star_run_records_its_model_and_caveat(tmp_path, monkeypatch, capsys):
    seen = {}

    def _loader(model, device=None, **kwargs):
        seen.update(kwargs)
        return _StubEncoder(model_id=vf.MODEL_ALIASES.get(model, model))

    monkeypatch.setattr(vf, "_load_encoder", _loader)
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    rc = vf.main([str(src), "--model", "star", "--allow-undeclared-license", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert seen == {"allow_undeclared_license": True}
    assert payload["results"]["model_id"] == "AIDA-UPM/star"
    caveats = " ".join(payload["claim_license"]["additional_caveats"])
    assert "STAR" in caveats and "no license" in caveats
    # STAR earns no verdict: its refusal text is the same as LUAR's.
    luar = vf._claim_license(model_id=vf.MODEL_ALIASES["luar"], mode="single")
    star = vf._claim_license(model_id=vf.MODEL_ALIASES["star"], mode="single")
    assert luar.does_not_license == star.does_not_license
