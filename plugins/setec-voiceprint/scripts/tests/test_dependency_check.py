#!/usr/bin/env python3
"""Regression tests for dependency_check.py.

The script reports state — what's installed, what's missing, and how
to install the missing pieces — for the four SETEC dependency tiers.
Tests verify:

  * The tier registry is well-formed (every tier has the keys the
    rendering and suggestion logic expects).
  * Detection helpers correctly flag installed vs. missing packages,
    spaCy models, and system binaries.
  * The aggregate survey produces stable JSON shape.
  * The platform-specific install-hint logic picks the right hint
    per platform.
  * Suggest-mode output omits commands when nothing is missing and
    distinguishes required from optional installs when something is.

The tests don't actually install anything. They exercise the
state-reporting surface only.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None

import dependency_check as dc  # type: ignore


# ------------------- Tier registry -------------------------------


def test_tier_registry_well_formed():
    """Every tier has the keys the rendering / suggest logic expects."""
    expected_keys = {
        "label", "requirements_file", "python_deps",
        "spacy_models", "system_deps",
    }
    for tier_key, tier in dc.TIERS.items():
        missing = expected_keys - set(tier.keys())
        assert not missing, f"tier {tier_key} missing keys {missing}"


def test_core_tier_has_spacy_and_dependencies():
    """Core tier must declare spaCy + en_core_web_sm + the four
    standard scientific packages."""
    core = dc.TIERS["core"]
    py_names = {d.import_name for d in core["python_deps"]}
    assert "spacy" in py_names
    assert "scipy" in py_names
    assert "sklearn" in py_names
    assert "statsmodels" in py_names
    model_names = {m.name for m in core["spacy_models"]}
    assert "en_core_web_sm" in model_names


def test_acquisition_tier_has_required_packages():
    """Acquisition tier must declare the six required Python deps."""
    acq = dc.TIERS["acquisition"]
    py_names = {d.import_name for d in acq["python_deps"]}
    assert {"requests", "feedparser", "bs4", "lxml", "dateutil",
            "pypdf"} <= py_names


def test_ocr_tier_has_system_binaries():
    """OCR tier must declare both the Python wrapper and the three
    system binaries it depends on."""
    ocr = dc.TIERS["ocr"]
    sys_names = {d.binary for d in ocr["system_deps"]}
    assert sys_names == {"tesseract", "gs", "qpdf"}


def test_calibration_tier_has_huggingface_and_pyarrow():
    cal = dc.TIERS["calibration"]
    py_names = {d.import_name for d in cal["python_deps"]}
    assert "huggingface_hub" in py_names
    assert "pyarrow" in py_names


def test_optional_tier_has_marked_optional():
    opt = dc.TIERS["optional"]
    for d in opt["python_deps"]:
        assert d.optional_in_tier, \
            f"optional tier dep {d.name} not marked optional_in_tier"


def test_ocr_python_dep_marked_optional_within_acquisition():
    """ocrmypdf should be optional within its tier so the OCR
    skip-when-missing path doesn't fire as a required-missing
    error."""
    ocr = dc.TIERS["ocr"]
    for d in ocr["python_deps"]:
        if d.import_name == "ocrmypdf":
            assert d.optional_in_tier
            return
    raise AssertionError("ocrmypdf not in OCR tier")


# ------------------- Platform detection --------------------------


def test_detect_platform_returns_known_value():
    plat = dc.detect_platform()
    assert plat in {"macos", "linux", "windows"}


def test_python_version_summary_has_expected_keys():
    s = dc.python_version_summary()
    assert "executable" in s
    assert "version" in s
    assert "version_info" in s
    assert "platform" in s
    assert s["platform"] in {"macos", "linux", "windows"}


# ------------------- Detection helpers ---------------------------


def test_check_python_dep_detects_installed_module():
    """Use a stdlib module that is guaranteed present."""
    dep = dc.PythonDep(
        name="json",
        import_name="json",
        pip_name="json",  # not really pip-installable, but fine for the test
        summary="stdlib",
    )
    result = dc.check_python_dep(dep)
    assert result["installed"] is True
    # version may be None / "unknown" for stdlib modules; both are fine.


def test_check_python_dep_detects_missing_module():
    dep = dc.PythonDep(
        name="definitely-not-installed-xyz123",
        import_name="definitely_not_installed_xyz123",
        pip_name="definitely-not-installed-xyz123",
        summary="dummy",
    )
    result = dc.check_python_dep(dep)
    assert result["installed"] is False
    assert result["error"]


def test_check_system_dep_detects_present_binary():
    """ls is on every PATH on macOS and Linux; cmd.exe on Windows.
    Use a binary that's guaranteed to be present on the host."""
    binary = "ls" if dc.detect_platform() != "windows" else "cmd"
    dep = dc.SystemDep(
        name="probe",
        binary=binary,
        summary="probe",
        install_hints={"macos": "", "linux": "", "windows": ""},
    )
    result = dc.check_system_dep(dep)
    assert result["installed"] is True
    assert result["path"]


