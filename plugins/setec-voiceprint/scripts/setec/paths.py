#!/usr/bin/env python3
"""setec.paths — plugin-root resolution and stay-put data paths.

Per `specs/svp-packaging-conversion.md` §1 ("Root resolution before
moves"). This module is the ONE place plugin-runtime code anchors
itself to the `setec-voiceprint` plugin root, instead of each module
computing its own `Path(__file__).resolve().parent...` chain.

This is intentionally the opposite job from a repo tool's `REPO_ROOT`:
`find_plugin_root()` always resolves the *plugin* root
(`plugins/setec-voiceprint/`), never the repository root. Tools under
`tools/` keep their own repo-root resolution — see the spec's
"Tools do not move" note — and must NOT import this module for that
purpose.

Zero-install constraint: this module is stdlib-only (no PyYAML, no
third-party import) so every plugin-runtime module can import it
unconditionally, exactly like `claim_license.py` today.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# The marker file that identifies a directory as the setec-voiceprint
# plugin root. Relative to a candidate root: <root>/.claude-plugin/plugin.json
_PLUGIN_MARKER = Path(".claude-plugin") / "plugin.json"


class PluginRootError(RuntimeError):
    """Raised by :func:`find_plugin_root` when the upward walk from
    ``start`` finds zero or more than one directory carrying
    ``.claude-plugin/plugin.json``. Both are refused (fail closed):
    zero means the walk left the plugin checkout (or the marker was
    deleted); more than one means ``start`` sits inside a nested /
    vendored plugin checkout and resolution would be ambiguous."""


def _candidate_roots(start: Path) -> list[Path]:
    """Every directory at or above ``start`` (inclusive) carrying the
    plugin marker file, nearest first."""
    start = start.resolve()
    search_from = start if start.is_dir() else start.parent
    chain = [search_from, *search_from.parents]
    return [d for d in chain if (d / _PLUGIN_MARKER).is_file()]


def find_plugin_root(start: Path | str | None = None) -> Path:
    """Walk upward from ``start`` for ``.claude-plugin/plugin.json`` and
    return the ONE directory that carries it.

    ``start`` defaults to this module's own file location
    (``scripts/setec/paths.py``), so a bare ``find_plugin_root()`` call
    from anywhere in the package resolves the same plugin root
    regardless of the caller's ``__file__``. Callers that need to probe
    a different tree — a scratch copy, a foreign checkout — pass an
    explicit ``start``.

    Refuses (raises :class:`PluginRootError`) on zero or more than one
    match: zero means the walk left the checkout without finding the
    marker (a foreign tree, or a deleted marker); more than one means
    ``start`` is itself inside a nested/vendored plugin checkout, where
    "the" plugin root is ambiguous. Both are fail-closed rather than
    silently picking the nearest or farthest match.
    """
    if start is None:
        start = Path(__file__).resolve()
    else:
        start = Path(start)

    matches = _candidate_roots(start)
    if not matches:
        raise PluginRootError(
            f"find_plugin_root({start!r}): no ancestor directory carries "
            f"{_PLUGIN_MARKER.as_posix()} — walked up to "
            f"{Path(start).resolve().anchor or '/'}"
        )
    if len(matches) > 1:
        rendered = ", ".join(str(m) for m in matches)
        raise PluginRootError(
            f"find_plugin_root({start!r}): ambiguous — {len(matches)} "
            f"ancestor directories carry {_PLUGIN_MARKER.as_posix()} "
            f"({rendered}); refusing to guess which is the plugin root"
        )
    return matches[0]


@dataclass(frozen=True)
class PluginPaths:
    """Named, stay-put data paths under a resolved plugin root.

    Every path here is a *location*, not a promise it exists — a
    caller that needs the directory present (e.g. to glob it) still
    checks/creates it. This dataclass only removes the repeated
    ``Path(__file__).resolve().parent...`` arithmetic; it does not
    change any existing loader's missing-directory behavior (per the
    spec's "leaves `claim_license._load_surface_labels` at its tested
    missing-directory behavior" constraint).
    """

    root: Path
    capabilities_d: Path
    plugin_json: Path
    claim_license_surfaces: Path
    register_tiers_d: Path
    references: Path
    data: Path


def plugin_paths(start: Path | str | None = None) -> PluginPaths:
    """Resolve the plugin root from ``start`` (see :func:`find_plugin_root`)
    and return its named data paths."""
    root = find_plugin_root(start)
    return PluginPaths(
        root=root,
        capabilities_d=root / "capabilities.d",
        plugin_json=root / _PLUGIN_MARKER,
        claim_license_surfaces=root / "scripts" / "claim_license_surfaces",
        register_tiers_d=root / "register_tiers.d",
        references=root / "references",
        data=root / "data",
    )


# ---------- convenience single-path accessors -----------------------
#
# Thin wrappers so a caller that only wants one path doesn't have to
# spell `plugin_paths(start).capabilities_d`. Each still resolves the
# root fresh (cheap: a handful of `is_file()` stats), so a caller in a
# scratch copy gets the scratch copy's root, never a cached value from
# a different process/tree.

def capabilities_d_dir(start: Path | str | None = None) -> Path:
    return plugin_paths(start).capabilities_d


def plugin_json_path(start: Path | str | None = None) -> Path:
    return plugin_paths(start).plugin_json


def claim_license_surfaces_dir(start: Path | str | None = None) -> Path:
    return plugin_paths(start).claim_license_surfaces


def register_tiers_d_dir(start: Path | str | None = None) -> Path:
    return plugin_paths(start).register_tiers_d


def references_dir(start: Path | str | None = None) -> Path:
    return plugin_paths(start).references


def data_dir(start: Path | str | None = None) -> Path:
    return plugin_paths(start).data
