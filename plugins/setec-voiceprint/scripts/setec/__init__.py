"""setec — the zero-install SETEC Voiceprint package.

Per `specs/svp-packaging-conversion.md`. This package lives at
`plugins/setec-voiceprint/scripts/setec/` and is imported directly off
`scripts/` on `sys.path` — no `pip install`, no `PYTHONPATH`, no
repository-relative CWD required. A copied `plugins/setec-voiceprint/`
subtree works standalone.

Layout (populated incrementally by later packaging phases; P1 lands
only this skeleton plus `paths.py`, ahead of any production move):

    setec/
      paths.py          # plugin-root resolution + stay-put data paths
      contract/          # output_schema, claim_license, capabilities (P2)
      core/               # L1 library modules (P3)
      surfaces/          # L2 capability-bearing whole modules (P4)
      calibration/        # implementations behind calibration/ launchers (P4)
      external_mirror/   # implementations behind external_mirror/ launchers (P4)
      replication/        # implementations behind replication/ launchers (P4)

This module intentionally does not import its subpackages eagerly —
each stays empty until its owning phase lands a real module, so
importing `setec` never pulls in heavy optional dependencies.
"""

from __future__ import annotations
