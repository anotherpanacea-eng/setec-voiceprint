"""Tests for ``specdetect_audit.py`` (spec 30, M1).

Pins the spec's M1 acceptance criteria + the two folded review P1s:

  * SpecDetect descriptors over known-spectrum injected sequences (sine peak;
    white-ish flat),
  * stdlib real-DFT correctness (Parseval) + numpy parity,
  * Lastde diversity-entropy over known-ordinal sequences (ramp ≈ 0; shuffle
    ≈ max), and the multiscale aggregate,
  * degenerate handling (constant / empty / too-short),
  * the PROVISIONAL band: spectrum-property values, ≥2-of-N vote,
    provisional + calibration_anchor + thresholds echoed,
  * **[P1 posture] no band value names a machine/AI/human class**,
  * no-verdict shape (no is_ai/ai_probability/verdict/composite score),
  * orthogonality source-scan (no DivEye/curvature/Binoculars field),
  * stdlib import (no torch/numpy pulled by importing the module),
  * ClaimLicense + both arXiv citations,
  * **[P1 build] the discrimination_spectral surface is registered** (else
    build_output raises) and the capability fragment + golden are present,
  * CLI model-gated, fails friendly.

No real model loads: a synthetic surprisal (bits) series is injected.
"""

from __future__ import annotations

import ast
import importlib
import json
import math
import random
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_REPO_ROOT = _SCRIPTS.parents[2]
sys.path.insert(0, str(_SCRIPTS))

import specdetect_audit as sd  # noqa: E402


# ============================================================
# Synthetic injected series helpers (bits; score_text returns bits)
# ============================================================


def _sine_logprobs(n=256, freq_bin=8, amp=1.0, offset=-3.0):
    """A log-prob series that is a pure sinusoid of a known frequency. The
    DFT should peak at ``freq_bin``. (We build log-probs directly here; the
    audit() path converts bits→log-probs, but the spectral core takes
    log-probs.)"""
    return [offset + amp * math.sin(2 * math.pi * freq_bin * t / n) for t in range(n)]


def _white_logprobs(n=256, seed=0):
    rng = random.Random(seed)
    return [rng.gauss(-3.0, 1.0) for _ in range(n)]


def _ramp_probs(n=120):
    """Strictly monotone ramp → one dominant ordinal pattern."""
    return [0.001 * t for t in range(n)]


def _shuffle_probs(n=600, seed=3):
    rng = random.Random(seed)
    xs = [rng.random() for _ in range(n)]
    return xs


# ============================================================
# 1. SpecDetect descriptors — known-spectrum fixtures
# ============================================================


def test_sine_peaks_at_known_bin_and_is_peaky():
    desc = sd.spectral_descriptors(_sine_logprobs(n=256, freq_bin=8), use_numpy=False)
    assert desc["degenerate"] is False
    # The required descriptor keys are all present.
    for k in (
        "spectral_centroid",
        "low_freq_energy_frac",
        "spectral_flatness",
        "peak_frequency_bin",
        "peak_magnitude_norm",
        "dominant_period_tokens",
    ):
        assert k in desc
    # Peak at the injected bin.
    assert desc["peak_frequency_bin"] == 8
    # A pure sine concentrates almost all energy in one bin → flatness near 0.
    assert desc["spectral_flatness"] < 0.2
    # dominant period = n / freq_bin = 256/8 = 32 tokens.
    assert desc["dominant_period_tokens"] == pytest.approx(32.0, rel=1e-6)


def test_white_noise_is_flat_no_dominant_peak():
    desc = sd.spectral_descriptors(_white_logprobs(n=256), use_numpy=False)
    assert desc["degenerate"] is False
    # White-ish noise spreads energy → flatness substantially above a sine's.
    assert desc["spectral_flatness"] > 0.4
    # No single bin dominates: peak magnitude is a small fraction of energy.
    assert desc["peak_magnitude_norm"] < 0.2


# ============================================================
# 2. DFT correctness (Parseval) + numpy parity
# ============================================================


def test_parseval_holds_stdlib_dft():
    """Summed spectral energy = mean-removed variance * n (to tolerance).

    For a real series of length n, with the rfft half-spectrum, Parseval is
    sum_t x_t^2 = (1/n) [ |X_0|^2 + |X_{n/2}|^2 + 2 * sum_{k=1}^{n/2-1} |X_k|^2 ].
    We mean-remove first (so sum x^2 = variance * n).
    """
    raw = _white_logprobs(n=128, seed=11)
    mean = sum(raw) / len(raw)
    centered = [x - mean for x in raw]
    n = len(centered)
    mags = sd.rdft_magnitude(centered, use_numpy=False)
    # Reconstruct full-spectrum energy from the half spectrum.
    half = n // 2
    energy = mags[0] ** 2 + (mags[half] ** 2 if n % 2 == 0 else 0.0)
    for k in range(1, half if n % 2 == 0 else half + 1):
        energy += 2 * mags[k] ** 2
    energy /= n
    time_energy = sum(x * x for x in centered)
    assert energy == pytest.approx(time_energy, rel=1e-9)