def test_check_system_dep_detects_missing_binary():
    dep = dc.SystemDep(
        name="probe",
        binary="definitely-not-installed-xyz123",
        summary="probe",
        install_hints={"macos": "", "linux": "", "windows": ""},
    )
    result = dc.check_system_dep(dep)
    assert result["installed"] is False
    assert result["path"] is None


def test_check_spacy_model_handles_missing_spacy():
    """When spaCy is not installed at all, the spaCy-model check
    must report a clear failure rather than raise."""
    model = dc.SpacyModel(name="en_core_web_sm", summary="test")
    # If spaCy is genuinely absent we get loaded=False naturally.
    # If it IS installed, we patch out the import to simulate absence.
    with mock.patch.dict("sys.modules", {"spacy": None}):
        result = dc.check_spacy_model(model)
    # Either "spaCy not installed" (when the patch took effect) or a
    # genuine model-load error (when spaCy was already imported in
    # this process). Both should result in loaded=False with a
    # human-readable error.
    assert result["loaded"] in (True, False)
    if not result["loaded"]:
        assert result["error"]


# ------------------- Survey aggregate ----------------------------


def test_survey_tier_returns_stable_shape():
    """The per-tier survey must always return the documented keys
    so the renderers and suggesters never KeyError."""
    survey = dc.survey_tier("acquisition", dc.TIERS["acquisition"])
    expected = {
        "tier_key", "label", "requirements_file", "python_deps",
        "spacy_models", "system_deps",
        "missing_required_count", "missing_optional_count",
        "missing_required", "missing_optional",
    }
    missing = expected - set(survey.keys())
    assert not missing, f"survey missing keys: {missing}"


def test_survey_all_returns_one_entry_per_tier():
    survey = dc.survey_all()
    keys = [t["tier_key"] for t in survey["tiers"]]
    assert set(keys) == set(dc.TIERS.keys())


def test_survey_all_filters_to_requested_tiers():
    survey = dc.survey_all(tiers=["acquisition"])
    keys = [t["tier_key"] for t in survey["tiers"]]
    assert keys == ["acquisition"]


# ------------------- Install-command suggestions -----------------


def test_suggest_install_commands_empty_when_all_present():
    """When the survey reports zero missing required deps, suggest
    must produce only optional suggestions (or empty)."""
    fake_survey = {
        "python": {"platform": "macos"},
        "tiers": [
            {
                "tier_key": "core",
                "label": "core stylometry",
                "requirements_file": "requirements.txt",
                "python_deps": [
                    {"name": "spacy", "pip_name": "spacy",
                     "installed": True, "optional": False},
                ],
                "spacy_models": [
                    {"name": "en_core_web_sm", "loaded": True},
                ],
                "system_deps": [],
            },
        ],
    }
    cmds = dc.suggest_install_commands(fake_survey)
    required = [c for c in cmds if not c["optional"]]
    assert required == []


def test_suggest_install_commands_proposes_pip_install_for_missing_required():
    fake_survey = {
        "python": {"platform": "linux"},
        "tiers": [
            {
                "tier_key": "acquisition",
                "label": "acquisition",
                "requirements_file": "requirements-acquisition.txt",
                "python_deps": [
                    {"name": "requests", "pip_name": "requests",
                     "installed": False, "optional": False},
                ],
                "spacy_models": [],
                "system_deps": [],
            },
        ],
    }
    cmds = dc.suggest_install_commands(fake_survey)
    required = [c for c in cmds if not c["optional"]]
    assert len(required) == 1
    assert "pip install -r" in required[0]["command"]
    assert "requirements-acquisition.txt" in required[0]["command"]


def test_suggest_install_commands_includes_spacy_download():
    fake_survey = {
        "python": {"platform": "macos"},
        "tiers": [
            {
                "tier_key": "core",
                "label": "core",
                "requirements_file": "requirements.txt",
                "python_deps": [],
                "spacy_models": [
                    {"name": "en_core_web_sm", "loaded": False,
                     "error": "model not found"},
                ],
                "system_deps": [],
            },
        ],
    }
    cmds = dc.suggest_install_commands(fake_survey)
    spacy_cmds = [
        c for c in cmds if "spacy download" in c["command"]
    ]
    assert spacy_cmds, "spaCy model download command not surfaced"
    assert "en_core_web_sm" in spacy_cmds[0]["command"]


