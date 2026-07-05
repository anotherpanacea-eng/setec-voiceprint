#!/usr/bin/env python3
"""dependency_check.py — survey the SETEC framework's runtime deps.

Walks the five dependency tiers SETEC uses and reports what's
present, what's missing, and how to install it. Designed for the
`setup` skill to invoke before the first real task: a fresh
SETEC install often runs into "module not found" deep inside a
diagnostic pipeline; this script catches the gap up front.

Tiers:

  * **Core stylometry.** Required for every diagnostic script.
    Lives in `requirements.txt`. Includes spaCy + the
    en_core_web_sm model, scipy, scikit-learn, statsmodels.

  * **Acquisition.** Required only for impostor-pool acquisition
    (`acquire_blog.py`, `acquire_blogger_takeout.py`,
    `acquire_magazine.py`, `pdf_inventory.py`, `pdf_extract.py`).
    Lives in `requirements-acquisition.txt`. Includes requests,
    feedparser, beautifulsoup4, lxml, python-dateutil, pypdf.
    Optional within this tier: ocrmypdf + system binaries
    (tesseract, ghostscript, qpdf) for image-only PDFs.

  * **Calibration.** Required only when re-deriving thresholds
    from EditLens or RAID/MAGE. Lives in
    `requirements-calibration.txt`. Includes huggingface_hub,
    pyarrow.

  * **Surprisal (Tier 4 + Binoculars).** Required for Tier 4
    surprisal signals in ``variance_audit.py`` + ``surprisal_audit.py``
    and for the Binoculars two-model perplexity audit
    (``binoculars_audit.py`` + ``binoculars_calibrate.py``). Lives in
    `requirements-surprisal.txt`. Includes transformers, tokenizers,
    torch. Substantial install footprint (~1.5-2 GB with torch's
    CUDA wheels); the setup skill should surface the size cost
    before installing. The external-mirror tools under
    ``external_mirror/`` do NOT live on this stack — their default
    sbert distance metric uses ``sentence-transformers`` (from the
    optional tier; torch comes in transitively), and the v2 metric
    stack uses sklearn / spaCy (from the core tier) plus stdlib.

  * **Optional power-ups.** sentence-transformers (Tier 3 cohesion
    via SBERT instead of TF-IDF; AND the default sbert distance
    path in ``external_mirror/compute_distances.py`` — required
    for external-mirror's default metric, not optional there),
    textstat (better FKGL), nltk (Brown corpus for
    idiolect_detector). All commented in requirements.txt;
    install only on demand.

Usage:

    # Just survey what's installed:
    python3 scripts/dependency_check.py

    # JSON output for the skill to parse:
    python3 scripts/dependency_check.py --json

    # Print platform-appropriate install commands for what's missing:
    python3 scripts/dependency_check.py --suggest

    # Survey only one tier:
    python3 scripts/dependency_check.py --tier acquisition

This script reports state. It does not install anything. The setup
skill reads this output, asks the user for permission, and then
runs the install commands itself — keeping install authority with
the user, not the script.
"""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent

TASK_SURFACE = "setup"
TOOL_NAME = "dependency_check"
SCRIPT_VERSION = "1.0"


# --------------- Platform helpers --------------------------------


def detect_platform() -> str:
    """Return ``"macos"``, ``"linux"``, or ``"windows"``.

    Used to format install commands the user will copy/paste. macOS
    uses Homebrew for system binaries; Linux assumes apt/yum; Windows
    points at chocolatey or scoop or manual installers.
    """
    sys_p = platform.system().lower()
    if sys_p == "darwin":
        return "macos"
    if sys_p == "windows":
        return "windows"
    return "linux"


def python_version_summary() -> dict[str, Any]:
    return {
        "executable": sys.executable,
        "version": sys.version.split()[0],
        "version_info": {
            "major": sys.version_info.major,
            "minor": sys.version_info.minor,
            "micro": sys.version_info.micro,
        },
        "implementation": platform.python_implementation(),
        "platform": detect_platform(),
        "platform_release": platform.release(),
    }


# --------------- Dependency declarations ------------------------


