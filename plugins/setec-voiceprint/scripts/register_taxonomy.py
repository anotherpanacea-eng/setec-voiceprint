#!/usr/bin/env python3
"""Closed leaf-register to audience/composition-tier registry.

The leaf vocabulary records provenance-specific register names. The tier
vocabulary is the smaller policy axis used by pooled-reference guards and
evaluation stratification. It is deliberately separate from the
receipt-frozen ``register_families/v2`` classifier taxonomy.

The canonical registry is drop-in: one JSON object per leaf under
``register_tiers.d/``. There is no fallback tier. Callers that present a
missing or unknown leaf receive ``None`` and must decide how to fail closed.
"""

from __future__ import annotations

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


REGISTER_TO_TIER = load_register_tiers()
PROFILE_ONLY_REGISTERS = frozenset(
    register
    for register, tier in REGISTER_TO_TIER.items()
    if tier == PRIVATE_DYADIC_TIER
)


def resolve_register_tier(register: object) -> str | None:
    """Return the declared tier, or ``None`` for missing/unknown leaves."""
    if type(register) is not str:
        return None
    return REGISTER_TO_TIER.get(register)


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
