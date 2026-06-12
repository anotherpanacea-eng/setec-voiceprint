#!/usr/bin/env python3
"""Shared R1 (normalized-entrypoint) field-bundle validator.

`validate_r1_bundle` is a pure, stdlib-only validator for a capability entry's
R1 field bundle (`min_setec_version` / `json_delivery` / `inputs` /
`required_groups`). It is the SINGLE source of truth, imported by BOTH the
drift linter (`tools/check_capabilities_drift.py`) and the capabilities seeder
(`tools/seed_capabilities.py`) so the two can never drift. Stdlib-only by
design, so the seeder stays self-contained — it imports this small validator,
NOT the drift linter (which pulls in the manifest loader).
"""

from __future__ import annotations

import re

# R1 (normalized-entrypoint) field bundle. The presence of `min_setec_version`
# is the bundle marker: a fragment carrying it is a subprocess consumer surface
# and MUST carry the rest of the bundle in valid form. Fragments WITHOUT
# `min_setec_version` are exempt (reference-tagged / internal entries are left
# untouched).
_R1_BUNDLE_MARKER = "min_setec_version"
_VALID_JSON_DELIVERY = frozenset({"stdout", "file"})
_VALID_INPUT_TYPES = frozenset(
    {"path", "string", "int", "float", "enum", "bool"}
)
# Conservative semver: MAJOR.MINOR.PATCH with optional -prerelease/+build. Good
# enough to catch a fat-fingered floor like "1.86" or "v1.86.0" without pulling
# in a packaging dep.
_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


def validate_r1_bundle(entry: dict) -> list[str]:
    """Return a list of human-readable problems with `entry`'s R1 field bundle.

    An entry is subject to the bundle iff it carries `min_setec_version` (the
    marker). When present, the FULL bundle is required and validated:

      * `min_setec_version` — a valid semver string.
      * `json_delivery` — one of {stdout, file}.
      * `inputs` — a non-empty list of mappings, each with `flag`, `type`, and
        `required`; `type` in the legal vocabulary; `values` (a non-empty list)
        present iff `type == "enum"`. An input may carry a `group`; an
        entry-level `required_groups` names groups of which exactly one member
        must be supplied, and every member of such a group must be
        `required: false` (the group, not the member, is mandatory).

    Entries WITHOUT the marker return `[]` (exempt). This is a pure validator
    (no side effects) so both the drift linter and the seeder can reuse it."""
    if _R1_BUNDLE_MARKER not in entry:
        return []
    problems: list[str] = []

    floor = entry.get("min_setec_version")
    if not isinstance(floor, str) or not _SEMVER_RE.match(floor):
        problems.append(
            f"min_setec_version must be a valid semver string "
            f"(MAJOR.MINOR.PATCH); got {floor!r}"
        )

    delivery = entry.get("json_delivery")
    if delivery not in _VALID_JSON_DELIVERY:
        problems.append(
            f"json_delivery must be one of {sorted(_VALID_JSON_DELIVERY)!r}; "
            f"got {delivery!r}"
        )

    inputs = entry.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        problems.append(
            f"inputs must be a non-empty list of mappings; got "
            f"{type(inputs).__name__ if inputs is not None else None!r}"
        )
    else:
        for i, item in enumerate(inputs):
            if not isinstance(item, dict):
                problems.append(
                    f"inputs[{i}] must be a mapping; got "
                    f"{type(item).__name__}"
                )
                continue
            for key in ("flag", "type", "required"):
                if key not in item:
                    problems.append(
                        f"inputs[{i}] missing required key {key!r}"
                    )
            itype = item.get("type")
            if itype is not None and itype not in _VALID_INPUT_TYPES:
                problems.append(
                    f"inputs[{i}].type {itype!r} not in "
                    f"{sorted(_VALID_INPUT_TYPES)!r}"
                )
            has_values = "values" in item
            if itype == "enum":
                vals = item.get("values")
                if not isinstance(vals, list) or not vals:
                    problems.append(
                        f"inputs[{i}] has type 'enum' but `values` is not a "
                        f"non-empty list; got {vals!r}"
                    )
            elif has_values:
                problems.append(
                    f"inputs[{i}] carries `values` but type is "
                    f"{itype!r} (values is only valid for type 'enum')"
                )

        # `group` + entry-level `required_groups`: an input may carry a
        # `group` (a mutually-exclusive alternative set); a group named in
        # `required_groups` requires exactly one of its members. Members of a
        # required group are individually `required: false` (the group, not the
        # member, is mandatory). Validating this makes the requirement
        # machine-knowable to a consumer instead of buried in prose.
        groups: dict[str, list[int]] = {}
        for i, item in enumerate(inputs):
            if not isinstance(item, dict):
                continue
            g = item.get("group")
            if g is None:
                continue
            if not isinstance(g, str) or not g:
                problems.append(
                    f"inputs[{i}].group must be a non-empty string; got {g!r}"
                )
            else:
                groups.setdefault(g, []).append(i)

        required_groups = entry.get("required_groups")
        if required_groups is not None:
            if not isinstance(required_groups, list) or not all(
                isinstance(g, str) for g in required_groups
            ):
                problems.append(
                    f"required_groups must be a list of group-name strings; "
                    f"got {required_groups!r}"
                )
            else:
                for g in required_groups:
                    members = groups.get(g)
                    if not members:
                        problems.append(
                            f"required_groups names {g!r} but no input carries "
                            f"group: {g!r}"
                        )
                        continue
                    for i in members:
                        if inputs[i].get("required") is not False:
                            problems.append(
                                f"inputs[{i}] is in required group {g!r} and "
                                f"must be `required: false` (the group is "
                                f"required, not the individual flag)"
                            )
    return problems
