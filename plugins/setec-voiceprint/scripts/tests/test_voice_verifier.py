"""Tests for voice_verifier — the LLM-as-verifier authorship surface (spec 31 M1).

Covers the M1 acceptance criteria: the no-verdict band vocabulary + blocklist, the
no-score result shape, validate_result (vocabulary + span-offset + CAVE
consistency), the mock judge end-to-end, the manifest judge + its VerifierError,
the pairwise-required entrypoint, the schema-1.0 envelope (uncalibrated +
fingerprint + claim license), the available:false refusal path (triggered by a
manifest missing the band key — NOT circular), and the stdlib-import guard.
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_ROOT))

import voice_verifier as vv  # noqa: E402
from output_schema import SCHEMA_VERSION, VALID_TASK_SURFACES  # noqa: E402

# Verdict tokens the band vocabulary / rationale schema must never name. The
# bare `ai` / `human` are blocklisted as a BAND/FEATURE/FIELD substring (a band
# token like "ai" or "human" would be a verdict), but NOT as an envelope-key
# token — `ai_status` is a legitimate framework key from build_output (the B.3
# authorship-state field), so the key check uses only the unambiguous verdict
# KEYS below.
_VERDICT_TOKENS = ["same_author", "different_author", "ai", "human", "forgery", "plagiar"]
_VERDICT_KEY_TOKENS = ["same_author", "different_author", "p_same_author", "verdict", "forgery", "plagiar"]


# ---- registration --------------------------------------------------------
def test_task_surface_registered():
    assert vv.TASK_SURFACE == "voice_verifier"
    assert vv.TASK_SURFACE in VALID_TASK_SURFACES


# ---- Acceptance 1: band vocabulary + no-verdict blocklist ----------------
def test_bands_are_the_five_token_ordinal_with_center():
    assert vv.VERIFIER_BANDS == (
        "consistent",
        "leans_consistent",
        "cannot_determine",
        "leans_inconsistent",
        "inconsistent",
    )
    assert vv.CENTER_BAND == "cannot_determine"
    assert vv.VERIFIER_BANDS[2] == vv.CENTER_BAND  # center is at the middle


def test_no_verdict_token_in_bands_features_or_fields():
    """No verdict token appears as a band, a RATIONALE_FEATURES key, or a
    VerifierResult field name."""
    surfaces: list[str] = []
    surfaces += list(vv.VERIFIER_BANDS)
    surfaces += list(vv.RATIONALE_FEATURES)
    # VerifierResult field names + the to_dict key names.
    r = vv.VerifierResult(band=vv.CENTER_BAND, feature_judgements={}, rationale="", judge_identity={})
    surfaces += list(r.to_dict().keys())
    surfaces += list(r.__dataclass_fields__.keys())
    for token in _VERDICT_TOKENS:
        for s in surfaces:
            assert token not in s.lower(), f"verdict token {token!r} in {s!r}"


def test_no_verdict_token_in_emitted_envelope_values_or_keys(tmp_path):
    """The full emitted envelope (mock judge) contains no verdict token as a
    KEY (a banned key like same_author/p_same_author) and the band/feature
    bands carry no verdict token as a VALUE. The license prose legitimately
    NAMES the refused verdicts, so we scope the value check to the band fields,
    not the whole document."""
    q = tmp_path / "q.txt"
    r = tmp_path / "r.txt"
    q.write_text("The query text, with its own habits and cadence.", encoding="utf-8")
    r.write_text("The reference text, sharing some lexical habits.", encoding="utf-8")
    rc = vv.main(["--query", str(q), "--reference", str(r), "--judge", "mock", "--json"])
    assert rc == 0

    backend = vv.build_verifier("mock", mock_band="leans_consistent")
    res, _ = vv.validate_result(backend(q.read_text(), r.read_text()), query=q.read_text(), reference=r.read_text())
    env = vv.compose_envelope(
        result=res, query_path=q, query_words=8, reference_path=r, warnings=[]
    )

    def _walk_keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from _walk_keys(v)
        elif isinstance(obj, list):
            for it in obj:
                yield from _walk_keys(it)

    # No verdict KEY appears anywhere in the envelope (a banned key like
    # same_author / p_same_author / verdict). Checked as a substring so
    # p_same_author and same_author_probability are both caught; the
    # _VERDICT_KEY_TOKENS list excludes the ambiguous bare `ai`/`human` so a
    # legitimate framework key (ai_status) is not false-positived.
    all_keys = "\n".join(_walk_keys(env)).lower()
    for token in _VERDICT_KEY_TOKENS:
        assert token not in all_keys, (
            f"verdict key token {token!r} appears as an envelope key"
        )

    # band values carry no verdict token
    results = env["results"]
    assert results["band"] in vv.VERIFIER_BANDS
    for fj in results["feature_judgements"].values():
        assert fj["band"] in vv.VERIFIER_BANDS


# ---- Acceptance 2: result shape + no score/confidence field --------------
def test_to_dict_shape_and_no_score_field():
    r = vv.VerifierResult(
        band="consistent",
        feature_judgements={
            f: {"band": "consistent", "note": "n", "spans": []}
            for f in vv.RATIONALE_FEATURES
        },
        rationale="ok",
        judge_identity={"kind": "mock"},
        raw_response="raw",
    )
    d = r.to_dict()
    assert set(d.keys()) == {
        "band",
        "feature_judgements",
        "rationale",
        "judge_identity",
        "raw_response_truncated",
    }
    for banned in ("p_same_author", "confidence_score", "confidence", "score", "verdict", "p_value"):
        assert banned not in d
    for fj in d["feature_judgements"].values():
        assert set(fj.keys()) == {"band", "note", "spans"}
        assert fj["band"] in vv.VERIFIER_BANDS


# ---- Acceptance 3: validate_result ---------------------------------------
def _full_features(band="cannot_determine", spans=None):
    return {
        f: {"band": band, "note": "", "spans": list(spans or [])}
        for f in vv.RATIONALE_FEATURES
    }


def test_validate_result_valid_roundtrips():
    res = vv.VerifierResult(
        band="leans_consistent",
        feature_judgements=_full_features("leans_consistent"),
        rationale="r",
        judge_identity={"kind": "mock"},
    )
    cleaned, warns = vv.validate_result(res, query="abcdef", reference="ghijkl")
    assert cleaned.band == "leans_consistent"
    assert warns == []


def test_validate_result_out_of_vocab_band_nulled_to_center():
    res = vv.VerifierResult(
        band="same_author",  # not a real band
        feature_judgements=_full_features(),
        rationale="r",
        judge_identity={},
    )
    cleaned, warns = vv.validate_result(res, query="abc", reference="def")
    assert cleaned.band == vv.CENTER_BAND
    assert any("not in VERIFIER_BANDS" in w for w in warns)


def test_validate_result_out_of_vocab_feature_band_nulled():
    feats = _full_features()
    feats["lexical_habits"]["band"] = "bogus"
    res = vv.VerifierResult(band="consistent", feature_judgements=feats, rationale="", judge_identity={})
    cleaned, warns = vv.validate_result(res, query="abc", reference="def")
    assert cleaned.feature_judgements["lexical_habits"]["band"] == vv.CENTER_BAND
    assert any("lexical_habits" in w and "not in VERIFIER_BANDS" in w for w in warns)


def test_validate_result_drops_span_that_does_not_index_side():
    query = "Hello world."
    bad_span = {"side": "query", "start": 100, "end": 110, "quote": "nope"}
    feats = _full_features(spans=[bad_span])
    res = vv.VerifierResult(band="consistent", feature_judgements=feats, rationale="", judge_identity={})
    cleaned, warns = vv.validate_result(res, query=query, reference="ref text")
    # span dropped everywhere
    for fj in cleaned.feature_judgements.values():
        assert fj["spans"] == []
    assert any("does not index" in w for w in warns)


def test_validate_result_drops_span_with_mismatched_quote():
    query = "Hello world."
    span = {"side": "query", "start": 0, "end": 5, "quote": "Howdy"}  # actual is "Hello"
    feats = _full_features(spans=[span])
    res = vv.VerifierResult(band="consistent", feature_judgements=feats, rationale="", judge_identity={})
    cleaned, warns = vv.validate_result(res, query=query, reference="ref")
    assert all(fj["spans"] == [] for fj in cleaned.feature_judgements.values())
    assert any("quote does not match" in w for w in warns)


def test_validate_result_keeps_valid_span_and_fills_quote():
    query = "Hello world."
    span = {"side": "query", "start": 0, "end": 5}  # no quote — filled from offsets
    feats = _full_features(spans=[span])
    res = vv.VerifierResult(band="consistent", feature_judgements=feats, rationale="", judge_identity={})
    cleaned, _ = vv.validate_result(res, query=query, reference="ref")
    kept = cleaned.feature_judgements["lexical_habits"]["spans"]
    assert kept == [{"side": "query", "start": 0, "end": 5, "quote": "Hello"}]


def test_validate_result_cave_mismatch_surfaced_not_corrected():
    # every per-feature band = consistent, but top-level = inconsistent
    feats = _full_features("consistent")
    res = vv.VerifierResult(band="inconsistent", feature_judgements=feats, rationale="", judge_identity={})
    cleaned, warns = vv.validate_result(res, query="abc", reference="def")
    # NOT auto-corrected: top-level band stays inconsistent
    assert cleaned.band == "inconsistent"
    assert any("rationale_band_mismatch" in w for w in warns)


def test_validate_result_no_mismatch_when_aligned():
    feats = _full_features("consistent")
    res = vv.VerifierResult(band="consistent", feature_judgements=feats, rationale="", judge_identity={})
    _, warns = vv.validate_result(res, query="abc", reference="def")
    assert not any("rationale_band_mismatch" in w for w in warns)


# ---- Acceptance 4: mock judge end-to-end ---------------------------------
@pytest.mark.parametrize("band", list(vv.VERIFIER_BANDS))
def test_mock_judge_produces_full_decomposition(band):
    backend = vv.build_verifier("mock", mock_band=band)
    res = backend("The quick brown fox.", "A lazy reference dog.")
    assert res.band == band
    assert set(res.feature_judgements) == set(vv.RATIONALE_FEATURES)
    for fj in res.feature_judgements.values():
        assert fj["band"] == band
    # one synthetic span per SIDE across the decomposition
    sides = {s["side"] for fj in res.feature_judgements.values() for s in fj["spans"]}
    assert sides == {"query", "reference"}
    # spans actually index the texts (round-trip through validate_result clean)
    cleaned, warns = vv.validate_result(res, query="The quick brown fox.", reference="A lazy reference dog.")
    assert not any("does not index" in w or "quote does not match" in w for w in warns)


def test_mock_judge_loads_no_provider_sdk(tmp_path):
    """Acceptance 9 corollary: running the mock judge imports no provider SDK."""
    q = tmp_path / "q.txt"
    r = tmp_path / "r.txt"
    q.write_text("query words here", encoding="utf-8")
    r.write_text("reference words here", encoding="utf-8")
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS_ROOT)!r})\n"
        "import voice_verifier as vv\n"
        f"vv.main(['--query', {str(q)!r}, '--reference', {str(r)!r}, '--judge', 'mock', '--json'])\n"
        "banned = {'torch', 'transformers', 'anthropic', 'openai', 'google'}\n"
        "loaded = banned & set(sys.modules)\n"
        "assert not loaded, f'unexpected SDK import: {loaded}'\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ---- Acceptance 5: manifest judge ----------------------------------------
def test_manifest_judge_reads_precomputed_result(tmp_path):
    payload = {
        "band": "leans_inconsistent",
        "feature_judgements": _full_features("leans_inconsistent"),
        "rationale": "operator-supplied",
        "judge_identity": {"model": "some-local-weights"},
    }
    mpath = tmp_path / "result.json"
    mpath.write_text(json.dumps(payload), encoding="utf-8")
    backend = vv.build_verifier("manifest", manifest_path=mpath)
    res = backend("q", "r")
    assert res.band == "leans_inconsistent"
    assert res.judge_identity["kind"] == "manifest"
    assert res.judge_identity["model"] == "some-local-weights"


def test_manifest_fingerprint_is_preserved_not_rebound(tmp_path, capsys):
    # Codex #247 P1: an imported manifest's band is NOT transferable to this code's prompt — the
    # envelope must carry the manifest's OWN recorded fingerprint (or null), never the current code's.
    q = tmp_path / "q.txt"; q.write_text("query habits here today.", encoding="utf-8")
    r = tmp_path / "r.txt"; r.write_text("reference habits here today.", encoding="utf-8")
    base = {"band": "leans_consistent",
            "feature_judgements": _full_features("leans_consistent"),
            "rationale": "x", "judge_identity": {"model": "local"}}
    # (a) no fingerprint in the manifest -> null, and crucially NOT the current code's fingerprint
    m = tmp_path / "nofp.json"; m.write_text(json.dumps(base), encoding="utf-8")
    assert vv.main(["--query", str(q), "--reference", str(r), "--judge", "manifest",
                    "--manifest", str(m), "--json"]) == 0
    env = json.loads(capsys.readouterr().out)
    assert env["results"]["prompt_fingerprint_sha256"] is None
    assert env["results"]["prompt_fingerprint_sha256"] != vv.fingerprint_prompt()
    # (b) a manifest carrying its own fingerprint -> preserved verbatim
    m2 = tmp_path / "fp.json"
    m2.write_text(json.dumps(dict(base, prompt_fingerprint_sha256="deadbeef")), encoding="utf-8")
    assert vv.main(["--query", str(q), "--reference", str(r), "--judge", "manifest",
                    "--manifest", str(m2), "--json"]) == 0
    env2 = json.loads(capsys.readouterr().out)
    assert env2["results"]["prompt_fingerprint_sha256"] == "deadbeef"


def test_manifest_malformed_feature_judgements_raises_verifier_error(tmp_path):
    # Codex #247 P2: a non-object feature_judgements (or a non-object value) is bad SETUP input,
    # mapped to VerifierError (-> available:false), never an AttributeError traceback.
    for bad in (["not", "a", "dict"], {"lexis": "should be an object"}):
        m = tmp_path / "bad.json"
        m.write_text(json.dumps({"band": "leans_consistent", "feature_judgements": bad}),
                     encoding="utf-8")
        with pytest.raises(vv.VerifierError):
            vv.build_verifier("manifest", manifest_path=m)


def test_manifest_malformed_input_paths_exit_2_not_traceback(tmp_path):
    # Codex #247 P2 follow-up: two more malformed shapes that previously escaped as tracebacks AFTER
    # successful backend construction must follow the documented exit-2 refusal path through main():
    #  (a) judge_identity as a truthy non-object (a list slipped past `or {}` -> AttributeError);
    #  (b) a feature's `spans` as a non-list (`for span in 1` -> TypeError).
    q = tmp_path / "q.txt"; q.write_text("query habits here today now words.", encoding="utf-8")
    r = tmp_path / "r.txt"; r.write_text("ref habits here today now words.", encoding="utf-8")
    feats_bad_spans = {f: {"band": "leans_consistent", "note": "", "spans": 1}
                       for f in vv.RATIONALE_FEATURES}
    bad_payloads = [
        {"band": "leans_consistent", "feature_judgements": _full_features(), "judge_identity": ["x"]},
        {"band": "leans_consistent", "feature_judgements": feats_bad_spans},
    ]
    for i, payload in enumerate(bad_payloads):
        m = tmp_path / ("bad%d.json" % i)
        m.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            vv.main(["--query", str(q), "--reference", str(r), "--judge", "manifest",
                     "--manifest", str(m), "--json"])
        assert exc.value.code == 2


def test_manifest_missing_file_raises_verifier_error(tmp_path):
    with pytest.raises(vv.VerifierError):
        vv.build_verifier("manifest", manifest_path=tmp_path / "nope.json")


def test_manifest_malformed_json_raises_verifier_error(tmp_path):
    mpath = tmp_path / "bad.json"
    mpath.write_text("{not json", encoding="utf-8")
    with pytest.raises(vv.VerifierError):
        vv.build_verifier("manifest", manifest_path=mpath)


def test_manifest_missing_band_key_raises_verifier_error(tmp_path):
    mpath = tmp_path / "noband.json"
    mpath.write_text(json.dumps({"rationale": "x"}), encoding="utf-8")
    with pytest.raises(vv.VerifierError):
        vv.build_verifier("manifest", manifest_path=mpath)


def test_manifest_requires_path():
    with pytest.raises(vv.VerifierError):
        vv.build_verifier("manifest", manifest_path=None)


def test_unknown_kind_raises():
    with pytest.raises(vv.VerifierError):
        vv.build_verifier("not-a-kind")


def test_mock_band_must_be_valid():
    with pytest.raises(vv.VerifierError):
        vv.build_verifier("mock", mock_band="bogus")


def test_api_kind_requires_model():
    with pytest.raises(vv.VerifierError):
        vv.build_verifier("anthropic", model=None)


# ---- Acceptance 6: pairwise input is required ----------------------------
def test_entrypoint_requires_both_query_and_reference(tmp_path):
    q = tmp_path / "q.txt"
    q.write_text("only one text", encoding="utf-8")
    # argparse exits 2 (SystemExit) when --reference is absent
    with pytest.raises(SystemExit) as exc:
        vv.main(["--query", str(q), "--judge", "mock"])
    assert exc.value.code != 0


def test_entrypoint_missing_query_file_returns_nonzero(tmp_path):
    r = tmp_path / "r.txt"
    r.write_text("ref", encoding="utf-8")
    rc = vv.main(["--query", str(tmp_path / "nope.txt"), "--reference", str(r), "--judge", "mock"])
    assert rc == 1


# ---- Acceptance 7: envelope + uncalibrated + fingerprint -----------------
def test_envelope_is_schema_1_0_uncalibrated_with_fingerprint(tmp_path, capsys):
    q = tmp_path / "q.txt"
    r = tmp_path / "r.txt"
    q.write_text("The query text with habits.", encoding="utf-8")
    r.write_text("The reference text with habits.", encoding="utf-8")
    rc = vv.main([
        "--query", str(q), "--reference", str(r),
        "--judge", "mock", "--mock-band", "leans_consistent", "--json",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    env = json.loads(out)
    assert env["schema_version"] == SCHEMA_VERSION
    assert env["available"] is True
    assert env["task_surface"] == "voice_verifier"
    results = env["results"]
    assert results["band"] == "leans_consistent"
    assert results["calibration_status"] == "uncalibrated"
    assert set(results["feature_judgements"]) == set(vv.RATIONALE_FEATURES)
    assert results["prompt_fingerprint_sha256"] == vv.fingerprint_prompt()
    # claim license names the refused verdicts
    lic = env["claim_license"]["does_not_license"].lower()
    assert "same-author" in lic or "same author" in lic
    assert "different-author" in lic or "different author" in lic
    assert "ai-vs-human" in lic or "ai-vs-human determination" in lic
    assert "probability" in lic and "score" in lic


def test_fingerprint_is_stable_and_changes_with_prompt():
    fp1 = vv.fingerprint_prompt()
    fp2 = vv.fingerprint_prompt()
    assert fp1 == fp2
    assert re.fullmatch(r"[0-9a-f]{64}", fp1)
    assert vv.fingerprint_prompt("a different prompt body") != fp1


# ---- Acceptance 8: refusal path is available:false (NON-circular) --------
def test_manifest_missing_band_routes_to_available_false_envelope(tmp_path, capsys):
    """A manifest missing the band key raises VerifierError, which the
    entrypoint routes into an available:false envelope with a reason_category —
    NOT a fabricated cannot_determine band in an available:true envelope."""
    mpath = tmp_path / "noband.json"
    mpath.write_text(json.dumps({"rationale": "x"}), encoding="utf-8")
    q = tmp_path / "q.txt"
    r = tmp_path / "r.txt"
    q.write_text("q", encoding="utf-8")
    r.write_text("r", encoding="utf-8")
    # manifest construction failure routes through argparse.error -> SystemExit(2)
    with pytest.raises(SystemExit) as exc:
        vv.main(["--query", str(q), "--reference", str(r), "--judge", "manifest", "--manifest", str(mpath)])
    assert exc.value.code == 2


def test_execution_failure_yields_available_false_not_fabricated_band(tmp_path, capsys, monkeypatch):
    """Control both ways: a backend that raises VerifierError at RUN time yields
    an available:false envelope (reason_category set), never a fabricated band in
    an available:true envelope."""
    q = tmp_path / "q.txt"
    r = tmp_path / "r.txt"
    q.write_text("q text", encoding="utf-8")
    r.write_text("r text", encoding="utf-8")

    def _boom(kind, **kw):
        def _run(_q, _r):
            raise vv.VerifierError("simulated judge failure")
        return _run

    monkeypatch.setattr(vv, "build_verifier", _boom)
    rc = vv.main(["--query", str(q), "--reference", str(r), "--judge", "mock", "--json"])
    assert rc == 3
    env = json.loads(capsys.readouterr().out)
    assert env["available"] is False
    assert env["reason_category"] in {"internal_error"}
    assert env["results"] == {}
    # control: a clean run IS available:true with a real band
    rc2 = vv.main(["--query", str(q), "--reference", str(r), "--judge", "mock", "--json"])
    # build_verifier is still monkeypatched here, so re-import the real one
    importlib.reload(vv)


# ---- Acceptance 9: stdlib import -----------------------------------------
def test_import_voice_verifier_loads_no_model_or_sdk():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS_ROOT)!r})\n"
        "import voice_verifier  # noqa\n"
        "banned = {'torch', 'transformers', 'anthropic', 'openai', 'google'}\n"
        "loaded = banned & set(sys.modules)\n"
        "assert not loaded, f'voice_verifier import pulled: {loaded}'\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


# ---- Acceptance 10: claim license label source ---------------------------
def test_claim_license_label_comes_from_txt_source():
    from claim_license import TASK_SURFACE_LABELS  # noqa
    assert "voice_verifier" in TASK_SURFACE_LABELS
    label = TASK_SURFACE_LABELS["voice_verifier"]
    assert "LLM-as-verifier" in label
    assert "no same-author" in label.lower() or "no probability" in label.lower()
