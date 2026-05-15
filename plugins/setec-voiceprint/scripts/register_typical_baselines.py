#!/usr/bin/env python3
"""register_typical_baselines.py — load register-typical baselines.

Reads ``baselines/register_typical.yaml`` (per
`internal/SPEC_aic_8_9_implementation.md` Step 9) and exposes a
small API for the AIC-8 / AIC-9 detectors and the compound
``aesthetic_authority_audit`` to look up baseline values when no
personal baseline is available.

The YAML ships with the framework at provisional values. Operators
who calibrate against their own corpora can either edit the YAML
in place or override at the call site via ``--baseline`` on each
detector's CLI.

Schema (per `baselines/register_typical.yaml`)::

    register_typical_baselines:
      <register_name>:
        <signal_name>:
          mean: <float>
          sd: <float>
          band: [<low>, <high>]
          provisional: true
          provenance: null

Registers shipped:
  * contemporary_essay
  * literary_fiction
  * hard_science_fiction
  * academic_prose
  * blog_post
  * technical_documentation

Signals shipped:
  * kicker_density (proportion of paragraphs)
  * image_conjunction_per_1000_tokens (count / 1000 tokens)
  * prestige_metaphor_per_1000_tokens (count / 1000 tokens)
  * domain_scatter_entropy (normalized [0, 1])

The loader is lazy + cached; loading the YAML happens on first
call and the parsed structure is reused thereafter. Operator-
extensible via ``extra_yaml_paths`` argument to merge additional
register entries (e.g., custom registers for niche genres).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_YAML_PATH = _REPO_ROOT / "baselines" / "register_typical.yaml"


class RegisterTypicalBaselineError(RuntimeError):
    """Raised when register-typical baselines cannot be loaded or
    resolved.

    Typed exception so callers can distinguish baseline-resolution
    failures from generic runtime errors and decide whether to
    fall back to operator-supplied values or to fail loudly.
    """


@lru_cache(maxsize=1)
def _load_baselines(yaml_path: str = "") -> dict[str, Any]:
    """Load and cache the register_typical.yaml structure.

    The ``yaml_path`` argument is a string (not Path) so
    ``lru_cache`` can hash it. Pass empty string for the default
    location.
    """
    path = Path(yaml_path) if yaml_path else _DEFAULT_YAML_PATH
    if not path.exists():
        raise RegisterTypicalBaselineError(
            f"register_typical.yaml not found at {path}. "
            "The file ships with the framework at "
            "`baselines/register_typical.yaml`; if you've moved it, "
            "pass the new location via `yaml_path` or the "
            "`--register-typical-yaml` CLI flag."
        )
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RegisterTypicalBaselineError(
            "PyYAML is not installed. Install with: "
            "pip install -r plugins/setec-voiceprint/requirements.txt"
        ) from exc
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RegisterTypicalBaselineError(
            f"Failed to parse {path}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise RegisterTypicalBaselineError(
            f"{path}: top-level YAML must be a mapping, got "
            f"{type(data).__name__}"
        )
    baselines = data.get("register_typical_baselines")
    if not isinstance(baselines, dict):
        raise RegisterTypicalBaselineError(
            f"{path}: missing or invalid "
            "`register_typical_baselines` key"
        )
    return baselines


def available_registers(
    yaml_path: Optional[Path | str] = None,
) -> list[str]:
    """Return the sorted list of register names in the loaded YAML."""
    path_str = str(yaml_path) if yaml_path else ""
    baselines = _load_baselines(path_str)
    return sorted(baselines.keys())


def get_baseline(
    register: str,
    signal: str,
    *,
    yaml_path: Optional[Path | str] = None,
) -> Optional[dict[str, Any]]:
    """Return the baseline entry for ``(register, signal)``.

    Returns a dict with ``mean``, ``sd``, ``band``, ``provisional``,
    ``provenance`` keys. Returns ``None`` if the register or signal
    isn't registered; callers handle the None case (fall back to
    operator-supplied baseline, or treat as missing).

    Register and signal names are matched case-insensitively against
    the YAML keys. Operators using mixed-case names (e.g.,
    "Literary Fiction") get the same lookup as snake_case.
    """
    path_str = str(yaml_path) if yaml_path else ""
    baselines = _load_baselines(path_str)
    register_norm = register.lower().replace(" ", "_").replace("-", "_")
    signal_norm = signal.lower()
    register_entry = baselines.get(register_norm)
    if register_entry is None:
        # Try direct lookup as a fallback.
        register_entry = baselines.get(register)
    if not isinstance(register_entry, dict):
        return None
    signal_entry = register_entry.get(signal_norm) or register_entry.get(signal)
    if not isinstance(signal_entry, dict):
        return None
    return signal_entry


def get_baseline_mean(
    register: str,
    signal: str,
    *,
    yaml_path: Optional[Path | str] = None,
) -> Optional[float]:
    """Convenience: return just the ``mean`` for ``(register, signal)``.

    Returns ``None`` if the baseline isn't registered.
    """
    entry = get_baseline(register, signal, yaml_path=yaml_path)
    if entry is None:
        return None
    mean = entry.get("mean")
    if isinstance(mean, (int, float)):
        return float(mean)
    return None


def resolve_baseline(
    register: Optional[str],
    signal: str,
    *,
    explicit_value: Optional[float] = None,
    explicit_source: Optional[str] = None,
    yaml_path: Optional[Path | str] = None,
) -> Optional[dict[str, Any]]:
    """Resolve a baseline by precedence: explicit > register-typical.

    Returns a dict with ``value`` (float) and ``source`` (string) if
    a baseline could be resolved; returns ``None`` if neither path
    produces one. The ``source`` field documents where the baseline
    came from so the detector's JSON output can surface provenance.

    Precedence:

      1. ``explicit_value`` (operator-supplied) wins. The
         ``explicit_source`` label is recorded as the source.
      2. ``register`` (lookup via `get_baseline_mean`) is the
         fallback. The source label is
         ``register_typical_<register>``.
      3. If neither path resolves, returns ``None``.
    """
    if explicit_value is not None:
        return {
            "value": float(explicit_value),
            "source": explicit_source or "operator-supplied",
        }
    if register:
        mean = get_baseline_mean(register, signal, yaml_path=yaml_path)
        if mean is not None:
            return {
                "value": mean,
                "source": f"register_typical_{register.lower().replace(' ', '_').replace('-', '_')}",
            }
    return None
