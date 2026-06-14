#!/usr/bin/env python3
"""argument_register_baselines.py — load register-matched ArgScope baselines.

Reads ``baselines/argument_register_baselines.yaml`` (calibration spec §2/§6 C0)
and resolves a per-genre, per-signal baseline row for ``argument_decision_audit``.
The audit's paper anchors are public-debate-forum means (register-bound,
directional reference); a register row lets a genre carry a register-matched
``human`` mean (and, at ``calibrated``, an ``ai`` mean + measured discrimination)
so a per-signal ``calibration_status`` can graduate up SETEC's standard ladder.

Resolution order (mirrors the ``--baseline-dir`` / ``$SETEC_BASELINES_DIR``
convention the rest of SETEC uses):

  1. ``baseline_dir`` argument (the surface's ``--baseline-dir``), if it holds an
     ``argument_register_baselines.yaml``;
  2. ``$SETEC_BASELINES_DIR/argument_register_baselines.yaml``, if it exists;
  3. the shipped ``baselines/argument_register_baselines.yaml``.

This keeps the surface stdlib-only by default: PyYAML is imported lazily, only
when ``--register`` / ``--baseline-dir`` is actually used. The C1/C3 corpus
builders write ``empirically_oriented`` / ``calibrated`` rows into an
operator-local copy this resolver then prefers — they "drop straight in".
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The canonical 5-tier calibration ladder (variance_audit.py
# THRESHOLD_STATUS_VALUES; provenance enforced in ThresholdSpec.__post_init__).
# Re-stated here (light, no heavy import) — keep in sync with the canonical set.
CALIBRATION_STATUS_VALUES = frozenset(
    {"heuristic", "literature_anchored", "empirically_oriented", "calibrated", "structural_only"}
)

YAML_NAME = "argument_register_baselines.yaml"
ENV_VAR = "SETEC_BASELINES_DIR"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_YAML_PATH = _REPO_ROOT / "baselines" / YAML_NAME


class RegisterBaselineError(RuntimeError):
    """Raised when a register baseline cannot be loaded or fails the honesty
    discipline (an above-heuristic row with no provenance, an unknown status, or
    an out-of-range proportion). Typed so the surface can fail loud on a
    malformed baseline rather than silently scoring against a bad anchor."""


@dataclass(frozen=True)
class SignalBaseline:
    """One genre's baseline for one signal."""

    signal_key: str
    human_mean: float | None
    ai_mean: float | None
    status: str
    provenance: str | None
    provisional: bool


@dataclass(frozen=True)
class RegisterBaseline:
    """A genre's full register baseline: per-signal rows + (at ``calibrated``)
    a genre-level discrimination row. ``source_path`` records which YAML it came
    from (shipped vs operator-local), for the envelope's provenance."""

    genre: str
    signals: dict[str, SignalBaseline]
    discrimination: dict[str, Any] | None
    source_path: str

    @property
    def is_calibrated(self) -> bool:
        """True iff the genre carries a measured discrimination row — the only
        state that licenses a real band verdict (calibration spec §4)."""
        return bool(self.discrimination) and self.discrimination.get("da_AUC") is not None


def resolve_yaml_path(baseline_dir: Path | None = None) -> Path:
    """Resolve which ``argument_register_baselines.yaml`` to read, honoring the
    operator-local override before the shipped default."""
    if baseline_dir is not None:
        cand = Path(baseline_dir).expanduser() / YAML_NAME
        if cand.is_file():
            return cand
    env = os.environ.get(ENV_VAR)
    if env:
        cand = Path(env).expanduser() / YAML_NAME
        if cand.is_file():
            return cand
    return _DEFAULT_YAML_PATH