@dataclass
class PythonDep:
    """One Python package the framework knows about.

    Tracks the import name (used for the runtime check), the pip
    package name (used in install commands; usually the same), and
    a one-line summary of what it enables. Optional within its tier
    means the script can run without it; the tier checker reports
    optional misses as info, not warnings.
    """
    name: str
    import_name: str
    pip_name: str
    summary: str
    optional_in_tier: bool = False


@dataclass
class SystemDep:
    """One non-Python binary the framework calls.

    Detected via ``shutil.which``. Each system dep carries a per-
    platform install hint (Homebrew on macOS, apt/yum on Linux,
    chocolatey or manual on Windows).
    """
    name: str
    binary: str
    summary: str
    install_hints: dict[str, str]
    optional_in_tier: bool = False


@dataclass
class SpacyModel:
    """A spaCy model is downloaded post-install via the
    ``python -m spacy download`` command, not pip-installed
    directly. Treated separately so its install command is
    surfaced correctly even when the spaCy package itself is
    already installed."""
    name: str  # e.g. "en_core_web_sm"
    summary: str


# Tier 1: core stylometry. Required for every diagnostic.
CORE_PYTHON_DEPS = [
    PythonDep(
        name="spaCy",
        import_name="spacy",
        pip_name="spacy",
        summary=(
            "Tier 2 of variance_audit.py (POS-bigram entropy + KL/JSD), "
            "POS-trigram and dependency-label n-gram families in "
            "stylometry_core.py."
        ),
    ),
    PythonDep(
        name="SciPy",
        import_name="scipy",
        pip_name="scipy",
        summary=(
            "Length-matched bootstrap intervals; pulled in by scikit-learn."
        ),
    ),
    PythonDep(
        name="scikit-learn",
        import_name="sklearn",
        pip_name="scikit-learn",
        summary=(
            "Tier 3 cohesion fallback (TF-IDF) and validation-harness "
            "primitives (ROC AUC, precision_recall_curve, confusion_matrix)."
        ),
    ),
    PythonDep(
        name="statsmodels",
        import_name="statsmodels",
        pip_name="statsmodels",
        summary=(
            "Confidence intervals for proportions in the validation "
            "harness (Wilson, Agresti-Coull, Clopper-Pearson, Jeffreys)."
        ),
    ),
]

CORE_SPACY_MODELS = [
    SpacyModel(
        name="en_core_web_sm",
        summary=(
            "Default English spaCy model. Required for Tier 2 of "
            "variance_audit.py and the POS / dependency feature families."
        ),
    ),
]

# Tier 2: acquisition.
ACQUISITION_PYTHON_DEPS = [
    PythonDep(
        name="requests",
        import_name="requests",
        pip_name="requests",
        summary="HTTP client for live blog / magazine acquisition.",
    ),
    PythonDep(
        name="feedparser",
        import_name="feedparser",
        pip_name="feedparser",
        summary="RSS / Atom feed parsing for Substack and WordPress / Ghost.",
    ),
    PythonDep(
        name="beautifulsoup4",
        import_name="bs4",
        pip_name="beautifulsoup4",
        summary="HTML parsing for blog and magazine post bodies.",
    ),
    PythonDep(
        name="lxml",
        import_name="lxml",
        pip_name="lxml",
        summary=(
            "Production HTML parser used by BeautifulSoup; faster and "
            "more tolerant than the stdlib fallback."
        ),
    ),
    PythonDep(
        name="python-dateutil",
        import_name="dateutil",
        pip_name="python-dateutil",
        summary="Date parsing across feed and HTML formats.",
    ),
    PythonDep(
        name="trafilatura",
        import_name="trafilatura",
        pip_name="trafilatura",
        summary=(
            "Primary main-content HTML extractor "
            "(acquisition_core.extract_main_content): readability "
            "heuristics strip boilerplate/nav/comments, replacing per-site "
            "selector tuning. Optional within this tier — extraction "
            "fail-softs to the BeautifulSoup path (html_to_text) when "
            "absent, so acquisition still runs without it."
        ),
        optional_in_tier=True,
    ),
    PythonDep(
        name="pypdf",
        import_name="pypdf",
        pip_name="pypdf",
        summary=(
            "PDF inventory and text-layer extraction for "
            "pdf_inventory.py / pdf_extract.py."
        ),
    ),
    PythonDep(
        name="datasketch",
        import_name="datasketch",
        pip_name="datasketch",
        summary=(
            "MinHash-LSH near-duplicate dedup for the staged acquisition "
            "manifest (near_dup_dedup.py) — removes near-identical reposts / "
            "reprints the exact SHA-256 guard misses. Optional within this "
            "tier: the pass is opt-in and lazy-imports datasketch, so "
            "acquisition still runs without it. Pulls numpy/scipy."
        ),
        optional_in_tier=True,
    ),
]