def test_numpy_parity_when_available():
    np = pytest.importorskip("numpy")  # noqa: F841
    series = _sine_logprobs(n=200, freq_bin=5)
    mean = sum(series) / len(series)
    centered = [x - mean for x in series]
    stdlib_mags = sd.rdft_magnitude(centered, use_numpy=False)
    numpy_mags = sd.rdft_magnitude(centered, use_numpy=True)
    assert len(stdlib_mags) == len(numpy_mags)
    for a, b in zip(stdlib_mags, numpy_mags):
        assert a == pytest.approx(b, abs=1e-6)


# ============================================================
# 3. Lastde diversity-entropy — known-ordinal fixtures
# ============================================================


def test_diversity_entropy_in_unit_interval():
    de = sd.diversity_entropy(_shuffle_probs(n=300), scale=1, order=3)
    assert de is not None
    assert 0.0 <= de <= 1.0


def test_monotone_ramp_low_entropy():
    de = sd.diversity_entropy(_ramp_probs(n=120), scale=1, order=3)
    assert de is not None
    # A strictly increasing ramp → every window is the identity pattern → 0.
    assert de < 1e-9


def test_shuffle_high_entropy():
    de = sd.diversity_entropy(_shuffle_probs(n=2000, seed=7), scale=1, order=3)
    assert de is not None
    # An i.i.d. shuffle → near-uniform over 3! = 6 patterns → near max (1.0).
    assert de > 0.95


def test_lastde_multiscale_shape():
    block = sd.lastde_multiscale(_shuffle_probs(n=500, seed=2))
    assert set(block) >= {"per_scale", "lastde_plus", "scales"}
    assert len(block["per_scale"]) == len(block["scales"])
    assert block["lastde_plus"] is not None
    assert 0.0 <= block["lastde_plus"] <= 1.0


# ============================================================
# 4. Degenerate handling
# ============================================================


def test_constant_series_degenerate():
    desc = sd.spectral_descriptors([-3.0] * 100, use_numpy=False)
    assert desc["degenerate"] is True
    assert desc["spectral_centroid"] is None
    assert "degenerate" in desc["caveats"]


def test_empty_series_degenerate():
    desc = sd.spectral_descriptors([], use_numpy=False)
    assert desc["degenerate"] is True
    assert desc["spectral_centroid"] is None


def test_short_series_flagged_not_spurious():
    desc = sd.spectral_descriptors(_sine_logprobs(n=16, freq_bin=2), use_numpy=False)
    # Below MIN_SERIES_FOR_SPECTRUM but still computable → flagged, not None.
    assert desc["degenerate"] is False
    assert "series_too_short_for_stable_spectrum" in desc["caveats"]


def test_diversity_entropy_none_when_too_short():
    assert sd.diversity_entropy([0.1, 0.2], scale=1, order=3) is None


# ============================================================
# 5. PROVISIONAL band
# ============================================================


def test_band_is_provisional_with_anchor_and_thresholds():
    desc = sd.spectral_descriptors(_sine_logprobs(n=256, freq_bin=2), use_numpy=False)
    band = sd._provisional_band(desc)
    assert band["provisional"] is True
    assert band["calibration_anchor"] == "user-baseline-required"
    assert band["thresholds_used"] == sd.PROVISIONAL_BAND_THRESHOLDS
    assert band["band"] in (
        "flat-spectrum",
        "concentrated-spectrum",
        "indeterminate",
    )


def test_band_is_two_of_n_vote():
    """No single descriptor decides: one concentrated signal alone stays
    indeterminate; two flip it."""
    # Only low_freq high (1 signal), flatness neutral → indeterminate.
    one = sd._provisional_band(
        {"degenerate": False, "low_freq_energy_frac": 0.9, "spectral_flatness": 0.5}
    )
    assert one["band"] == "indeterminate"
    # low_freq high AND flatness low (2 signals) → concentrated-spectrum.
    two = sd._provisional_band(
        {"degenerate": False, "low_freq_energy_frac": 0.9, "spectral_flatness": 0.2}
    )
    assert two["band"] == "concentrated-spectrum"
    # The flat inverse.
    flat = sd._provisional_band(
        {"degenerate": False, "low_freq_energy_frac": 0.1, "spectral_flatness": 0.9}
    )
    assert flat["band"] == "flat-spectrum"


