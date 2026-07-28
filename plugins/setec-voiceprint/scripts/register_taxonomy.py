#!/usr/bin/env python3
"""Closed leaf-register to audience/composition-tier registry.

The leaf vocabulary records provenance-specific register names. The tier
vocabulary is the smaller policy axis used by pooled-reference guards and
evaluation stratification. It is deliberately separate from the
receipt-frozen ``register_families/v2`` classifier taxonomy.

The canonical registry is drop-in: one JSON object per leaf under
``register_tiers.d/``. There is no fallback tier. Callers that present a
missing or unknown leaf receive ``None`` and must decide how to fail closed.

Maintaining the pins
--------------------
The loaded registry is checked against two pins (``EXPECTED_REGISTER_LEAVES``
and ``EXPECTED_REGISTRY_DIGEST``) at import time, on every import path. A
legitimate registry change therefore has to update the matching pin in the
same commit as the fragment. That is deliberate — retiering a leaf out of
``private_dyadic`` turns off a privacy guard, and doing so should not be
possible by editing one JSON file. There is no bypass flag; a bypass would
reintroduce exactly the fail-open the pins exist to close.

To update: make the fragment change, then run anything that imports this
module — ``test_register_taxonomy.py`` alone is enough — and copy the
``actual`` values out of the raised ``ValueError``. It reports the offending
leaves and the replacement digest verbatim, in paste-ready form, so no
separate tool is needed to recompute a pin.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from types import MappingProxyType
from typing import AbstractSet, Any, Mapping


REGISTER_TIERS = frozenset({
    "public_composed",
    "public_responsive",
    "private_composed",
    "private_dyadic",
})
PRIVATE_DYADIC_TIER = "private_dyadic"
REGISTRY_PATH = Path(__file__).resolve().parents[1] / "register_tiers.d"
MAX_FRAGMENT_BYTES = 1024

# ---------- Pinned registry integrity ----------
#
# Loading is fail-open by shape. A fragment that is deleted, renamed (POSIX
# ``glob("*.json")`` is case-sensitive while APFS is not), or retiered leaves a
# registry that is internally consistent and merely SHORT or WRONG. A leaf that
# drops out resolves to ``None``, disappears from ``PROFILE_ONLY_REGISTERS``,
# and the pooled-reference guard in ``stylometry_core`` reads ``None`` as
# benign — so private-dyadic material pools with essay prose and nothing
# raises.
#
# ``validate_registry_closure`` cannot close that on its own: it runs as an
# import-time side effect of ``manifest_validator``, and most
# ``stylometry_core`` consumers (``voice_distance``, ``voice_profile``,
# ``general_imposters``, and ~20 others) never import that module. So the
# registry validates ITSELF below, immediately after the load.
#
# The leaf-set pin catches a short or over-long registry and names the exact
# leaves. The digest pin additionally covers the tier VALUES, which a leaf-set
# or key-set comparison is blind to. See the module docstring for how to
# update them.
EXPECTED_REGISTER_LEAVES = frozenset({
    "academic_philosophy",
    "blog_essay",
    "expert_affidavit",
    "forum_metafilter",
    "grant_proposal",
    "legal_brief",
    "literary_fiction",
    "literary_horror",
    "message.facebook_messenger",
    "message.imessage",
    "personal",
    "policy_advocacy",
    "policy_brief",
    "professional_letter",
    "regulatory_comment",
    "scholarly_article",
    "social_media_facebook_comments",
    "social_media_facebook_posts",
    "social_media_twitter",
    "teaching",
    "testimony_policy",
})
EXPECTED_REGISTRY_DIGEST = (
    "sha256:4fdaf0252ab3a8787fa0bcc7cf62ee2345a8543b4b7f213e30a191cad35920a4"
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _read_fragment(fragment: Path) -> bytes:
    """Read one bounded regular file without following symlinks on POSIX."""
    if fragment.is_symlink():
        raise ValueError(f"{fragment}: fragment must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    # A blocking O_RDONLY open on a FIFO waits for a writer forever, which
    # would hang every import of this module before the S_ISREG check below
    # ever runs. O_NONBLOCK makes the open return immediately so the
    # regular-file check can reject it; it is a no-op for regular files.
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(fragment, flags)
    except OSError as exc:
        raise ValueError(f"{fragment}: unreadable register-tier fragment") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{fragment}: fragment must be a regular file")
        if info.st_size > MAX_FRAGMENT_BYTES:
            raise ValueError(f"{fragment}: register-tier fragment is too large")
        chunks: list[bytes] = []
        remaining = MAX_FRAGMENT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_FRAGMENT_BYTES:
            raise ValueError(f"{fragment}: register-tier fragment is too large")
        return raw
    finally:
        os.close(descriptor)


def load_register_tiers(path: Path = REGISTRY_PATH) -> Mapping[str, str]:
    """Load and validate one ``<register>.json`` fragment per leaf."""
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"register-tier registry directory missing: {path}")

    mapping: dict[str, str] = {}
    fragments = sorted(path.glob("*.json"))
    if not fragments:
        raise ValueError(f"register-tier registry is empty: {path}")

    for fragment in fragments:
        try:
            raw = _read_fragment(fragment)
            text = raw.decode("utf-8")
            value: Any = json.loads(
                text, object_pairs_hook=_reject_duplicate_keys
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"{fragment}: unreadable register-tier fragment"
            ) from exc
        if type(value) is not dict or set(value) != {"register", "tier"}:
            raise ValueError(
                f"{fragment}: fragment must contain exactly register and tier"
            )
        register = value["register"]
        tier = value["tier"]
        if type(register) is not str or not register:
            raise ValueError(f"{fragment}: register must be a non-empty string")
        if register != fragment.stem:
            raise ValueError(
                f"{fragment}: register {register!r} must match filename stem "
                f"{fragment.stem!r}"
            )
        if type(tier) is not str or tier not in REGISTER_TIERS:
            raise ValueError(f"{fragment}: unknown register tier {tier!r}")
        if register in mapping:
            raise ValueError(f"{fragment}: duplicate register {register!r}")
        mapping[register] = tier

    return MappingProxyType(mapping)


def canonical_registry_bytes(mapping: Mapping[str, str]) -> bytes:
    """Serialize the whole leaf-to-tier mapping to order-independent bytes."""
    return json.dumps(
        dict(sorted(mapping.items())),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\n"


def registry_digest(mapping: Mapping[str, str]) -> str:
    """Return the prefixed digest of the canonical mapping bytes."""
    return "sha256:" + hashlib.sha256(canonical_registry_bytes(mapping)).hexdigest()


def verify_registry_pin(mapping: Mapping[str, str]) -> None:
    """Refuse a registry that is not byte-for-byte the pinned registry."""
    leaves = set(mapping)
    actual = registry_digest(mapping)
    if leaves != set(EXPECTED_REGISTER_LEAVES):
        missing = sorted(set(EXPECTED_REGISTER_LEAVES) - leaves)
        extra = sorted(leaves - set(EXPECTED_REGISTER_LEAVES))
        raise ValueError(
            "register-tier registry does not match the pinned leaf set "
            f"(missing={missing!r}, extra={extra!r}). A short registry "
            "silently disables the private-dyadic pooling guard. If this "
            "change is intended, update EXPECTED_REGISTER_LEAVES and set "
            f"EXPECTED_REGISTRY_DIGEST = {actual!r} in register_taxonomy.py."
        )
    if actual != EXPECTED_REGISTRY_DIGEST:
        profile_only = sorted(
            register
            for register, tier in mapping.items()
            if tier == PRIVATE_DYADIC_TIER
        )
        raise ValueError(
            "register-tier registry has the pinned leaf set but at least one "
            f"TIER VALUE changed (expected {EXPECTED_REGISTRY_DIGEST}, actual "
            f"{actual}). Retiering a leaf out of {PRIVATE_DYADIC_TIER!r} "
            "disables the pooled-reference guard and the profile-only "
            "manifest rejection; the loaded registry now declares "
            f"{PRIVATE_DYADIC_TIER}={profile_only!r}. If this change is "
            f"intended, set EXPECTED_REGISTRY_DIGEST = {actual!r} in "
            "register_taxonomy.py."
        )


REGISTER_TO_TIER = load_register_tiers()
verify_registry_pin(REGISTER_TO_TIER)
PROFILE_ONLY_REGISTERS = frozenset(
    register
    for register, tier in REGISTER_TO_TIER.items()
    if tier == PRIVATE_DYADIC_TIER
)


def resolve_register_tier(register: object) -> str | None:
    """Return the declared tier, or ``None`` for missing/unknown leaves."""
    if not isinstance(register, str):
        return None
    # ``stylometry_core._entry_register`` admits a register via ``isinstance``,
    # so a ``str`` SUBCLASS can reach this lookup. Resolving it through the
    # base ``str`` payload keeps the two seams agreed and denies a subclass any
    # say in the result: a strict ``type(...) is str`` test would fail open to
    # ``None`` (read as benign by the pooling guard), while a bare
    # ``isinstance`` lookup would let an overridden ``__hash__``/``__eq__``
    # choose which tier the leaf resolves to.
    return REGISTER_TO_TIER.get(str.__str__(register))


def validate_registry_closure(allowed_registers: AbstractSet[str]) -> None:
    """Require an exact one-to-one registry cell for every allowed leaf."""
    allowed = set(allowed_registers)
    registered = set(REGISTER_TO_TIER)
    if allowed != registered:
        missing = sorted(allowed - registered)
        extra = sorted(registered - allowed)
        raise ValueError(
            "register-tier registry is not closed over ALLOWED_REGISTER "
            f"(missing={missing!r}, extra={extra!r})"
        )
