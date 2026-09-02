#!/usr/bin/env python3
"""fetch_brysbaert.py — re-download and convert Brysbaert concreteness norms.

Companion to `scripts/concreteness.py`. SETEC does not distribute
`data/brysbaert_concreteness.csv`; this explicit opt-in script
downloads and converts the upstream Springer source for a user's
local use. The user is responsible for the source terms.

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
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import concreteness  # type: ignore


_SOURCE_URL = (
    "https://static-content.springer.com/esm/"
    "art%3A10.3758%2Fs13428-013-0403-5/MediaObjects/"
    "13428_2013_403_MOESM1_ESM.xlsx"
)


# Output CSV schema; matches the ignored locally generated CSV at
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


# Minimum data rows a converted Brysbaert CSV must carry to be accepted.
# There is ONE floor, and the loader owns it: `concreteness.MIN_USABLE_ROWS`
# is what decides whether an installed file is usable, so the fetcher must
# not install anything the loader would then reject (nor accept something
# the loader would). The published 2014 dataset has 39,954 rows; the floor
# sits well below that so an upstream revision does not fail the fetcher,
# while a truncated write (Ctrl-C, disk-full, a non-float Conc.M cell
# aborting the stream) cannot leave a plausible-looking partial file at the
# target path.
MIN_CONVERTED_ROWS = concreteness.MIN_USABLE_ROWS


def convert_xlsx_to_csv(
    xlsx_path: Path,
    csv_path: Path,
    *,
    min_rows: int = MIN_CONVERTED_ROWS,
) -> int:
    """Convert the Brysbaert XLSX to the framework's CSV schema.

    Returns the number of data rows written. The output schema is
    fixed (matches `data/brysbaert_concreteness.csv`); the input
    is expected to follow Brysbaert 2014's published layout (Sheet1
    with columns Word / Bigram / Conc.M / Conc.SD / Unknown / Total
    / Percent_known / SUBTLEX).

    The write is atomic and validated on BOTH axes the loader validates,
    using the loader's own constants, so this function cannot install a
    file ``concreteness`` would then reject:

      * **values** — every non-empty ``Conc.M`` cell must satisfy
        ``concreteness.is_valid_rating`` (finite, within
        ``CONC_SCALE_MIN``..``CONC_SCALE_MAX``). One bad cell aborts the
        conversion. Without this the converter happily installed, say,
        12,001 rows containing a 7.5, which the loader reported as
        ``data_malformed`` — and the malformed guidance used to name this
        very command as the remedy, a loop.
      * **cardinality** — the count of DISTINCT usable words (keyed by
        ``concreteness.rating_key``, i.e. lowercased) must clear
        ``min_rows``, which defaults to the loader's own floor. Rows whose
        ``Conc.M`` is empty are written but do not count, exactly as the
        loader does not count them; and duplicated rows count ONCE, exactly
        as they collapse in the loader's dict. Counting rows instead let a
        duplicated upstream table (12,000 rows, 6,000 distinct words)
        install and then read as ``data_malformed``.

    Rows stream into a temp file in ``csv_path``'s own directory and are
    moved onto ``csv_path`` with ``os.replace`` ONLY after both checks
    pass. A Ctrl-C, a full disk, a bad cell, or a short table therefore
    leaves the previous file (or no file) untouched.

    Returns the number of data rows written.
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
    fd, tmp_name = tempfile.mkstemp(
        dir=str(csv_path.parent), prefix=csv_path.name + ".", suffix=".part",
    )
    tmp_path = Path(tmp_name)
    n_data = 0
    usable_keys: set[str] = set()
    try:
        with open(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(_OUT_HEADER)
            for row in rows:
                if row[0] is None:
                    continue
                word, bigram, conc_m, conc_sd, unk, total, pct, subtlex = row
                if conc_m is not None:
                    try:
                        rating = float(conc_m)
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"{xlsx_path}: row for {word!r} carries a "
                            f"non-numeric Conc.M {conc_m!r}; refusing to "
                            f"install it at {csv_path}"
                        ) from None
                    if not concreteness.is_valid_rating(rating):
                        raise ValueError(
                            f"{xlsx_path}: row for {word!r} carries rating "
                            f"{conc_m!r}, which is not a finite value on the "
                            f"documented {concreteness.CONC_SCALE_MIN}-"
                            f"{concreteness.CONC_SCALE_MAX} scale; refusing to "
                            f"install it at {csv_path}"
                        )
                    key = concreteness.rating_key(str(word))
                    if key:
                        usable_keys.add(key)
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
            f.flush()
            os.fsync(f.fileno())
        if len(usable_keys) < min_rows:
            raise ValueError(
                f"{xlsx_path}: converted only {len(usable_keys):,} distinct "
                f"usable word(s) from {n_data:,} row(s) (expected at least "
                f"{min_rows:,}); refusing to install a partial concreteness "
                f"table at {csv_path}"
            )
        os.replace(tmp_path, csv_path)
    except BaseException:
        # Ctrl-C included: never leave the temp file, and never leave a
        # partial table at the target path.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return n_data