# Tier 2 optional: OCR layer for image-only PDFs.
OCR_PYTHON_DEPS = [
    PythonDep(
        name="ocrmypdf",
        import_name="ocrmypdf",
        pip_name="ocrmypdf",
        summary=(
            "OCR wrapper around tesseract. Optional within "
            "acquisition tier; needed only for image-only / mixed PDFs."
        ),
        optional_in_tier=True,
    ),
]

OCR_SYSTEM_DEPS = [
    SystemDep(
        name="tesseract",
        binary="tesseract",
        summary="OCR engine that ocrmypdf wraps.",
        install_hints={
            "macos": "brew install tesseract",
            "linux": "sudo apt-get install tesseract-ocr  # or yum install tesseract",
            "windows": (
                "Download from https://github.com/UB-Mannheim/tesseract/wiki "
                "or `choco install tesseract` (chocolatey)."
            ),
        },
        optional_in_tier=True,
    ),
    SystemDep(
        name="ghostscript",
        binary="gs",
        summary="PDF rasterizer ocrmypdf depends on.",
        install_hints={
            "macos": "brew install ghostscript",
            "linux": "sudo apt-get install ghostscript",
            "windows": (
                "Download from https://www.ghostscript.com/releases/ "
                "or `choco install ghostscript`."
            ),
        },
        optional_in_tier=True,
    ),
    SystemDep(
        name="qpdf",
        binary="qpdf",
        summary="PDF transformer ocrmypdf depends on.",
        install_hints={
            "macos": "brew install qpdf",
            "linux": "sudo apt-get install qpdf",
            "windows": (
                "Download from https://qpdf.sourceforge.io/ or "
                "`choco install qpdf`."
            ),
        },
        optional_in_tier=True,
    ),
]

# Tier 3: calibration. Both calibration deps are optional within the
# tier — fetch_pangram_editlens_github.py downloads the same corpus
# from the public GitHub mirror with stdlib only (no HF token, no
# parquet). Users who pick the GitHub path don't need huggingface_hub
# or pyarrow.
CALIBRATION_PYTHON_DEPS = [
    PythonDep(
        name="huggingface_hub",
        import_name="huggingface_hub",
        pip_name="huggingface_hub",
        summary=(
            "HuggingFace dataset download for fetch_pangram_editlens.py "
            "(license-gated path; the GitHub mirror at "
            "fetch_pangram_editlens_github.py is stdlib-only)."
        ),
        optional_in_tier=True,
    ),
    PythonDep(
        name="pyarrow",
        import_name="pyarrow",
        pip_name="pyarrow",
        summary=(
            "Parquet read for HuggingFace dataset payloads. Not needed "
            "for the GitHub-fetcher path (CSV-only)."
        ),
        optional_in_tier=True,
    ),
]

