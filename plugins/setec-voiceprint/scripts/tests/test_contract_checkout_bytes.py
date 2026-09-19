"""Git checkout preserves the producer bytes that consumers vendor and hash."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
CLIENT = "plugins/setec-voiceprint/scripts/setec/consumer_client.py"
FIXTURES = "plugins/setec-voiceprint/references/contract_fixtures"


def _git(cwd: Path, env: dict[str, str], *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout


def test_contract_paths_checkout_as_raw_git_blobs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"
    source.mkdir()

    # An isolated Git home keeps machine configuration out of both staging and
    # checkout. The real attributes file supplies the rule under test.
    git_home = tmp_path / "git-home"
    git_home.mkdir()
    env = os.environ.copy()
    env.pop("GIT_CONFIG_PARAMETERS", None)
    env.update(
        HOME=str(git_home),
        XDG_CONFIG_HOME=str(git_home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_COUNT="0",
        GIT_ATTR_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
    )
    _git(tmp_path, env, "init", "-q", str(source))
    _git(source, env, "config", "user.name", "Contract checkout test")
    _git(source, env, "config", "user.email", "contract-checkout@example.invalid")
    _git(source, env, "config", "core.autocrlf", "false")
    shutil.copyfile(REPO_ROOT / ".gitattributes", source / ".gitattributes")

    authored = {
        CLIENT: b"def client():\n    return 1\n",
        f"{FIXTURES}/checkout.json": b'{"fixture":"CRLF"}\r\n',
        f"{FIXTURES}/checkout.bin": b"\x00binary\r\n",
        "plugins/setec-voiceprint/scripts/setec/checkout_control.py": (
            b"def ordinary():\n    return 2\n"
        ),
        "plugins/setec-voiceprint/references/checkout_control.json": (
            b'{"ordinary":true}\n'
        ),
    }
    for relative_path, payload in authored.items():
        destination = source / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

    _git(source, env, "add", ".gitattributes", *authored)
    _git(source, env, "commit", "-qm", "Synthetic contract checkout")

    # Ordinary git add must retain every authored byte. This catches a text
    # eol=lf rule normalizing the CRLF fixture before checkout even begins.
    blobs = {
        relative_path: _git(source, env, "show", f"HEAD:{relative_path}")
        for relative_path in authored
    }
    for relative_path, payload in authored.items():
        assert blobs[relative_path] == payload, relative_path

    _git(tmp_path, env, "-c", "core.autocrlf=true", "clone", "--no-local", str(source), str(checkout))

    for relative_path in (CLIENT, f"{FIXTURES}/checkout.json", f"{FIXTURES}/checkout.bin"):
        assert (checkout / relative_path).read_bytes() == blobs[relative_path], relative_path

    for relative_path in (
        "plugins/setec-voiceprint/scripts/setec/checkout_control.py",
        "plugins/setec-voiceprint/references/checkout_control.json",
    ):
        checked_out = (checkout / relative_path).read_bytes()
        assert checked_out == blobs[relative_path].replace(b"\n", b"\r\n"), relative_path
        assert checked_out != blobs[relative_path], relative_path
