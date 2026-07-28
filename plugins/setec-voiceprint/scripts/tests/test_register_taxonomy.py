#!/usr/bin/env python3
"""Closed drop-in register-tier registry contract."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
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


def test_shipped_registry_matches_both_pins():
    """The pins must describe the registry that actually ships."""
    assert set(rt.EXPECTED_REGISTER_LEAVES) == set(EXPECTED)
    assert rt.registry_digest(EXPECTED) == rt.EXPECTED_REGISTRY_DIGEST
    rt.verify_registry_pin(rt.REGISTER_TO_TIER)


def test_pin_rejects_a_short_registry():
    """A deleted or case-renamed fragment must not load quietly.

    ``pathlib.glob("*.json")`` is case-sensitive on POSIX while APFS is not,
    so ``message.imessage.JSON`` reads as a rename to the loader. Either way
    the leaf drops out of ``PROFILE_ONLY_REGISTERS`` and the pooled-reference
    guard stops seeing it, so the loss has to be loud.
    """
    short = {k: v for k, v in EXPECTED.items() if k != "message.imessage"}
    with pytest.raises(ValueError, match=r"pinned leaf set.*message\.imessage"):
        rt.verify_registry_pin(short)


def test_pin_rejects_an_extra_unpinned_leaf():
    with pytest.raises(ValueError, match=r"extra=\['message\.smuggled'\]"):
        rt.verify_registry_pin({**EXPECTED, "message.smuggled": "public_composed"})


def test_pin_rejects_a_retiered_leaf_the_key_sets_cannot_see():
    """Closure compares key sets; only the digest covers the tier VALUES."""
    tampered = {**EXPECTED, "message.imessage": "public_composed"}
    # The closure check is blind to this: the key sets are identical.
    rt.validate_registry_closure(set(tampered))
    with pytest.raises(ValueError, match="TIER VALUE changed") as excinfo:
        rt.verify_registry_pin(tampered)
    message = str(excinfo.value)
    # The error has to be actionable: it names the surviving private-dyadic
    # set and the paste-ready replacement digest.
    assert "private_dyadic=['message.facebook_messenger']" in message
    assert rt.registry_digest(tampered) in message
    assert "EXPECTED_REGISTRY_DIGEST" in message


def test_digest_is_order_independent_but_value_sensitive():
    reordered = dict(reversed(list(EXPECTED.items())))
    assert rt.registry_digest(reordered) == rt.registry_digest(EXPECTED)
    for leaf in ("message.imessage", "blog_essay", "personal"):
        changed = {**EXPECTED, leaf: "public_responsive"}
        if changed[leaf] == EXPECTED[leaf]:
            continue
        assert rt.registry_digest(changed) != rt.registry_digest(EXPECTED)


def test_pin_is_enforced_on_the_bare_import_path(tmp_path: Path):
    """Every consumer must get the tripwire, not just manifest_validator's.

    ``validate_registry_closure(ALLOWED_REGISTER)`` runs as an import-time side
    effect of ``manifest_validator``, which ``voice_distance``,
    ``voice_profile``, ``general_imposters`` and most other ``stylometry_core``
    consumers never import. Importing ``register_taxonomy`` alone, against a
    short registry, has to fail on its own.
    """
    scripts = Path(rt.__file__).resolve().parent
    registry = tmp_path / "register_tiers.d"
    for register, tier in EXPECTED.items():
        if register == "message.imessage":
            continue
        _fragment(registry, register, tier)
    (tmp_path / "scripts").mkdir()
    shutil.copy(scripts / "register_taxonomy.py", tmp_path / "scripts")

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(tmp_path / 'scripts')!r})\n"
        "import register_taxonomy\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "pinned leaf set" in result.stderr
    assert "message.imessage" in result.stderr


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


def test_loader_rejects_a_fifo_promptly(tmp_path: Path):
    """A FIFO must be rejected, not blocked on.

    ``_read_fragment`` checks ``S_ISREG`` after ``os.open``, and a blocking
    ``O_RDONLY`` open on a FIFO waits for a writer forever — hanging every
    import of the module. ``O_NONBLOCK`` lets the open return so the
    regular-file check can run.
    """
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs unavailable on this host")
    fifo = tmp_path / "message.demo.json"
    os.mkfifo(fifo)

    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(rt.load_register_tiers(tmp_path))
        except BaseException as exc:  # noqa: BLE001 - recorded for assertion
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=20)
    # A daemon thread blocked in os.open cannot be cancelled; a live thread
    # here IS the hang, so assert on liveness rather than trying to join it.
    assert not worker.is_alive(), "load_register_tiers blocked on a FIFO"
    assert isinstance(outcome[0], ValueError)
    assert "regular file" in str(outcome[0])


def test_loader_rejects_a_directory_named_like_a_fragment(tmp_path: Path):
    (tmp_path / "message.demo.json").mkdir()
    with pytest.raises(ValueError, match="regular file|unreadable"):
        rt.load_register_tiers(tmp_path)


def test_resolve_normalizes_a_str_subclass_instead_of_failing_open():
    """The ``_entry_register``/``resolve_register_tier`` seams must agree.

    ``stylometry_core._entry_register`` admits a register via ``isinstance``,
    so a ``str`` subclass can reach the lookup. A strict ``type(...) is str``
    test resolved it to ``None``, which the pooling guard reads as benign; a
    bare ``isinstance`` lookup would instead let an overridden
    ``__hash__``/``__eq__`` pick the tier. Normalizing through the base ``str``
    payload is the only option that resolves the real leaf.
    """

    class Hostile(str):
        def __eq__(self, other: object) -> bool:
            return True

        def __hash__(self) -> int:
            return hash("blog_essay")

    assert rt.resolve_register_tier(Hostile("message.imessage")) == "private_dyadic"
    assert rt.resolve_register_tier(str("message.imessage")) == "private_dyadic"
    assert rt.resolve_register_tier(Hostile("not.registered")) is None
    assert rt.resolve_register_tier(b"message.imessage") is None
    assert rt.resolve_register_tier(None) is None


def test_loader_rejects_duplicate_json_keys(tmp_path: Path):
    target = tmp_path / "message.demo.json"
    target.write_text(
        '{"register":"message.demo","register":"message.other",'
        '"tier":"private_dyadic"}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        rt.load_register_tiers(tmp_path)