# Tier 4 + Binoculars: shared transformers + torch stack. Tier 4
# surprisal (variance_audit.py --tier4 + surprisal_audit.py) loads
# causal LMs through surprisal_backend.py; Binoculars uses the same
# backend for its scorer + observer pair (binoculars_audit.py +
# binoculars_calibrate.py). Substantial install footprint (~1.5-2 GB
# with torch's CUDA wheels on most platforms); the setup skill
# surfaces the size cost before installing.
#
# The external-mirror tools (external_mirror/) deliberately do NOT
# live on this stack — their default sbert distance metric uses
# sentence-transformers (from the optional tier; torch comes in
# transitively), and the v2 metric stack uses sklearn + spaCy from
# the core tier. Operators installing this tier for Binoculars only
# do not get external-mirror's default sbert metric; that needs
# sentence-transformers from the optional tier.
SURPRISAL_PYTHON_DEPS = [
    PythonDep(
        name="transformers",
        import_name="transformers",
        pip_name="transformers",
        summary=(
            "HuggingFace transformers — loads causal LM scorers for "
            "Tier 4 surprisal (variance_audit.py --tier4, "
            "surprisal_audit.py) and the Binoculars scorer + observer "
            "pair (binoculars_audit.py)."
        ),
    ),
    PythonDep(
        name="tokenizers",
        import_name="tokenizers",
        pip_name="tokenizers",
        summary=(
            "HuggingFace tokenizers — usually pulled in by transformers, "
            "but pinned here so the version is explicit. The Binoculars "
            "v2 cross-perplexity path requires the scorer + observer to "
            "share a tokenizer; tokenizer-compat detection lives in "
            "binoculars_audit.py."
        ),
    ),
    PythonDep(
        name="torch",
        import_name="torch",
        pip_name="torch",
        summary=(
            "PyTorch — backend for transformers. CPU wheels suffice "
            "for the framework's default tinyllama + gpt2 pair. CUDA / "
            "ROCm / MPS wheels speed up Tier 4 and Binoculars on a "
            "discrete GPU but aren't required."
        ),
    ),
]

# Tier 5: optional power-ups across all tiers.
OPTIONAL_PYTHON_DEPS = [
    PythonDep(
        name="sentence-transformers",
        import_name="sentence_transformers",
        pip_name="sentence-transformers",
        summary=(
            "Calibrated sentence embeddings. Two consumers: (a) Tier 3 "
            "cohesion (SBERT instead of scikit-learn TF-IDF; cosines "
            "comparable to literature reference values; this is "
            "OPTIONAL for Tier 3 — TF-IDF works as a fallback). (b) the "
            "default sbert distance metric in "
            "external_mirror/compute_distances.py (Surface 5; "
            "REQUIRED for external-mirror's default metric — the v2 "
            "metric stack of TF-IDF + POS-bigram + word-set Jaccard "
            "can run without sentence-transformers, but the default "
            "sbert path cannot). Pulls in torch (~2 GB)."
        ),
        optional_in_tier=True,
    ),
    PythonDep(
        name="textstat",
        import_name="textstat",
        pip_name="textstat",
        summary=(
            "More accurate FKGL than the stdlib syllable approximation "
            "in variance_audit.py."
        ),
        optional_in_tier=True,
    ),
    PythonDep(
        name="NLTK",
        import_name="nltk",
        pip_name="nltk",
        summary=(
            "Optional: nltk.tokenize.sent_tokenize fallback "
            "(variance_audit.py) and Brown corpus for "
            "idiolect_detector.py --reference-corpus brown."
        ),
        optional_in_tier=True,
    ),
    # style_embedding note: voice_fingerprint.py (Surface
    # authorship_embedding) loads a FROZEN STYLE ENCODER that is a
    # DISTINCT download from the Tier-4 causal-LM scorers. The default
    # LUAR encoder (rrivera1849/LUAR-MUD, Apache-2.0) loads through
    # `transformers` directly with trust_remote_code=True (~0.5 GB);
    # the optional Wegmann cross-check (AnnaWegmann/Style-Embedding,
    # ~0.4 GB) loads through sentence-transformers. transformers
    # itself lives on the surprisal tier; this note exists so the
    # style-embedding consumer is discoverable and the extra
    # weight-download footprint is surfaced before a run.
    PythonDep(
        name="transformers (style-embedding)",
        import_name="transformers",
        pip_name="transformers",
        summary=(
            "Loads the frozen style encoder for voice_fingerprint.py "
            "(Surface authorship_embedding). Default LUAR "
            "(rrivera1849/LUAR-MUD, Apache-2.0) loads via transformers "
            "with trust_remote_code=True; a DISTINCT ~0.5-1.4 GB "
            "model download from the Tier-4 surprisal scorers. The "
            "--model wegmann cross-check additionally needs "
            "sentence-transformers."
        ),
        optional_in_tier=True,
    ),
]