def _is_conventional_path(output: Path) -> bool:
    """True when ``output`` is the install path the loader reads by default."""
    try:
        return output.resolve() == concreteness._DEFAULT_DATA_PATH.resolve()
    except OSError:
        return False


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
        "--min-rows", type=int, default=MIN_CONVERTED_ROWS, metavar="N",
        help=(
            "Refuse to install the converted CSV unless it carries at least "
            f"N usable rating rows (default: {MIN_CONVERTED_ROWS:,}, which is "
            "concreteness.MIN_USABLE_ROWS — the loader's own floor). The guard "
            "is what stops an interrupted or truncated conversion from leaving "
            "a header-only table at the install path. Lowering it below the "
            "loader floor is REFUSED for the conventional install path, "
            "because the result would be a file the loader reports as "
            "data_malformed; it is allowed for any other --output path, which "
            "is the bring-your-own / experiment seam."
        ),
    )
    parser.add_argument(
        "--keep-xlsx", action="store_true",
        help=(
            "On a SUCCESSFUL conversion, keep the downloaded XLSX next "
            "to the output CSV (default: deleted after conversion). A "
            "failed conversion always deletes it."
        ),
    )
    args = parser.parse_args(argv)

    # Clamp: a floor below the loader's own would install a file the loader
    # rejects at the conventional path — the exact "fetcher and loader can
    # disagree" gap this guard exists to close. Any other --output path is
    # the bring-your-own / experiment seam and keeps the override.
    if args.min_rows < concreteness.MIN_USABLE_ROWS and _is_conventional_path(
        args.output
    ):
        print(
            f"error: --min-rows {args.min_rows:,} is below the loader's floor "
            f"({concreteness.MIN_USABLE_ROWS:,}), so the installed file would "
            f"be reported as {concreteness.DATA_MALFORMED} at the conventional "
            f"path {args.output}. Pass a different --output to experiment.",
            file=sys.stderr,
        )
        return 2

    xlsx_path = download_xlsx(url=args.source_url)
    try:
        n = convert_xlsx_to_csv(xlsx_path, args.output, min_rows=args.min_rows)
    except BaseException:
        # Conversion failed (or was interrupted): the raw XLSX is not a
        # deliverable, so it is always discarded — --keep-xlsx keeps the
        # source of a SUCCESSFUL conversion, never the debris of a failed
        # one. (Previously the rename lived in a `finally`, so a failed run
        # still parked the publisher's XLSX beside the CSV.)
        try:
            xlsx_path.unlink()
        except OSError:
            pass
        raise
    if args.keep_xlsx:
        kept = args.output.with_suffix(".xlsx")
        shutil.move(str(xlsx_path), str(kept))
        print(f"Kept XLSX at {kept}", file=sys.stderr)
    else:
        try:
            xlsx_path.unlink()
        except OSError:
            pass
    print(
        f"Wrote {n:,} concreteness rows to {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
