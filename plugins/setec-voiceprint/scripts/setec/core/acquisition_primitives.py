"""setec.core.acquisition_primitives — content identity and the private-path
guard, shared by every acquisition surface.

These helpers were defined in `acquisition_core.py`, which carries a
`capabilities.d` fragment and is therefore an L2 *surface* under
`tools/check_layering.py`'s predicates. Every acquirer needs them, so every
acquirer had to import a surface from a surface — 22 grandfathered
`l2_to_l2` exemption rows, and a ratchet that (correctly) refuses to grant a
23rd: "a genuinely new violation in new code must be fixed, never added
here."

Fixing that means the shared behaviour has to live below the surface layer,
which is what `setec.core` exists for (P3, "eligible L1 core"). Both the
identity rule and the privacy refusal stay single-sourced:
`acquisition_core` re-exports these names so its 22 existing callers are
untouched, and a new acquirer imports this module directly instead of
adding an exemption row.

L1 by predicate, and it must stay that way: no `capabilities.d` fragment,
no `__main__` CLI entry, no `build_output(...)` envelope. Adding any of the
three would silently promote this to L2 and re-create the very edge it
exists to remove.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Iterable

# Marker directory name for the private-safe path check. Mirrors the
# existing `voice_profile.is_private_output_path` convention.
PRIVATE_DIR_NAME = "ai-prose-baselines-private"


def compute_content_hash(text: str) -> str:
    """SHA-256 of cleaned text, prefixed ``sha256:``.

    The prefix matches the manifest convention in
    ``references/manifest-schema.md`` and lets future hash families
    (e.g., a normalized-text fingerprint) coexist without ambiguity.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def is_private_safe_path(path: Path) -> bool:
    """Marker-based private-path check.

    Returns True iff any component of the resolved absolute path is
    named ``ai-prose-baselines-private``. Mirrors
    ``voice_profile.is_private_output_path`` and
    ``voice_drift_tracker._check_output_privacy``. Both repo-internal
    and sibling private roots are accepted; the documented standard
    layout uses a sibling directory.
    """
    return PRIVATE_DIR_NAME in path.expanduser().resolve().parts


def check_output_privacy(
    paths: Iterable[Path], *, allow_public: bool, tool: str,
) -> None:
    """Enforce the marker-based private-path rule across output paths.

    Acquisition tools call this once after computing every path they
    plan to write (output dir, manifest path, summary report). When
    ``allow_public`` is False and any path is outside a private root,
    the tool prints a refusal explaining both options (write into a
    private root, or pass ``--allow-public-output`` for non-personal
    corpora) and exits with code 2.
    """
    if allow_public:
        return
    for p in paths:
        if p is None:
            continue
        if not is_private_safe_path(Path(p)):
            sys.stderr.write(
                f"Refusing to write {p}: not under any directory "
                f"named '{PRIVATE_DIR_NAME}'. {tool} output is voice-"
                f"cloning input. Either write into a directory named "
                f"'{PRIVATE_DIR_NAME}' (repo-internal or sibling — "
                f"both are accepted), or pass --allow-public-output "
                f"for non-personal corpora.\n"
            )
            sys.exit(2)