TIERS = {
    "core": {
        "label": "core stylometry",
        "requirements_file": "requirements.txt",
        "python_deps": CORE_PYTHON_DEPS,
        "spacy_models": CORE_SPACY_MODELS,
        "system_deps": [],
    },
    "acquisition": {
        "label": "impostor-corpus acquisition",
        "requirements_file": "requirements-acquisition.txt",
        "python_deps": ACQUISITION_PYTHON_DEPS,
        "spacy_models": [],
        "system_deps": [],
    },
    "ocr": {
        "label": "OCR for image-only PDFs (optional within acquisition)",
        "requirements_file": "requirements-acquisition.txt",
        "python_deps": OCR_PYTHON_DEPS,
        "spacy_models": [],
        "system_deps": OCR_SYSTEM_DEPS,
    },
    "calibration": {
        "label": "threshold calibration",
        "requirements_file": "requirements-calibration.txt",
        "python_deps": CALIBRATION_PYTHON_DEPS,
        "spacy_models": [],
        "system_deps": [],
    },
    "surprisal": {
        "label": (
            "Tier 4 surprisal + Binoculars (Surface 5). "
            "external-mirror's default sbert metric needs "
            "sentence-transformers from the optional tier — "
            "not this one."
        ),
        "requirements_file": "requirements-surprisal.txt",
        "python_deps": SURPRISAL_PYTHON_DEPS,
        "spacy_models": [],
        "system_deps": [],
    },
    "optional": {
        "label": "optional power-ups",
        "requirements_file": None,  # commented entries in requirements.txt
        "python_deps": OPTIONAL_PYTHON_DEPS,
        "spacy_models": [],
        "system_deps": [],
    },
}


# --------------- Detection ---------------------------------------


def check_python_dep(dep: PythonDep) -> dict[str, Any]:
    """Return ``{installed, version, error}`` for a Python dep."""
    try:
        mod = importlib.import_module(dep.import_name)
    except ImportError as exc:
        return {
            "installed": False,
            "version": None,
            "error": str(exc),
        }
    version = getattr(mod, "__version__", None)
    if version is None:
        # Some modules don't expose __version__ — try
        # importlib.metadata as a fallback.
        try:
            import importlib.metadata as md
            version = md.version(dep.pip_name)
        except Exception:
            version = "unknown"
    return {"installed": True, "version": version, "error": None}


def check_spacy_model(model: SpacyModel) -> dict[str, Any]:
    """Try to load the spaCy model; report success/failure.

    spaCy models are pip-installable as wheel packages (their import
    name matches the model name) but the canonical install path is
    ``python -m spacy download <model>``. Both paths produce the
    same loadable object, so we just try ``spacy.load``.
    """
    try:
        import spacy
    except ImportError:
        return {
            "loaded": False,
            "error": "spaCy not installed; install requirements.txt first",
        }
    try:
        spacy.load(model.name)
        return {"loaded": True, "error": None}
    except (OSError, IOError) as exc:
        return {"loaded": False, "error": str(exc)}


def check_system_dep(dep: SystemDep) -> dict[str, Any]:
    """Check via shutil.which whether a binary is on PATH."""
    binary = shutil.which(dep.binary)
    return {
        "installed": binary is not None,
        "path": binary,
    }


