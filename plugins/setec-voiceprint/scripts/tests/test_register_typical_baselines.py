#!/usr/bin/env python3
"""Regression tests for register_typical_baselines.py.

Pins the YAML loader's contract: parses successfully, returns
expected baseline shapes, handles missing registers / signals
gracefully, resolves baselines with the explicit > register
precedence, and surfaces typed errors when the YAML is missing
or malformed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import register_typical_baselines as rtb  # type: ignore


_FIXTURE_YAML = """
register_typical_baselines:
  test_register:
    kicker_density:
      mean: 0.05
      sd: 0.02
      band: [0.02, 0.10]
      provisional: true
      provenance: null
    image_conjunction_per_1000_tokens:
      mean: 3.0
      sd: 1.0
      band: [1.0, 6.0]
      provisional: true
      provenance: null
  another_register:
    kicker_density:
      mean: 0.20
      sd: 0.05
      band: [0.10, 0.30]
      provisional: true
      provenance: null
"""


@pytest.fixture
def fixture_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "fixture.yaml"
    p.write_text(_FIXTURE_YAML, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def clear_loader_cache():
    rtb._load_baselines.cache_clear()
    yield
    rtb._load_baselines.cache_clear()


# ---------- Loader contract ----------


def test_loader_reads_fixture(fixture_yaml: Path):
    baselines = rtb._load_baselines(str(fixture_yaml))
    assert "test_register" in baselines
    assert "another_register" in baselines


def test_loader_raises_typed_error_on_missing_file(tmp_path: Path):
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(rtb.RegisterTypicalBaselineError) as exc:
        rtb._load_baselines(str(missing))
    assert "register_typical.yaml" in str(exc.value)


def test_loader_raises_on_malformed_yaml(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "register_typical_baselines:\n  bad: [unclosed",
        encoding="utf-8",
    )
    with pytest.raises(rtb.RegisterTypicalBaselineError):
        rtb._load_baselines(str(bad))


def test_loader_raises_on_missing_top_level_key(tmp_path: Path):
    bad = tmp_path / "no-key.yaml"
    bad.write_text("some_other_key: {}", encoding="utf-8")
    with pytest.raises(rtb.RegisterTypicalBaselineError) as exc:
        rtb._load_baselines(str(bad))
    assert "register_typical_baselines" in str(exc.value)


# ---------- available_registers ----------


def test_available_registers_fixture(fixture_yaml: Path):
    assert rtb.available_registers(fixture_yaml) == [
        "another_register", "test_register",
    ]


def test_available_registers_shipped_yaml():
    """The shipped baselines/register_typical.yaml has 6 registers."""
    registers = rtb.available_registers()
    assert len(registers) == 6
    assert "contemporary_essay" in registers
    assert "literary_fiction" in registers
    assert "hard_science_fiction" in registers
    assert "academic_prose" in registers
    assert "blog_post" in registers
    assert "technical_documentation" in registers


# ---------- get_baseline + get_baseline_mean ----------


def test_get_baseline_returns_entry(fixture_yaml: Path):
    entry = rtb.get_baseline(
        "test_register", "kicker_density", yaml_path=fixture_yaml,
    )
    assert entry is not None
    assert entry["mean"] == 0.05
    assert entry["sd"] == 0.02
    assert entry["band"] == [0.02, 0.10]
    assert entry["provisional"] is True
    assert entry["provenance"] is None


def test_get_baseline_unknown_register_returns_none(fixture_yaml: Path):
    assert rtb.get_baseline(
        "unknown_register", "kicker_density", yaml_path=fixture_yaml,
    ) is None


def test_get_baseline_unknown_signal_returns_none(fixture_yaml: Path):
    assert rtb.get_baseline(
        "test_register", "unknown_signal", yaml_path=fixture_yaml,
    ) is None


def test_get_baseline_case_insensitive(fixture_yaml: Path):
    """Mixed-case register names match snake_case YAML keys."""
    entry = rtb.get_baseline(
        "Test Register", "kicker_density", yaml_path=fixture_yaml,
    )
    assert entry is not None
    assert entry["mean"] == 0.05


def test_get_baseline_mean_returns_float(fixture_yaml: Path):
    assert rtb.get_baseline_mean(
        "test_register", "kicker_density", yaml_path=fixture_yaml,
    ) == 0.05


def test_get_baseline_mean_unknown_returns_none(fixture_yaml: Path):
    assert rtb.get_baseline_mean(
        "unknown", "kicker_density", yaml_path=fixture_yaml,
    ) is None


# ---------- resolve_baseline precedence ----------


def test_resolve_explicit_wins(fixture_yaml: Path):
    """Explicit value takes precedence over register lookup."""
    result = rtb.resolve_baseline(
        "test_register", "kicker_density",
        explicit_value=0.99,
        explicit_source="my-personal-baseline",
        yaml_path=fixture_yaml,
    )
    assert result["value"] == 0.99
    assert result["source"] == "my-personal-baseline"


def test_resolve_falls_back_to_register(fixture_yaml: Path):
    """No explicit value → register lookup → returns register-typical."""
    result = rtb.resolve_baseline(
        "test_register", "kicker_density",
        yaml_path=fixture_yaml,
    )
    assert result["value"] == 0.05
    assert result["source"] == "register_typical_test_register"


def test_resolve_returns_none_when_both_paths_fail(fixture_yaml: Path):
    """Unknown register + no explicit value → None."""
    result = rtb.resolve_baseline(
        "unknown_register", "kicker_density",
        yaml_path=fixture_yaml,
    )
    assert result is None


def test_resolve_handles_no_register(fixture_yaml: Path):
    """register=None + no explicit value → None."""
    result = rtb.resolve_baseline(
        None, "kicker_density", yaml_path=fixture_yaml,
    )
    assert result is None


def test_resolve_explicit_value_only(fixture_yaml: Path):
    """register=None + explicit value → returns explicit."""
    result = rtb.resolve_baseline(
        None, "kicker_density",
        explicit_value=0.42,
        explicit_source="my-personal-baseline",
        yaml_path=fixture_yaml,
    )
    assert result["value"] == 0.42
    assert result["source"] == "my-personal-baseline"


# ---------- Shipped YAML integration ----------


def test_shipped_yaml_contemporary_essay_kicker_density():
    """Sanity-check the shipped YAML against the spec's starting
    contemporary_essay baseline (kicker_density mean 0.08)."""
    entry = rtb.get_baseline(
        "contemporary_essay", "kicker_density",
    )
    assert entry is not None
    assert entry["mean"] == 0.08
    assert entry["provisional"] is True


def test_shipped_yaml_all_provisional():
    """Every shipped baseline entry must carry provisional=True per
    the Stylometry-to-the-people policy."""
    for register in rtb.available_registers():
        baselines = rtb._load_baselines("")
        for signal_name, entry in baselines[register].items():
            assert entry.get("provisional") is True, (
                f"{register}/{signal_name} is not provisional"
            )
            assert entry.get("provenance") is None, (
                f"{register}/{signal_name} has provenance set "
                f"while shipped provisional"
            )


def test_shipped_yaml_all_signals_present():
    """Every register must carry the four AIC-8/9 signals."""
    expected = {
        "kicker_density",
        "image_conjunction_per_1000_tokens",
        "prestige_metaphor_per_1000_tokens",
        "domain_scatter_entropy",
    }
    baselines = rtb._load_baselines("")
    for register in baselines:
        actual = set(baselines[register].keys())
        missing = expected - actual
        assert not missing, f"{register} missing signals: {missing}"