def test_render_prints_band_and_anchor():
    series_bits = [-lp for lp in _sine_logprobs(n=128, freq_bin=4)]
    results = sd.audit("x", series=series_bits)
    env = sd.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    md = sd.render_markdown(env)
    assert "Band (PROVISIONAL)" in md
    assert "user-baseline-required" in md


# ============================================================
# 6. No-verdict shape (structural) + [P1] band names no class
# ============================================================


def test_no_verdict_fields_in_results():
    series_bits = [-lp for lp in _sine_logprobs(n=128, freq_bin=4)]
    results = sd.audit("x", series=series_bits)
    env = sd.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    r = env["results"]
    for forbidden in (
        "is_ai",
        "ai_probability",
        "verdict",
        "verdict_band",
        "score",
        "is_human",
        "ai_likelihood",
    ):
        assert forbidden not in r, f"verdict-shaped field {forbidden!r} leaked"
    # The band exists and is descriptive, not a composite score.
    assert "band" in r
    assert isinstance(r["band"]["band"], str)
    # The module ships no default threshold constant.
    assert not hasattr(sd, "DEFAULT_THRESHOLD_LOW")
    assert not hasattr(sd, "DEFAULT_THRESHOLD_HIGH")
    # Uncalibrated posture surfaced.
    assert "no_calibrated_thresholds_supplied" in r["caveats"]


def test_band_values_name_no_machine_ai_human_class():
    """[P1 posture, folded] Every reachable band value names a SPECTRUM
    PROPERTY, never the inference target — no 'machine' / 'ai' / 'human'."""
    reachable_bands = set()
    # Drive every branch of the band table.
    fixtures = [
        {"degenerate": True},
        {"degenerate": False, "low_freq_energy_frac": 0.9, "spectral_flatness": 0.2},
        {"degenerate": False, "low_freq_energy_frac": 0.1, "spectral_flatness": 0.9},
        {"degenerate": False, "low_freq_energy_frac": 0.5, "spectral_flatness": 0.5},
    ]
    for f in fixtures:
        reachable_bands.add(sd._provisional_band(f)["band"])
    assert reachable_bands == {
        "indeterminate",
        "concentrated-spectrum",
        "flat-spectrum",
    }
    for b in reachable_bands:
        low = b.lower()
        assert "machine" not in low
        assert "ai" not in low.split("-")  # token-level: 'ai' is never a band token
        assert "human" not in low


def test_render_never_concludes_ai_or_human():
    # Degenerate path renders an explicit "unavailable", never "human".
    results = sd.audit("x", series=[-3.0] * 100)
    env = sd.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    md = sd.render_markdown(env).lower()
    assert "ai-generated" not in md
    assert "human-written" not in md
    assert "unavailable" in md


# ============================================================
# 7. Orthogonality guard (structural source scan)
# ============================================================


def _strip_comments_and_strings(source: str) -> str:
    """Remove docstrings/string literals and comments so the scan tests the
    CODE, not the posture documentation (the voicewright separation-guard /
    curvature test_orthogonal_statistic precedent)."""
    tree = ast.parse(source)
    string_spans = []

    class _V(ast.NodeVisitor):
        def visit_Constant(self, node):  # noqa: N802
            if isinstance(node.value, str) and hasattr(node, "end_lineno"):
                string_spans.append(
                    (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)
                )
            self.generic_visit(node)

    _V().visit(tree)
    lines = source.splitlines()
    # Blank out string spans.
    for (sl, sc, el, ec) in string_spans:
        if sl == el:
            line = lines[sl - 1]
            lines[sl - 1] = line[:sc] + " " * (ec - sc) + line[ec:]
        else:
            lines[sl - 1] = lines[sl - 1][:sc]
            for i in range(sl, el - 1):
                lines[i] = ""
            lines[el - 1] = lines[el - 1][ec:]
    # Drop comments.
    out = []
    for line in lines:
        hash_idx = line.find("#")
        out.append(line if hash_idx < 0 else line[:hash_idx])
    return "\n".join(out)


def test_orthogonal_statistic():
    source = (sd.__file__ and Path(sd.__file__).read_text(encoding="utf-8"))
    code = _strip_comments_and_strings(source)
    for forbidden in (
        "mean_surprisal",
        "sd_surprisal",
        "autocorrelation",
        "lag_",
        "curvature",
        "perplexity_ratio",
        "cross_perplexity",
        "binoculars",
    ):
        assert forbidden not in code, (
            f"orthogonality leak: {forbidden!r} referenced in spectral CODE "
            f"(docstrings/comments are stripped before this scan)"
        )


# ============================================================
# 8. Stdlib import (no torch / no numpy pulled by import)
# ============================================================