def survey_tier(tier_key: str, tier: dict[str, Any]) -> dict[str, Any]:
    """Run every check in one tier and aggregate the result."""
    py_results: list[dict[str, Any]] = []
    for dep in tier["python_deps"]:
        result = check_python_dep(dep)
        py_results.append({
            "name": dep.name,
            "import_name": dep.import_name,
            "pip_name": dep.pip_name,
            "summary": dep.summary,
            "optional": dep.optional_in_tier,
            **result,
        })
    spacy_results: list[dict[str, Any]] = []
    for model in tier["spacy_models"]:
        result = check_spacy_model(model)
        spacy_results.append({
            "name": model.name,
            "summary": model.summary,
            **result,
        })
    sys_results: list[dict[str, Any]] = []
    for dep in tier["system_deps"]:
        result = check_system_dep(dep)
        sys_results.append({
            "name": dep.name,
            "binary": dep.binary,
            "summary": dep.summary,
            "optional": dep.optional_in_tier,
            **result,
        })

    # Aggregate: missing-required count is what triggers a "needs
    # install" recommendation. Missing-optional is reported separately
    # so the user can decide.
    missing_required = (
        [r for r in py_results if not r["installed"] and not r["optional"]]
        + [r for r in spacy_results if not r["loaded"]]
        + [r for r in sys_results if not r["installed"] and not r["optional"]]
    )
    missing_optional = (
        [r for r in py_results if not r["installed"] and r["optional"]]
        + [r for r in sys_results if not r["installed"] and r["optional"]]
    )
    return {
        "tier_key": tier_key,
        "label": tier["label"],
        "requirements_file": tier["requirements_file"],
        "python_deps": py_results,
        "spacy_models": spacy_results,
        "system_deps": sys_results,
        "missing_required_count": len(missing_required),
        "missing_optional_count": len(missing_optional),
        "missing_required": missing_required,
        "missing_optional": missing_optional,
    }


def survey_all(tiers: list[str] | None = None) -> dict[str, Any]:
    """Survey every tier (or a subset) and return the aggregate."""
    keys = tiers or list(TIERS.keys())
    results = [survey_tier(k, TIERS[k]) for k in keys if k in TIERS]
    return {
        "task_surface": TASK_SURFACE,
        "tool": TOOL_NAME,
        "version": SCRIPT_VERSION,
        "python": python_version_summary(),
        "plugin_root": str(PLUGIN_ROOT),
        "tiers": results,
    }


# --------------- Install-command suggestions --------------------


