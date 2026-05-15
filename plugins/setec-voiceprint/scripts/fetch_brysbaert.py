#!/usr/bin/env python3
"""fetch_brysbaert.py — re-download and convert Brysbaert concreteness norms.

Companion to `scripts/concreteness.py`. The framework ships
`data/brysbaert_concreteness.csv` in-repo so operators don't need
to refetch on install; this script regenerates the CSV from the
upstream Springer source for operators whose redistribution
context excludes the data file, or for periodic refresh against
the canonical source.

Usage::

    python3 plugins/setec-voiceprint/scripts/fetch_brysbaert.py \\
        --output plugins/setec-voiceprint/data/brysbaert_concreteness.csv

By default the script downloads to a temporary location, converts,
and writes the CSV to ``data/brysbaert_concreteness.csv`` relative
to the script's location. Pass ``--output`` to override.

Source: Brysbaert, M., Warriner, A. B., & Kuperman, V. (2014).
Concreteness ratings for 40 thousand generally known English word
lemmas. *Behavior Research Methods*, 46(3), 904-911.
https://doi.org/10.3758/s13428-013-0403-5

The supplementary data lives at Springer's static-content CDN:
https://static-content.springer.com/esm/art%3A10.3758%2Fs13428-013-0403-5/MediaObjects/13428_2013_403_MOESM1_ESM.xlsx
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


_SOURCE_URL = (
    "https://static-content.springer.com/esm/"
    "art%3A10.3758%2Fs13428-013-0403-5/MediaObjects/"
    "13428_2013_403_MOESM1_ESM.xlsx"
)


# Output CSV schema; matches the in-repo CSV at
# plugins/setec-voiceprint/data/brysbaert_concreteness.csv.
_OUT_HEADER = [
    "word",
    "is_bigram",
    "conc_mean",
    "conc_sd",
    "unknown_count",
    "total_raters",
    "percent_known",
    "subtlex_freq",
]


def download_xlsx(url: str = _SOURCE_URL, dest: Path | None = None) -> Path:
    """Download the Brysbaert XLSX to ``dest`` (or a tempfile).

    Tries in order: (1) `requests` if installed (handles certs via
    bundled certifi, most reliable on macOS Python installs);
    (2) `curl` via subprocess if installed (universal fallback);
    (3) urllib (last resort; often fails on macOS Python without
    certifi). Returns the local path. Raises ``OSError`` with a
    diagnostic message when every method fails.
    """
    if dest is None:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".xlsx", delete=False
        )
        dest = Path(tmp.name)
        tmp.close()
    print(f"Downloading {url}...", file=sys.stderr)

    # Method 1: requests (preferred). Bundled certifi handles SSL
    # cleanly on machines where the system Python lacks certs.
    try:
        import requests  # type: ignore
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        print(
            f"Wrote {len(resp.content):,} bytes to {dest} (via requests)",
            file=sys.stderr,
        )
        return dest
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 — fall through to next method
        print(
            f"requests fetch failed ({type(exc).__name__}); trying curl",
            file=sys.stderr,
        )

    # Method 2: curl via subprocess. Universal on macOS / Linux;
    # often available on Windows via WSL or git-bash.
    curl_path = shutil.which("curl")
    if curl_path:
        try:
            subprocess.run(
                [curl_path, "-sSL", "-o", str(dest), url],
                check=True, capture_output=True, timeout=120,
            )
            size = dest.stat().st_size
            print(
                f"Wrote {size:,} bytes to {dest} (via curl)",
                file=sys.stderr,
            )
            return dest
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            print(
                f"curl fetch failed ({type(exc).__name__}); trying urllib",
                file=sys.stderr,
            )

    # Method 3: urllib (fallback). Often fails on macOS Python
    # without certifi; included for completeness.
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
        dest.write_bytes(data)
        print(
            f"Wrote {len(data):,} bytes to {dest} (via urllib)",
            file=sys.stderr,
        )
        return dest
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise OSError(
            f"Failed to download Brysbaert XLSX from {url}: "
            f"{type(exc).__name__}: {exc}. "
            "Tried requests, curl, and urllib in sequence. "
            "Install requests (`pip install requests`) or check "
            "curl availability for the most reliable path."
        ) from exc


def convert_xlsx_to_csv(xlsx_path: Path, csv_path: Path) -> int:
    """Convert the Brysbaert XLSX to the framework's CSV schema.

    Returns the number of data rows written. The output schema is
    fixed (matches `data/brysbaert_concreteness.csv`); the input
    is expected to follow Brysbaert 2014's published layout (Sheet1
    with columns Word / Bigram / Conc.M / Conc.SD / Unknown / Total
    / Percent_known / SUBTLEX).
    """
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "openpyxl is not installed. Install with: "
            "pip install openpyxl"
        ) from exc
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb["Sheet1"]
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if header is None:
        raise ValueError(f"{xlsx_path}: Sheet1 is empty")
    expected = (
        "Word", "Bigram", "Conc.M", "Conc.SD", "Unknown",
        "Total", "Percent_known", "SUBTLEX",
    )
    if tuple(header) != expected:
        raise ValueError(
            f"{xlsx_path}: unexpected header {header!r}; "
            f"expected {expected!r}"
        )
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    n_data = 0
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(_OUT_HEADER)
        for row in rows:
            if row[0] is None:
                continue
            word, bigram, conc_m, conc_sd, unk, total, pct, subtlex = row
            writer.writerow([
                word,
                int(bigram) if bigram is not None else 0,
                f"{conc_m:.2f}" if conc_m is not None else "",
                f"{conc_sd:.2f}" if conc_sd is not None else "",
                int(unk) if unk is not None else "",
                int(total) if total is not None else "",
                f"{pct:.6f}" if pct is not None else "",
                int(subtlex) if subtlex is not None else 0,
            ])
            n_data += 1
    return n_data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-download and convert Brysbaert concreteness norms "
            "(40K English word lemmas, 1-5 scale; Brysbaert et al. "
            "2014, Behavior Research Methods). Outputs a CSV "
            "consumed by scripts/concreteness.py."
        ),
    )
    default_output = (
        Path(__file__).resolve().parent.parent
        / "data" / "brysbaert_concreteness.csv"
    )
    parser.add_argument(
        "--output", type=Path, default=default_output,
        help=(
            "Output CSV path. Default: "
            "plugins/setec-voiceprint/data/brysbaert_concreteness.csv "
            "(relative to the script's plugin directory)."
        ),
    )
    parser.add_argument(
        "--source-url", type=str, default=_SOURCE_URL,
        help="Override the upstream XLSX URL (rarely needed).",
    )
    parser.add_argument(
        "--keep-xlsx", action="store_true",
        help=(
            "Keep the downloaded XLSX next to the output CSV "
            "(default: deleted after conversion)."
        ),
    )
    args = parser.parse_args(argv)

    xlsx_path = download_xlsx(url=args.source_url)
    try:
        n = convert_xlsx_to_csv(xlsx_path, args.output)
    finally:
        if not args.keep_xlsx:
            try:
                xlsx_path.unlink()
            except OSError:
                pass
        elif args.keep_xlsx:
            kept = args.output.with_suffix(".xlsx")
            xlsx_path.rename(kept)
            print(f"Kept XLSX at {kept}", file=sys.stderr)
    print(
        f"Wrote {n:,} concreteness rows to {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
