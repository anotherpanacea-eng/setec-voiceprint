#!/usr/bin/env python3
"""fetch_pan24_voightkampff.py

Local-only fetcher for the PAN@CLEF 2024 "Voight-Kampff" Generative-AI
Authorship Verification bootstrap corpus (spec 04 / `pan_replay`). Models
`fetch_pangram_editlens.py`: download into `ai-prose-baselines-private/pan24/`
(gitignored, never committed) and write a NOTICE.md with attribution +
the no-redistribution term.

Source: Zenodo record 10718757 (DOI 10.5281/zenodo.10718757),
  file `pan24-generative-authorship-news.zip` (~12 MB, openly downloadable).
Terms (Zenodo): "Copyrighted Material ... may be used only for research
  purposes. No redistribution allowed." → SETEC treats it LOCAL-ONLY: the
  corpus stays in ai-prose-baselines-private/ (the whole dir is gitignored),
  never committed; only aggregate measurements may ship in code.

Usage:  py -3.12 fetch_pan24_voightkampff.py [--refresh] [--dest DIR]
"""
from __future__ import annotations
import argparse, sys, urllib.request, zipfile
from pathlib import Path

ZENODO_URL = "https://zenodo.org/records/10718757/files/pan24-generative-authorship-news.zip?download=1"
DOI = "10.5281/zenodo.10718757"

# Same REPO_ROOT idiom as fetch_pangram_editlens.py: scripts ship at
# ``<repo>/plugins/setec-voiceprint/scripts/calibration/foo.py``, so parents[4]
# is the repo root in dev and the marketplace root in an installed copy. The
# private corpus dir is that root's sibling-of-repo neighbour either way — never
# a machine-local absolute path (those don't travel across the fleet).
REPO_ROOT = Path(__file__).resolve().parents[4]
PRIVATE_DIR = REPO_ROOT / "ai-prose-baselines-private"
TARGET_DIR = PRIVATE_DIR / "pan24"
NOTICE = """# PAN@CLEF 2024 — Voight-Kampff Generative-AI Authorship Verification (bootstrap corpus)

- **Source:** Zenodo record {doi} — `pan24-generative-authorship-news.zip`
- **Task:** "Given two texts, one human, one machine: pick out the human." (PAN24 + ELOQUENT)
- **Terms:** Copyrighted material; **research use only; NO redistribution.**
- **SETEC posture:** LOCAL-ONLY. This corpus lives under `ai-prose-baselines-private/`
  (the entire directory is gitignored) and is NEVER committed. Used by `pan_replay`
  (spec 04) as the clean side of (clean, obfuscated) robustness pairs. Only aggregate
  measurements (no corpus rows) may appear in shipped code.
- **Fetched by:** `scripts/calibration/fetch_pan24_voightkampff.py`
"""


def _safe_extract(z: zipfile.ZipFile, dest: Path) -> list[str]:
    """Extract, refusing any member that would land outside ``dest``.

    ``extractall`` follows absolute paths and ``..`` segments in member names, so
    a malicious or malformed archive can write anywhere the process can reach.
    Zenodo is a trusted host, but the guard is cheap and the fetcher runs against
    a URL, not a vetted local file.
    """
    dest = dest.resolve()
    names = z.namelist()
    for name in names:
        target = (dest / name).resolve()
        if target != dest and dest not in target.parents:
            raise ValueError(f"refusing archive member outside destination: {name!r}")
    z.extractall(dest)
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="re-download even if present")
    ap.add_argument("--dest", default=None,
                    help=f"destination dir (default: {TARGET_DIR}, the gitignored private corpus dir)")
    a = ap.parse_args()
    dest = Path(a.dest).expanduser() if a.dest else TARGET_DIR
    dest.mkdir(parents=True, exist_ok=True)
    zip_path = dest / "pan24-generative-authorship-news.zip"
    if zip_path.exists() and not a.refresh:
        print(f"already present: {zip_path} ({zip_path.stat().st_size/1e6:.1f} MB) -- use --refresh to re-pull")
    else:
        print(f"downloading PAN24 bootstrap corpus ({DOI})...", flush=True)
        req = urllib.request.Request(ZENODO_URL, headers={"User-Agent": "Mozilla/5.0 (SETEC research fetcher)"})
        with urllib.request.urlopen(req) as r, open(zip_path, "wb") as f:
            f.write(r.read())
        print(f"  -> {zip_path} ({zip_path.stat().st_size/1e6:.1f} MB)", flush=True)
    with zipfile.ZipFile(zip_path) as z:
        names = _safe_extract(z, dest)
    print(f"  unzipped {len(names)} entries -> {dest}")
    (dest / "NOTICE.md").write_text(NOTICE.format(doi=DOI), encoding="utf-8")
    print("  wrote NOTICE.md (research-only, no-redistribution, local-only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