def _coerce_mean(arm: Any, *, genre: str, signal: str, which: str) -> float | None:
    if arm is None:
        return None
    if not isinstance(arm, dict) or "mean" not in arm:
        raise RegisterBaselineError(
            f"{genre}.{signal}.{which}: expected a mapping with a `mean`, got {arm!r}"
        )
    mean = arm["mean"]
    if not isinstance(mean, (int, float)) or isinstance(mean, bool):
        raise RegisterBaselineError(f"{genre}.{signal}.{which}.mean must be a number, got {mean!r}")
    if not (0.0 <= float(mean) <= 1.0):
        raise RegisterBaselineError(
            f"{genre}.{signal}.{which}.mean={mean} out of [0, 1] (signals are proportions)"
        )
    return float(mean)


def load_register(
    genre: str, *, baseline_dir: Path | None = None, yaml_path: Path | None = None
) -> RegisterBaseline | None:
    """Load ``genre``'s register baseline, or None if the genre is absent.

    Raises RegisterBaselineError on a malformed file / a row that violates the
    honesty discipline (above-heuristic status with no provenance, unknown
    status, out-of-range proportion). A `calibrated` per-signal status with no
    genre-level discrimination row is also rejected (calibrated REQUIRES
    measured discrimination, §4)."""
    path = Path(yaml_path) if yaml_path is not None else resolve_yaml_path(baseline_dir)
    if not path.is_file():
        raise RegisterBaselineError(
            f"{YAML_NAME} not found at {path}. It ships at baselines/{YAML_NAME}; "
            f"pass --baseline-dir or set ${ENV_VAR} for an operator-local copy."
        )
    try:
        import yaml  # lazy — keeps the surface stdlib-only when --register is unused
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RegisterBaselineError(
            "PyYAML is not installed (needed for --register / --baseline-dir). "
            "Install: pip install -r plugins/setec-voiceprint/requirements.txt"
        ) from exc
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RegisterBaselineError(f"{path} did not parse: {exc}") from exc

    table = doc.get("argument_register_baselines") or {}
    if not isinstance(table, dict):
        raise RegisterBaselineError(f"{path}: `argument_register_baselines` must be a mapping")
    row = table.get(genre)
    if row is None:
        return None
    if not isinstance(row, dict):
        raise RegisterBaselineError(f"{path}: genre {genre!r} must be a mapping of signals")

    discrimination = (doc.get("discrimination") or {}).get(genre)

    signals: dict[str, SignalBaseline] = {}
    for signal_key, body in row.items():
        if not isinstance(body, dict):
            raise RegisterBaselineError(f"{genre}.{signal_key} must be a mapping")
        status = body.get("status", "heuristic")
        if status not in CALIBRATION_STATUS_VALUES:
            raise RegisterBaselineError(
                f"{genre}.{signal_key}.status={status!r} is not a calibration_status "
                f"(one of {sorted(CALIBRATION_STATUS_VALUES)})"
            )
        provenance = body.get("provenance")
        # The ThresholdSpec rule, reused: provenance is mandatory above heuristic.
        if status != "heuristic" and not provenance:
            raise RegisterBaselineError(
                f"{genre}.{signal_key}: status {status!r} requires provenance "
                f"(no documented corpus/anchor → cannot claim a tier above heuristic)"
            )
        if status == "calibrated" and not (discrimination and discrimination.get("da_AUC") is not None):
            raise RegisterBaselineError(
                f"{genre}.{signal_key}: `calibrated` requires a genre-level "
                f"`discrimination` row with measured da_AUC (§4)"
            )
        signals[signal_key] = SignalBaseline(
            signal_key=signal_key,
            human_mean=_coerce_mean(body.get("human"), genre=genre, signal=signal_key, which="human"),
            ai_mean=_coerce_mean(body.get("ai"), genre=genre, signal=signal_key, which="ai"),
            status=status,
            provenance=provenance,
            provisional=bool(body.get("provisional", True)),
        )
    return RegisterBaseline(
        genre=genre,
        signals=signals,
        discrimination=discrimination if isinstance(discrimination, dict) else None,
        source_path=str(path),
    )
