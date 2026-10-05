#!/usr/bin/env python3
"""Provision only the pinned English test dependency before characterization."""
import argparse
import hashlib
import io
from pathlib import Path
from urllib.request import urlopen
import zipfile

REVISION = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
ARCHIVE_SHA256 = "e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106"
FILES = ("collocations.tab", "sent_starters.txt", "abbrev_types.txt", "ortho_context.tab")
URL = f"https://raw.githubusercontent.com/nltk/nltk_data/{REVISION}/packages/tokenizers/punkt_tab.zip"


def prepare(destination, archive_bytes):
    if hashlib.sha256(archive_bytes).hexdigest() != ARCHIVE_SHA256:
        raise ValueError("Punkt archive SHA256 mismatch")
    destination = Path(destination)
    if destination.exists():
        raise ValueError("use a new isolated resource root")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        payloads = {name: archive.read("punkt_tab/english/" + name) for name in FILES}
    directory = destination / "tokenizers/punkt_tab/english"
    directory.mkdir(parents=True)
    for name, payload in payloads.items():
        (directory / name).write_bytes(payload)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--archive", type=Path, help="verify an already-downloaded official archive")
    args = parser.parse_args()
    if args.archive:
        payload = args.archive.read_bytes()
    else:
        with urlopen(URL, timeout=60) as response:
            payload = response.read(8 * 1024 * 1024 + 1)
        if len(payload) > 8 * 1024 * 1024:
            raise ValueError("unexpected archive size")
    print(prepare(args.destination, payload))
