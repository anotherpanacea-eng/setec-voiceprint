#!/usr/bin/env python3
"""Closed drop-in register-tier registry contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import manifest_validator as mv  # type: ignore
import register_taxonomy as rt  # type: ignore


EXPECTED = {
    "academic_philosophy": "public_composed",
    "blog_essay": "public_composed",
    "expert_affidavit": "public_composed",
    "forum_metafilter": "public_responsive",
    "grant_proposal": "public_composed",
    "legal_brief": "public_composed",
    "literary_fiction": "public_composed",
    "literary_horror": "public_composed",
    "message.facebook_messenger": "private_dyadic",
    "message.imessage": "private_dyadic",
    "personal": "private_composed",
    "policy_advocacy": "public_composed",
    "policy_brief": "public_composed",
    "professional_letter": "private_composed",
    "regulatory_comment": "public_composed",
    "scholarly_article": "public_composed",
    "social_media_facebook_comments": "public_responsive",
    "social_media_facebook_posts": "public_composed",
    "social_media_twitter": "public_responsive",
    "teaching": "public_composed",
    "testimony_policy": "public_composed",
}


def _fragment(path: Path, register: str, tier: str, *, name: str | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    target = path / f"{name or register}.json"
    target.write_text(
        json.dumps({"register": register, "tier": tier}),
        encoding="utf-8",
    )


def test_registry_is_exact_total_and_has_no_default():
    assert rt.REGISTER_TIERS == {
        "public_composed",
        "public_responsive",
        "private_composed",
        "private_dyadic",
    }
    assert dict(rt.REGISTER_TO_TIER) == EXPECTED
    assert set(rt.REGISTER_TO_TIER) == mv.ALLOWED_REGISTER
    assert rt.PROFILE_ONLY_REGISTERS == {
        "message.imessage",
        "message.facebook_messenger",
    }
    assert rt.resolve_register_tier(None) is None
    assert rt.resolve_register_tier("not.registered") is None


def test_closure_rejects_added_but_unmapped_and_extra_cells():
    with pytest.raises(ValueError, match="missing=.*new_leaf"):
        rt.validate_registry_closure(mv.ALLOWED_REGISTER | {"new_leaf"})
    with pytest.raises(ValueError, match="extra="):
        rt.validate_registry_closure(mv.ALLOWED_REGISTER - {"teaching"})


def test_loader_accepts_one_valid_dropin(tmp_path: Path):
    _fragment(tmp_path, "message.demo", "private_dyadic")
    assert dict(rt.load_register_tiers(tmp_path)) == {
        "message.demo": "private_dyadic",
    }


@pytest.mark.parametrize(
    ("payload", "name", "match"),
    [
        ({"register": "message.demo", "tier": "not_a_tier"}, None, "unknown"),
        ({"register": "message.other", "tier": "private_dyadic"}, "message.demo", "filename"),
        ({"register": "message.demo", "tier": "private_dyadic", "default": True}, None, "exactly"),
    ],
)
def test_loader_rejects_hostile_fragments(
    tmp_path: Path, payload: dict, name: str | None, match: str
):
    target = tmp_path / f"{name or payload['register']}.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        rt.load_register_tiers(tmp_path)


def test_loader_rejects_empty_registry(tmp_path: Path):
    with pytest.raises(ValueError, match="empty"):
        rt.load_register_tiers(tmp_path)


def test_loader_rejects_symlink_fragment(tmp_path: Path):
    target = tmp_path / "outside.json"
    target.write_text(
        json.dumps({"register": "message.demo", "tier": "private_dyadic"}),
        encoding="utf-8",
    )
    registry = tmp_path / "registry"
    registry.mkdir()
    try:
        (registry / "message.demo.json").symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    with pytest.raises(ValueError, match="symlink"):
        rt.load_register_tiers(registry)


def test_loader_rejects_oversized_fragment(tmp_path: Path):
    target = tmp_path / "message.demo.json"
    target.write_text(" " * (rt.MAX_FRAGMENT_BYTES + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="too large"):
        rt.load_register_tiers(tmp_path)


def test_loader_rejects_duplicate_json_keys(tmp_path: Path):
    target = tmp_path / "message.demo.json"
    target.write_text(
        '{"register":"message.demo","register":"message.other",'
        '"tier":"private_dyadic"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        rt.load_register_tiers(tmp_path)
