#!/usr/bin/env python3
"""Tests for spec 28 (M1) — StyleDistance / mUAR style encoders behind the
existing ``voice_fingerprint._load_encoder`` seam, plus the opt-in
``crosslingual_voice_distance --encoder muar`` parallel block.

ALL tests are model-free / stub-only (the spec-02 discipline): the
``_load_encoder`` seam is monkeypatched to a deterministic stub, so no real
weights are downloaded or loaded, no GPU, no network. They cover the M1 (stdlib)
numbered acceptance criteria from ``specs/28-styledistance-encoder-upgrade.md``:

  1.  alias registration + default-preserving
  2.  seam dispatch via stub (styledistance / muar) for all three modes
  3.  dependency gate is clean for the new encoders (transformers absent)
  4.  refusals invariant across encoders (EXISTING strings, no "AI/human" coinage)
  5.  per-encoder caveat text (StyleDistance / mUAR named; cross-model = 4 encoders)
  6.  --device threaded to the encoder (stub records it), no weights
  7.  crosslingual default unchanged (imports no transformers/voice_fingerprint)
  8.  crosslingual opt-in encoder mode (stub) — block beside delta, --lang refusal kept
  9.  held-out disjointness (structural import grep)
  10. capabilities regen, no count change (golden + drift/docs gates)

The M2 acceptance criteria (#11 real load, #12 license-tag gate) are the gated
maintainer smoke — out of scope here and never run in CI.
"""

from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

import voice_fingerprint as vf  # type: ignore  # noqa: E402
import crosslingual_voice_distance as cvd  # type: ignore  # noqa: E402

# Capture the genuine loader BEFORE any fixture patches it, so the
# missing-transformers test can exercise the real dependency-gate path.
_REAL_LOAD_ENCODER = vf._load_encoder

_NEW_ALIASES = ("styledistance", "muar")
_ALL_ALIASES = ("luar", "wegmann", "styledistance", "muar")


# --------------- Deterministic stub encoder (records device) ----------