def test_import_pulls_no_torch_no_numpy():
    """Importing the module in a fresh subprocess must not import torch or
    numpy (numpy is only an optional fast path chosen at call time)."""
    script = (
        "import sys; import specdetect_audit; "
        "assert 'torch' not in sys.modules, 'torch imported'; "
        "assert 'numpy' not in sys.modules, 'numpy imported'; "
        "print('clean')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_SCRIPTS),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "clean" in proc.stdout


# ============================================================
# 9. ClaimLicense + citations
# ============================================================


def test_claim_license_refuses_verdict_and_cites_both_papers():
    series_bits = [-lp for lp in _sine_logprobs(n=128, freq_bin=4)]
    results = sd.audit("x", series=series_bits)
    env = sd.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=300, results=results,
    )
    cl = env["claim_license"]
    assert cl["task_surface"] == "discrimination_spectral"

    lic = cl["licenses"].lower()
    assert "spectral" in lic
    assert "not a verdict" in lic
    assert "orthogonal" in lic

    dnl = cl["does_not_license"].lower()
    assert "verdict" in dnl
    assert "threshold" in dnl
    assert "absence is not evidence" in dnl

    # Both arXiv roots, by title + id.
    refs = " ".join(cl["references"])
    assert "2508.11343" in refs and "SpecDetect" in refs
    assert "2410.06072" in refs and "Lastde" in refs

    # Surface registered in the label map (so the rendered block isn't a raw
    # key fallback).
    import claim_license as cl_mod
    assert "discrimination_spectral" in cl_mod.TASK_SURFACE_LABELS


# ============================================================
# [P1 build] surface registered; build_output would raise without it
# ============================================================


def test_surface_is_registered_in_valid_task_surfaces():
    from output_schema import VALID_TASK_SURFACES  # type: ignore

    assert sd.TASK_SURFACE in VALID_TASK_SURFACES
    # And the fragment file is the source of truth.
    frag = _SCRIPTS / "claim_license_surfaces" / "discrimination_spectral.txt"
    assert frag.exists()


def test_envelope_builds_with_registered_surface():
    """End-to-end: audit() → compose_envelope() builds a valid schema-1.0
    envelope (this is exactly the path that hard-raised before the fragment
    was added)."""
    series_bits = [-lp for lp in _sine_logprobs(n=128, freq_bin=4)]
    results = sd.audit("x", series=series_bits)
    env = sd.compose_envelope(
        target_path=Path("/tmp/x.txt"), target_words=200, results=results,
    )
    assert env["schema_version"] == "1.0"
    assert env["task_surface"] == "discrimination_spectral"
    assert env["tool"] == "specdetect_audit"
    assert env["available"] is True


def test_lastde_not_wired_into_surface_output():
    """M1 ships SpecDetect only — the Lastde block must NOT appear in the
    surface results (gated to M2)."""
    series_bits = [-lp for lp in _sine_logprobs(n=128, freq_bin=4)]
    results = sd.audit("x", series=series_bits)
    assert "lastde" not in results
    assert "diversity_entropy" not in results
    assert "lastde_block_gated_to_m2_orthogonality_check" in results["caveats"]


# ============================================================
# 10. Capability fragment + golden + drift
# ============================================================


def test_capability_entry_and_golden_present():
    tools_dir = _REPO_ROOT / "tools"
    sys.path.insert(0, str(tools_dir))
    import check_capabilities_drift as drift  # type: ignore

    report = drift.check_drift()
    assert report.passed, (
        "capabilities drift detected:\n"
        + "\n".join(v.render() for v in report.violations)
    )

    manifest = drift.load_manifest(drift.DEFAULT_MANIFEST)
    entry = next(
        (e for e in manifest["entries"] if e.get("id") == "specdetect_audit"),
        None,
    )
    assert entry is not None, "specdetect_audit missing from capabilities.d"
    assert entry["surface"] == "discrimination_spectral"
    assert entry["status"] == "literature_anchored"
    assert entry["compute"]["tier"] == "surprisal"

    # Per-id golden fragment exists and equals the loaded entry.
    golden = _HERE / "_golden_capabilities" / "specdetect_audit.json"
    assert golden.exists()
    assert json.loads(golden.read_text(encoding="utf-8")) == entry


# ============================================================
# 11. CLI is model-gated, fails friendly
# ============================================================


def test_cli_returns_nonzero_on_missing_target(tmp_path):
    rc = sd.main([str(tmp_path / "nonexistent.txt")])
    assert rc == 1


def test_cli_missing_torch_graceful(monkeypatch, tmp_path, capsys):
    target = tmp_path / "target.txt"
    target.write_text("the cat sat on the mat " * 50, encoding="utf-8")

    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def fake_import(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("No module named 'torch'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    rc = sd.main([str(target)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "surprisal tier" in err
    assert "pip install" in err
    assert "Traceback" not in err


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