def suggest_install_commands(
    survey: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return a list of platform-appropriate install commands for
    every missing required dep.

    Each command dict carries:
      * ``label`` — human-readable description
      * ``platform`` — "macos" / "linux" / "windows" or "all"
      * ``command`` — the shell command to run
      * ``optional`` — true if installing this is optional

    The setup skill reads this list, presents it to the user, asks
    permission, and then runs the commands.
    """
    out: list[dict[str, Any]] = []
    plat = survey["python"]["platform"]

    for tier in survey["tiers"]:
        # Python pip install for missing required deps. Use the
        # tier's requirements file when one exists; otherwise list
        # individual packages.
        missing_py_required = [
            r for r in tier["python_deps"]
            if not r["installed"] and not r["optional"]
        ]
        if missing_py_required:
            req = tier["requirements_file"]
            if req:
                # Prefer the requirements file invocation — picks up
                # version pins and any indirect deps the registry has.
                req_path = PLUGIN_ROOT / req
                out.append({
                    "label": f"Install {tier['label']} Python deps",
                    "platform": "all",
                    "command": (
                        f"pip install -r {req_path.relative_to(PLUGIN_ROOT.parent.parent) if req_path.is_relative_to(PLUGIN_ROOT.parent.parent) else req_path}"
                    ),
                    "optional": False,
                    "tier": tier["tier_key"],
                })
            else:
                packages = " ".join(r["pip_name"] for r in missing_py_required)
                out.append({
                    "label": f"Install {tier['label']} Python deps",
                    "platform": "all",
                    "command": f"pip install {packages}",
                    "optional": False,
                    "tier": tier["tier_key"],
                })

        # spaCy model downloads.
        missing_models = [m for m in tier["spacy_models"] if not m["loaded"]]
        for m in missing_models:
            out.append({
                "label": f"Download spaCy model {m['name']}",
                "platform": "all",
                "command": f"python -m spacy download {m['name']}",
                "optional": False,
                "tier": tier["tier_key"],
            })

        # System binaries.
        missing_sys_required = [
            d for d in tier["system_deps"]
            if not d["installed"] and not d["optional"]
        ]
        missing_sys_optional = [
            d for d in tier["system_deps"]
            if not d["installed"] and d["optional"]
        ]
        for d in missing_sys_required + missing_sys_optional:
            # Look up the install hint for the user's platform; fall
            # back to the linux hint if nothing matches.
            cfg = next(
                (sd for sd in OCR_SYSTEM_DEPS if sd.name == d["name"]),
                None,
            )
            hint = ""
            if cfg is not None:
                hint = cfg.install_hints.get(plat) or cfg.install_hints.get("linux", "")
            out.append({
                "label": f"Install system binary {d['name']}",
                "platform": plat,
                "command": hint,
                "optional": d["optional"],
                "tier": tier["tier_key"],
            })

        # Missing optional Python deps — report as suggestions, not
        # required.
        missing_py_optional = [
            r for r in tier["python_deps"]
            if not r["installed"] and r["optional"]
        ]
        for r in missing_py_optional:
            out.append({
                "label": f"(optional) Install {r['name']}: {r['summary']}",
                "platform": "all",
                "command": f"pip install {r['pip_name']}",
                "optional": True,
                "tier": tier["tier_key"],
            })
    return out


# --------------- Rendering --------------------------------------


def _check_or_x(ok: bool) -> str:
    return "[ok]" if ok else "[--]"


def render_human(survey: dict[str, Any]) -> str:
    """Plain-text summary for the terminal."""
    lines: list[str] = []
    py = survey["python"]
    lines.append(f"Python: {py['version']} ({py['implementation']})")
    lines.append(f"Platform: {py['platform']} ({py['platform_release']})")
    lines.append(f"Plugin root: {survey['plugin_root']}")
    lines.append("")
    for tier in survey["tiers"]:
        marker = "OK" if tier["missing_required_count"] == 0 else "MISSING"
        lines.append(
            f"== {tier['label']} ({tier['tier_key']}): {marker} =="
        )
        for r in tier["python_deps"]:
            tag = "(optional)" if r["optional"] else ""
            ver = f" {r['version']}" if r["installed"] and r["version"] else ""
            lines.append(
                f"  {_check_or_x(r['installed'])} {r['name']}{ver} {tag}".rstrip()
            )
        for m in tier["spacy_models"]:
            lines.append(
                f"  {_check_or_x(m['loaded'])} spaCy model {m['name']}"
            )
        for d in tier["system_deps"]:
            tag = "(optional)" if d["optional"] else ""
            lines.append(
                f"  {_check_or_x(d['installed'])} system: {d['name']} {tag}".rstrip()
            )
        if tier["missing_required_count"]:
            lines.append(
                f"  → install {tier['requirements_file'] or 'individually'} "
                "to fix missing required deps."
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_suggest(survey: dict[str, Any]) -> str:
    """Print the install commands the setup skill would propose."""
    cmds = suggest_install_commands(survey)
    if not cmds:
        return "All required dependencies are present. Nothing to install.\n"
    plat = survey["python"]["platform"]
    lines = [f"# Install commands for {plat}:", ""]
    required = [c for c in cmds if not c["optional"]]
    optional = [c for c in cmds if c["optional"]]
    if required:
        lines.append("# Required:")
        for c in required:
            lines.append(f"# {c['label']}")
            lines.append(c["command"])
            lines.append("")
    if optional:
        lines.append("# Optional (install if you need them):")
        for c in optional:
            lines.append(f"# {c['label']}")
            lines.append(c["command"])
            lines.append("")
    return "\n".join(lines)


# --------------- CLI --------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "Survey SETEC framework dependencies across all tiers. "
            "Reports state; does not install. The setup skill reads "
            "the JSON output, asks the user for permission, and runs "
            "the install commands itself."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--tier",
        choices=sorted(TIERS.keys()) + ["all"],
        default="all",
        help="Which tier(s) to survey (default: all).",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the full survey as JSON.",
    )
    p.add_argument(
        "--suggest", action="store_true",
        help="Print platform-appropriate install commands for what's missing.",
    )
    return p


def run(args: argparse.Namespace) -> int:
    tiers = None if args.tier == "all" else [args.tier]
    survey = survey_all(tiers)

    if args.json:
        sys.stdout.write(json.dumps(survey, indent=2, default=str) + "\n")
    elif args.suggest:
        sys.stdout.write(render_suggest(survey))
    else:
        sys.stdout.write(render_human(survey))

    # Exit code: 0 if everything required is present; 1 if anything
    # required is missing. Optional misses don't change the code.
    missing_required = sum(
        t["missing_required_count"] for t in survey["tiers"]
    )
    return 0 if missing_required == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