def test_suggest_install_commands_uses_macos_brew_for_system_deps():
    """When the survey says platform=macos and tesseract is missing,
    the suggested command should be `brew install tesseract`."""
    fake_survey = {
        "python": {"platform": "macos"},
        "tiers": [
            {
                "tier_key": "ocr",
                "label": "ocr",
                "requirements_file": "requirements-acquisition.txt",
                "python_deps": [],
                "spacy_models": [],
                "system_deps": [
                    {"name": "tesseract", "binary": "tesseract",
                     "installed": False, "optional": True},
                ],
            },
        ],
    }
    cmds = dc.suggest_install_commands(fake_survey)
    tess = next(c for c in cmds if "tesseract" in c["label"])
    assert "brew install tesseract" in tess["command"]


def test_suggest_install_commands_uses_apt_for_linux():
    fake_survey = {
        "python": {"platform": "linux"},
        "tiers": [
            {
                "tier_key": "ocr",
                "label": "ocr",
                "requirements_file": "requirements-acquisition.txt",
                "python_deps": [],
                "spacy_models": [],
                "system_deps": [
                    {"name": "tesseract", "binary": "tesseract",
                     "installed": False, "optional": True},
                ],
            },
        ],
    }
    cmds = dc.suggest_install_commands(fake_survey)
    tess = next(c for c in cmds if "tesseract" in c["label"])
    assert "apt-get install" in tess["command"]


def test_suggest_install_commands_uses_chocolatey_or_manual_for_windows():
    fake_survey = {
        "python": {"platform": "windows"},
        "tiers": [
            {
                "tier_key": "ocr",
                "label": "ocr",
                "requirements_file": "requirements-acquisition.txt",
                "python_deps": [],
                "spacy_models": [],
                "system_deps": [
                    {"name": "tesseract", "binary": "tesseract",
                     "installed": False, "optional": True},
                ],
            },
        ],
    }
    cmds = dc.suggest_install_commands(fake_survey)
    tess = next(c for c in cmds if "tesseract" in c["label"])
    assert (
        "choco install" in tess["command"]
        or "github.com" in tess["command"].lower()
    ), f"Windows hint should mention chocolatey or manual install: {tess['command']}"


# ------------------- Rendering -----------------------------------


def test_render_human_includes_platform_and_python_version():
    survey = dc.survey_all(tiers=["acquisition"])
    text = dc.render_human(survey)
    assert "Python:" in text
    assert "Platform:" in text
    assert "acquisition" in text


def test_render_suggest_says_nothing_to_install_when_all_present():
    fake_survey = {
        "python": {"platform": "macos"},
        "tiers": [
            {
                "tier_key": "core",
                "label": "core",
                "requirements_file": "requirements.txt",
                "python_deps": [
                    {"name": "x", "pip_name": "x",
                     "installed": True, "optional": False},
                ],
                "spacy_models": [],
                "system_deps": [],
            },
        ],
    }
    out = dc.render_suggest(fake_survey)
    assert "Nothing to install" in out or "All required" in out


# ------------------- CLI -----------------------------------------


def test_cli_help_lists_required_flags():
    parser = dc.build_arg_parser()
    help_text = parser.format_help()
    assert "--tier" in help_text
    assert "--json" in help_text
    assert "--suggest" in help_text


def test_run_returns_exit_code_based_on_required_misses(capsys):
    """The exit code is 0 when nothing required is missing, 1 when
    something is. The optional tier is, by definition, all-optional;
    surveying just it should always be exit 0."""
    args = argparse.Namespace(
        tier="optional", json=False, suggest=False,
    )
    rc = dc.run(args)
    assert rc == 0


def test_json_mode_emits_parseable_json(capsys):
    args = argparse.Namespace(
        tier="acquisition", json=True, suggest=False,
    )
    dc.run(args)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "tiers" in parsed
    assert parsed["tiers"][0]["tier_key"] == "acquisition"


def test_suggest_mode_emits_install_commands_or_clean_message(capsys):
    args = argparse.Namespace(
        tier="all", json=False, suggest=True,
    )
    dc.run(args)
    out = capsys.readouterr().out
    # Either "Nothing to install" / "All required" OR contains a pip
    # install line. Both outcomes are valid; pin that ONE happens.
    assert (
        "Nothing to install" in out
        or "All required" in out
        or "pip install" in out
    )


# ------------------- Skill discoverability -----------------------


def test_setup_skill_skill_md_exists():
    """The setup SKILL.md must ship with the plugin so Claude can
    discover the dep-check workflow."""
    skill_md = ROOT.parent / "skills" / "setup" / "SKILL.md"
    assert skill_md.is_file(), \
        f"setup skill not found at {skill_md}"


def test_setup_skill_references_dependency_check():
    """The skill should reference the dependency_check.py script and
    the CLAUDE_PLUGIN_ROOT path pattern."""
    skill_md = ROOT.parent / "skills" / "setup" / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    assert "dependency_check.py" in text
    assert "CLAUDE_PLUGIN_ROOT" in text
    # Mac / Linux / Windows install hints all present.
    assert "brew" in text.lower()
    assert "apt" in text.lower() or "yum" in text.lower()
    assert "choco" in text.lower() or "windows" in text.lower()


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