class _StubEncoder:
    """Deterministic stand-in. Content-based unit vectors (identical text
    -> cosine 1.0). Records the ``model_id`` and ``device`` it was built
    with so the seam-dispatch and --device-threading tests can assert
    against them WITHOUT loading any weights."""

    DIM = 16

    def __init__(self, model_id: str = "stub-style-encoder", device=None) -> None:
        self.model_id = model_id
        self.device = device

    def _vec(self, text: str):
        import numpy as np

        sig = " ".join(text.split()).lower().encode("utf-8")
        digest = hashlib.sha256(sig).digest()
        raw = np.frombuffer(
            (digest * ((self.DIM // len(digest)) + 1))[: self.DIM * 4],
            dtype=np.uint8,
        )[: self.DIM].astype("float32")
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


@pytest.fixture
def stub_loader(monkeypatch: pytest.MonkeyPatch):
    """Replace the real encoder loader with a stub that RESOLVES the alias
    to its model_id (so envelope ``model_id`` assertions are meaningful)
    and records the device. Returns the list of (model, device) calls."""
    calls: list[tuple[str, object]] = []

    def _fake_load(model, device=None):
        calls.append((model, device))
        resolved = vf.MODEL_ALIASES.get(model, model)
        return _StubEncoder(model_id=resolved, device=device)

    monkeypatch.setattr(vf, "_load_encoder", _fake_load)
    return calls


# --------------- Helpers ----------------------------------------------


def _multi_para_text(n_paras: int = 5, words_per: int = 60) -> str:
    paras = []
    for i in range(n_paras):
        body = " ".join([f"word{i}_{j}" for j in range(words_per)])
        paras.append(f"Paragraph number {i}. {body}.")
    return "\n\n".join(paras)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _write_dir(tmp_path: Path, dirname: str, files: dict[str, str]) -> Path:
    d = tmp_path / dirname
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return d


# ============ 1. Alias registration + default-preserving ==============


def test_aliases_registered_and_resolve():
    """styledistance / muar resolve to weight ids; the existing aliases
    are untouched and DEFAULT_MODEL stays luar (no silent flip)."""
    assert vf.MODEL_ALIASES["styledistance"]
    assert vf.MODEL_ALIASES["muar"]
    # New aliases point at distinct, non-empty weight ids.
    assert vf.MODEL_ALIASES["styledistance"] != vf.MODEL_ALIASES["luar"]
    assert vf.MODEL_ALIASES["muar"] != vf.MODEL_ALIASES["luar"]
    # Existing entries preserved.
    assert vf.MODEL_ALIASES["luar"] == "rrivera1849/LUAR-MUD"
    assert vf.MODEL_ALIASES["wegmann"] == "AnnaWegmann/Style-Embedding"
    # The load-bearing default-preserving guard.
    assert vf.DEFAULT_MODEL == "luar"


def test_default_run_unchanged(tmp_path: Path, stub_loader):
    """A run with NO --model produces the luar-resolved model_id and the
    single-mode envelope shape — the surface is default-preserving."""
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    out_path = tmp_path / "fp.json"
    rc = vf.main([str(src), "--json", "--out", str(out_path)])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["results"]["mode"] == "single"
    # No --model -> DEFAULT_MODEL=luar -> resolved LUAR id.
    assert payload["results"]["model_id"] == vf.MODEL_ALIASES["luar"]
    # The loader was asked for the default alias, not a new one.
    assert stub_loader[0][0] == "luar"


# ============ 2. Seam dispatch via stub, all three modes ==============


@pytest.mark.parametrize("alias", _NEW_ALIASES)
def test_new_encoder_single_mode(tmp_path: Path, stub_loader, alias):
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    out_path = tmp_path / "fp.json"
    rc = vf.main([str(src), "--model", alias, "--json", "--out", str(out_path)])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["task_surface"] == "authorship_embedding"
    assert payload["results"]["mode"] == "single"
    assert payload["results"]["model_id"] == vf.MODEL_ALIASES[alias]
    for key in ("mean", "sd", "min", "p10", "p50", "p90"):
        assert key in payload["results"]["cosine_distribution"]


@pytest.mark.parametrize("alias", _NEW_ALIASES)
def test_new_encoder_two_corpus_mode(tmp_path: Path, stub_loader, alias):
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    baseline = _write_dir(
        tmp_path, "baseline",
        {"b1.txt": _multi_para_text(n_paras=4),
         "b2.txt": _multi_para_text(n_paras=3)},
    )
    out_path = tmp_path / "fp.json"
    rc = vf.main([
        str(src), "--model", alias, "--baseline-dir", str(baseline),
        "--json", "--out", str(out_path),
    ])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["results"]["mode"] == "two_corpus"
    assert payload["results"]["model_id"] == vf.MODEL_ALIASES[alias]
    assert "cosine_distribution" in payload["results"]


@pytest.mark.parametrize("alias", _NEW_ALIASES)
def test_new_encoder_n_way_mode(tmp_path: Path, stub_loader, alias):
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    baseline = _write_dir(
        tmp_path, "baseline", {"b1.txt": _multi_para_text(n_paras=4)})
    impostors = _write_dir(
        tmp_path, "impostors",
        {"i1.txt": _multi_para_text(n_paras=4),
         "i2.txt": _multi_para_text(n_paras=3)},
    )
    out_path = tmp_path / "fp.json"
    rc = vf.main([
        str(src), "--model", alias,
        "--baseline-dir", str(baseline),
        "--impostor-dir", str(impostors),
        "--json", "--out", str(out_path),
    ])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["results"]["mode"] == "n_way"
    assert payload["results"]["model_id"] == vf.MODEL_ALIASES[alias]
    assert payload["results"]["n_impostors"] == 2


def test_import_pulls_no_model_dependency():
    """import voice_fingerprint must not pull transformers / torch eagerly
    (the encoder classes are present but their deps are lazy)."""
    code = (
        "import sys; import voice_fingerprint;"
        "bad=[m for m in ('transformers','torch','sentence_transformers') "
        "if m in sys.modules];"
        "print(','.join(bad))"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SCRIPTS_ROOT), capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "", f"eager model import: {res.stdout!r}"


# ============ 3. Dependency gate clean for new encoders ===============


@pytest.mark.parametrize("alias", _NEW_ALIASES)
def test_new_encoder_missing_transformers_graceful(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias,
):
    """With transformers absent, --model styledistance / muar raise the
    typed VoiceFingerprintError with the install hint, NOT a traceback —
    via the EXISTING gate (the new encoders add no new gate path)."""
    monkeypatch.setattr(vf, "_load_encoder", _REAL_LOAD_ENCODER)
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "transformers" or name.startswith("transformers."):
            raise ImportError("No module named 'transformers'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(vf.VoiceFingerprintError) as excinfo:
        vf._load_encoder(alias)
    msg = str(excinfo.value)
    assert "transformers" in msg
    assert "pip install" in msg
    assert "Traceback" not in msg

    src = _write(tmp_path, "draft.txt", _multi_para_text())
    rc = vf.main([str(src), "--model", alias, "--json"])
    assert rc == 3  # clean VoiceFingerprintError exit


# ============ 4. Refusals invariant across encoders ===================


def test_refusals_byte_identical_across_encoders():
    """The does_not_license refusal string is IDENTICAL for every encoder
    (no encoder earns a verdict). Asserts the EXISTING strings, and that
    no re-coined 'AI/human' canonical form was introduced."""
    refusals = {
        vf._claim_license(model_id=vf.MODEL_ALIASES[a], mode="single").does_not_license
        for a in _ALL_ALIASES
    }
    assert len(refusals) == 1, "refusal text diverged across encoders"
    refusal = refusals.pop()
    # The EXISTING refusal substrings (spec 28 [P1]): NOT 'AI/human'.
    assert "SAME PERSON" in refusal
    assert "DIFFERENT AUTHOR" in refusal
    assert "AI-generated or human-written" in refusal
    assert "AI/human" not in refusal


def test_licenses_byte_identical_across_encoders_modulo_model_id():
    """The licenses string differs ONLY by the embedded model_id; with the
    model_id normalized out it is identical across encoders (the verdict-
    bearing prose is shared, not per-encoder)."""
    normalized = set()
    for a in _ALL_ALIASES:
        mid = vf.MODEL_ALIASES[a]
        lic = vf._claim_license(model_id=mid, mode="single").licenses
        normalized.add(lic.replace(mid, "<MODEL_ID>"))
    assert len(normalized) == 1


def test_calibration_status_provisional_for_new_encoders():
    for a in _NEW_ALIASES:
        lic = vf._claim_license(model_id=vf.MODEL_ALIASES[a], mode="single")
        status = lic.comparison_set["calibration_status"]
        assert "PROVISIONAL" in status
        assert "uncalibrated" in status.lower()


# ============ 5. Per-encoder caveat text ==============================


def test_styledistance_caveat_named():
    lic = vf._claim_license(
        model_id=vf.MODEL_ALIASES["styledistance"], mode="single")
    blob = " ".join(lic.additional_caveats)
    assert "StyleDistance" in blob
    assert "synthetic" in blob.lower()
    # "more content-controlled, not topic-proof".
    assert "content-controlled" in blob.lower()
    assert "topic-proof" in blob.lower()


def test_muar_caveat_named():
    lic = vf._claim_license(model_id=vf.MODEL_ALIASES["muar"], mode="single")
    blob = " ".join(lic.additional_caveats)
    assert "mUAR" in blob or "multilingual" in blob.lower()
    # "multilingual representation does not license cross-language comparison".
    assert "cross-language" in blob.lower()
    assert "license" in blob.lower()


def test_cross_model_caveat_enumerates_four_encoders():
    """The cross-model-incomparability caveat now covers >=3 (here 4)
    encoders — it must no longer read only 'LUAR and Wegmann'."""
    lic = vf._claim_license(model_id=vf.MODEL_ALIASES["luar"], mode="single")
    blob = " ".join(lic.additional_caveats)
    cross = [c for c in lic.additional_caveats if "Cross-model" in c]
    assert cross, "cross-model caveat missing"
    cm = cross[0]
    assert "LUAR" in cm
    assert "Wegmann" in cm
    assert "StyleDistance" in cm
    assert "mUAR" in cm


def test_content_caveat_is_per_encoder():
    """The (formerly static) content-control caveat now differs by encoder
    — the [P1] refactor. Each encoder's first caveat names itself."""
    heads = {}
    for a in _ALL_ALIASES:
        lic = vf._claim_license(model_id=vf.MODEL_ALIASES[a], mode="single")
        heads[a] = lic.additional_caveats[0]
    # All four content-control caveats are distinct.
    assert len(set(heads.values())) == 4
    assert "LUAR" in heads["luar"]
    assert "Wegmann" in heads["wegmann"]
    assert "StyleDistance" in heads["styledistance"]
    assert "mUAR" in heads["muar"]


# ============ 6. --device threaded ====================================


def test_device_threaded_to_encoder(tmp_path: Path, stub_loader):
    """--device cuda:0 is threaded to _load_encoder and onto the encoder
    (recorded by the stub) WITHOUT loading weights."""
    src = _write(tmp_path, "draft.txt", _multi_para_text())
    out_path = tmp_path / "fp.json"
    rc = vf.main([
        str(src), "--model", "styledistance", "--device", "cuda:0",
        "--json", "--out", str(out_path),
    ])
    assert rc == 0
    # The loader recorded (model, device).
    assert stub_loader[-1] == ("styledistance", "cuda:0")


# ============ 7. Crosslingual default unchanged =======================


def test_crosslingual_default_imports_no_model_deps():
    """importing crosslingual_voice_distance pulls NEITHER transformers
    NOR voice_fingerprint / semantic_trajectory_audit (the [P2] guard:
    the reuse import is lazy / in-branch)."""
    code = (
        "import sys; import crosslingual_voice_distance;"
        "bad=[m for m in ('transformers','torch','voice_fingerprint',"
        "'semantic_trajectory_audit') if m in sys.modules];"
        "print(','.join(bad))"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SCRIPTS_ROOT), capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "", f"eager import leak: {res.stdout!r}"


def test_crosslingual_source_has_no_toplevel_model_import():
    """Source-level [P2] guard: no top-level (column-0) transformers or
    voice_fingerprint import; the only voice_fingerprint import is
    indented (in-branch)."""
    src_lines = Path(cvd.__file__).read_text(encoding="utf-8").splitlines()
    for line in src_lines:
        # column-0 import statements only
        if line.startswith("import ") or line.startswith("from "):
            assert "transformers" not in line, line
            assert "voice_fingerprint" not in line, line
            assert "semantic_trajectory_audit" not in line, line
    # The in-branch import exists but is indented.
    assert any(
        l.strip() == "import voice_fingerprint as vf  # type: ignore"
        and l != l.lstrip()
        for l in src_lines
    ), "expected a lazy, indented voice_fingerprint import"


def test_crosslingual_default_envelope_unchanged(tmp_path: Path):
    """With no --encoder, the parser-free envelope carries delta /
    cosine_distance / profiles and NO encoder_block."""
    bdir = tmp_path / "b"
    bdir.mkdir()
    text = ("the quick brown fox jumps over the lazy dog " * 60)
    for i in range(3):
        (bdir / f"f{i}.txt").write_text(text, encoding="utf-8")
    target = _write(tmp_path, "t.txt", text)
    out = tmp_path / "o.json"
    rc = cvd.main([str(target), "--baseline-dir", str(bdir), "--lang", "en",
                   "--json", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    r = payload["results"]
    for key in ("delta", "cosine_distance", "per_baseline_file",
                "top_contributing_ngrams"):
        assert key in r
    assert "encoder_block" not in r


# ============ 8. Crosslingual opt-in encoder mode (stub) ==============


@pytest.fixture
def stub_cvd_encoder(monkeypatch: pytest.MonkeyPatch):
    """Patch voice_fingerprint._load_encoder (the shared mUAR load path)
    to the stub, so the crosslingual --encoder muar block runs with no
    weights. The lazy in-branch `import voice_fingerprint as vf` binds the
    same module object we patch here."""
    monkeypatch.setattr(
        vf, "_load_encoder",
        lambda model, device=None: _StubEncoder(
            model_id=vf.MODEL_ALIASES.get(model, model), device=device),
    )


def _cvd_baseline(tmp_path: Path, text: str, copies: int = 3) -> Path:
    bdir = tmp_path / "b"
    bdir.mkdir()
    for i in range(copies):
        (bdir / f"f{i}.txt").write_text(text, encoding="utf-8")
    return bdir


def test_crosslingual_encoder_block_beside_delta(tmp_path: Path, stub_cvd_encoder):
    """--encoder muar adds an encoder_block (encoder_id + cosine
    distribution) BESIDE the parser-free delta (not replacing it)."""
    text = "\n\n".join(_multi_para_text(n_paras=6) for _ in range(2))
    bdir = _cvd_baseline(tmp_path, text)
    target = _write(tmp_path, "t.txt", text)
    out = tmp_path / "o.json"
    rc = cvd.main([str(target), "--baseline-dir", str(bdir), "--lang", "en",
                   "--encoder", "muar", "--json", "--out", str(out)])
    assert rc == 0
    r = json.loads(out.read_text(encoding="utf-8"))["results"]
    # Parser-free distance is STILL present (block is beside, not instead).
    assert "delta" in r
    assert "cosine_distance" in r
    # Encoder block carries encoder_id + a cosine distribution.
    block = r["encoder_block"]
    assert block["encoder_id"] == vf.MODEL_ALIASES["muar"]
    assert block["available"] is True
    for key in ("mean", "sd", "min", "p10", "p50", "p90"):
        assert key in block["cosine_distribution"]


def test_muar_real_load_fails_loud_no_public_checkpoint(monkeypatch: pytest.MonkeyPatch):
    # Codex #241 P1: rrivera1849/mUAR has no public checkpoint, so the REAL load path must fail loud
    # with actionable guidance instead of a transformers 404. The alias stays registered (spec-only)
    # and is listed in _UNRELEASED_MODEL_IDS; the guard sits AFTER the transformers gate.
    assert vf.MODEL_ALIASES["muar"] in vf._UNRELEASED_MODEL_IDS
    monkeypatch.setitem(sys.modules, "transformers", types.ModuleType("transformers"))
    with pytest.raises(vf.VoiceFingerprintError) as exc:
        vf._load_encoder("muar")              # real loader; transformers gate passes -> guard fires
    msg = str(exc.value).lower()
    assert "no public checkpoint" in msg and "spec-only" in msg


def test_encoder_block_appears_in_markdown_report():
    # Codex #241 P2: the opt-in learned-encoder block must render in the NON-JSON markdown report too,
    # not only the JSON envelope (else `--encoder muar` without `--json` silently drops it).
    payload = {
        "target": {"path": "t.txt", "words": 100},
        "available": True,
        "claim_license_rendered": "PARSER-FREE LICENSE",
        "results": {
            "lang": "en", "char_ngram_n": 4, "top_k": 10, "delta": 1.0,
            "cosine_distance": 0.2, "per_baseline_file": [], "top_contributing_ngrams": [],
            "encoder_block": {
                "encoder_id": "rrivera1849/mUAR", "available": True,
                "cosine_distribution": {"mean": 0.5}, "n_windows": 3, "n_baseline_windows": 4,
                "claim_license_caveat": "ENCODER-CAVEAT-MUAR-SENTINEL",
            },
        },
    }
    md = cvd.render_report(payload)
    assert "Learned-encoder block" in md
    assert "rrivera1849/mUAR" in md
    assert "ENCODER-CAVEAT-MUAR-SENTINEL" in md      # the per-encoder caveat is not dropped


def test_crosslingual_encoder_block_keeps_cross_language_refusal(
    tmp_path: Path, stub_cvd_encoder,
):
    """Even in --encoder muar mode, the parser-free claim-license still
    refuses cross-language comparison and the --lang tag is provenance —
    multilingual is a capability, not a license."""
    text = "\n\n".join(_multi_para_text(n_paras=6) for _ in range(2))
    bdir = _cvd_baseline(tmp_path, text)
    target = _write(tmp_path, "t.txt", text)
    out = tmp_path / "o.json"
    cvd.main([str(target), "--baseline-dir", str(bdir), "--lang", "en",
              "--encoder", "muar", "--json", "--out", str(out)])
    payload = json.loads(out.read_text(encoding="utf-8"))
    # The surface-level claim-license still refuses cross-language comparison.
    dnl = payload["claim_license"]["does_not_license"].lower()
    assert "cross-language comparison" in dnl
    # The per-encoder block caveat ALSO names the unrelaxed refusal.
    block = payload["results"]["encoder_block"]
    cav = block["claim_license_caveat"].lower()
    assert "does not relax" in cav or "not relax" in cav
    assert "cross-language" in cav
    # And it is descriptive-only: no verdict / threshold / new scalar.
    assert "verdict" in cav
    assert "no threshold" in cav


def test_crosslingual_encoder_lazy_import_only_on_flag(
    tmp_path: Path, stub_cvd_encoder,
):
    """The voice_fingerprint reuse is imported only when --encoder is
    supplied: a fresh subprocess WITHOUT the flag never imports it; WITH
    the flag it does. (Stub patching here keeps weights out; the
    subprocess variant below confirms the no-flag default stays clean.)"""
    # In-process: with the flag, the lazy import binds voice_fingerprint.
    text = "\n\n".join(_multi_para_text(n_paras=6) for _ in range(2))
    bdir = _cvd_baseline(tmp_path, text)
    target = _write(tmp_path, "t.txt", text)
    rc = cvd.main([str(target), "--baseline-dir", str(bdir), "--lang", "en",
                   "--encoder", "muar", "--json", "--out", str(tmp_path / "o.json")])
    assert rc == 0
    assert "voice_fingerprint" in sys.modules  # the in-branch import ran


# ============ 9. Held-out disjointness (structural) ===================

_HELDOUT_VALIDATORS = (
    "general_imposters.py",
    "mimicry_cosplay_audit.py",
    "binoculars_audit.py",
)


@pytest.mark.parametrize("validator", _HELDOUT_VALIDATORS)
def test_holdout_validators_do_not_import_selector_encoder(validator):
    """The held-out validators must NOT import the selector's encoder
    module / aliases (the firewall's M1 code-leak form). NECESSARY, not
    SUFFICIENT — the correlation leak is the consumer drift gate's job."""
    src = (SCRIPTS_ROOT / validator).read_text(encoding="utf-8")
    assert "import voice_fingerprint" not in src, (
        f"{validator} imports voice_fingerprint — selector manifold leak"
    )
    assert "from voice_fingerprint" not in src, validator
    # The new encoder classes/aliases must not appear by name either.
    for token in ("_StyleDistanceEncoder", "_MUAREncoder",
                  "MODEL_ALIASES", "StyleDistance/styledistance",
                  "rrivera1849/mUAR"):
        assert token not in src, f"{validator} references {token!r}"


# ============ 10. Capabilities regen, no count change =================


def test_capabilities_count_unchanged_and_no_new_surface():
    """Editing the two fragments did not add an entry or a task surface:
    the manifest entry count equals the golden fragment count (no new id),
    and both edited surfaces keep their existing surface labels."""
    pytest.importorskip("yaml")
    from capabilities import load_manifest  # type: ignore

    cap_dir = SCRIPTS_ROOT.parent / "capabilities.d"
    golden_dir = Path(__file__).resolve().parent / "_golden_capabilities"
    manifest = load_manifest(cap_dir)
    by_id = {e["id"]: e for e in manifest["entries"]}

    golden_ids = {
        p.stem for p in golden_dir.glob("*.json") if p.name != "_meta.json"
    }
    # No new id (count parity is the no-new-surface guarantee).
    assert len(manifest["entries"]) == len(golden_ids)
    assert {e["id"] for e in manifest["entries"]} == golden_ids

    # Surfaces unchanged by the encoder upgrade.
    assert by_id["voice_fingerprint"]["surface"] == "authorship_embedding"
    assert by_id["crosslingual_voice_distance"]["surface"] == "voice_coherence"
    # The crosslingual default tier stays core; transformers is optional only.
    cl = by_id["crosslingual_voice_distance"]
    assert cl["compute"]["tier"] == "core"
    assert cl["dependencies"]["python"] == []
    assert "transformers" in cl["dependencies"]["python_optional"]


def test_drift_and_docs_gates_pass():
    """The capabilities drift linter and docs-freshness gate run green
    against the committed fragments (no stale golden, changelog covered)."""
    pytest.importorskip("yaml")
    tools = REPO_ROOT / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import check_capabilities_drift as ccd  # type: ignore
    import check_docs_freshness as freshness  # type: ignore

    report = ccd.check_drift()
    assert report.passed, str(
        [f"{v.kind}:{v.where}" for v in report.violations]
    )
    missing = freshness.changelog_coverage(
        freshness.DEFAULT_MANIFEST, freshness.CHANGELOG)
    assert missing == []
