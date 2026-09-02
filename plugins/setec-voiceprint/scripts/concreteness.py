#!/usr/bin/env python3
"""concreteness.py — Brysbaert concreteness norms loader.

Reads the per-word concreteness ratings from Brysbaert, Warriner &
Kuperman (2014) and exposes them as an O(1) lookup. The dataset
covers 39,954 English words and two-word phrases on a 1-5 scale
(5 = most concrete, 1 = most abstract).

This module is foundation infrastructure for the AIC-8 family
(`SPEC_aic_8_9_implementation.md` Step 1). The image-conjunction
detector (`image_conjunction.py`) and the prestige-metaphor detector
(`prestige_metaphor.py`) both read concreteness scores through this
loader. The framework does not threshold concreteness on its own;
the value lies in the **gap** between two words' concreteness
ratings combined with their semantic distance.

Citation: Brysbaert, M., Warriner, A. B., & Kuperman, V. (2014).
Concreteness ratings for 40 thousand generally known English word
lemmas. *Behavior Research Methods*, 46(3), 904-911.
https://doi.org/10.3758/s13428-013-0403-5

Cache location: `plugins/setec-voiceprint/data/brysbaert_concreteness.csv`.
The CSV is optional and is not distributed with the framework. If absent, an
individual user may obtain it from its publisher via `scripts/fetch_brysbaert.py`.

Design notes:

  * **Lazy load, cached.** The CSV is ~1.5 MB and loads into a
    dict of ~40K entries. Loading takes ~100ms; subsequent lookups
    are O(1). The loader caches the dict at module level so
    repeated `get_concreteness()` calls don't re-read the file.
  * **Presence is not availability.** ``is_available()`` validates the
    file's CONTENTS, not just that it opens: required columns present
    and at least ``MIN_USABLE_ROWS`` parsed ratings. A 0-byte,
    header-only, or all-empty-``conc_mean`` CSV is reported unavailable
    (``data_malformed``) instead of loading to ``{}`` and letting the
    detectors emit a confident 0.0.
  * **Unknown words return None, not zero.** A zero concreteness
    would be a falsy interpretable value (extremely abstract);
    `None` is the typed missing-data signal. Callers handle the
    None case explicitly (skip the pair, fall back to a register
    default, or treat as unknown).
  * **Case-insensitive lookups.** Brysbaert lowercases all entries.
    The loader does too; callers don't need to pre-lowercase.
  * **Bigram support.** The dataset includes two-word phrases
    (e.g., "zero tolerance", "zip code"). `get_concreteness("zero
    tolerance")` works as expected. Single-word lookups don't
    accidentally match bigrams because the dict keys are the full
    `word` field.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Optional

# Default cache path. Resolves to
# plugins/setec-voiceprint/data/brysbaert_concreteness.csv when the
# module is imported from anywhere inside the plugin tree.
_DEFAULT_DATA_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "brysbaert_concreteness.csv"
)

# Reason codes for "the optional dataset is not usable". They are distinct
# on purpose: the operator fix differs per code, and reporting
# ``data_not_installed`` for a file that IS installed but corrupt sends the
# operator to re-run the fetcher on top of a file the fetcher already wrote.
DATA_NOT_INSTALLED = "data_not_installed"
DATA_MALFORMED = "data_malformed"
DATA_UNREADABLE = "data_unreadable"

# Columns the loader needs. A CSV missing either one is malformed, not
# missing — re-fetching is the wrong advice.
REQUIRED_COLUMNS = ("word", "conc_mean")

# Content floor for "available". A file that opens but yields no usable
# (word, rating) pair -- a 0-byte file, a header-only file, an all-empty
# ``conc_mean`` column -- is NOT a dataset. Reporting it as available made
# every AIC-8 detector emit value 0.0 / status provisional and let
# variance_audit band it "within typical range": a fail-OPEN.
MIN_USABLE_ROWS = 1

MISSING_DATA_GUIDANCE = (
    "Optional Brysbaert concreteness data is not installed. Fetch it explicitly "
    "for local use with: python3 "
    "plugins/setec-voiceprint/scripts/fetch_brysbaert.py"
)
MALFORMED_DATA_GUIDANCE = (
    "Optional Brysbaert concreteness data is present but unusable: it is missing "
    "the required word/conc_mean columns, carries no usable rating rows, or is "
    "not valid UTF-8 CSV. Inspect or delete the file before regenerating it "
    "with: python3 plugins/setec-voiceprint/scripts/fetch_brysbaert.py"
)
UNREADABLE_DATA_GUIDANCE = (
    "Optional Brysbaert concreteness data is present but could not be read "
    "(permissions, or a directory in place of the file). Fix the path or its "
    "permissions; re-running "
    "plugins/setec-voiceprint/scripts/fetch_brysbaert.py will not help."
)

_REASON_GUIDANCE = {
    DATA_NOT_INSTALLED: MISSING_DATA_GUIDANCE,
    DATA_MALFORMED: MALFORMED_DATA_GUIDANCE,
    DATA_UNREADABLE: UNREADABLE_DATA_GUIDANCE,
}


class ConcretenessDataError(RuntimeError):
    """The optional CSV is present but unusable.

    Carries the machine-readable ``reason`` (``data_malformed`` or
    ``data_unreadable``) so a caller names it in an envelope without
    string-matching the message.
    """

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def guidance_for(reason: Optional[str]) -> str:
    """Human guidance for an unavailability ``reason``.

    An unrecognized reason (e.g. variance_audit's "AIC-8 modules
    unimportable: ...") is returned verbatim, so a summary line can print
    EVERY unavailable reason rather than only the missing-file one.
    """
    if reason is None:
        return ""
    return _REASON_GUIDANCE.get(reason, reason)


@lru_cache(maxsize=1)
def _load_concreteness_dict(path: str = "") -> dict[str, float]:
    """Load the concreteness CSV into a {word: conc_mean} dict.

    The path argument is a string (not Path) so ``lru_cache`` can
    hash it. Pass an empty string for the default location.

    Raises ``FileNotFoundError`` with operator-facing guidance when
    the CSV is missing — the message names the fetcher as the fix.

    Raises ``ConcretenessDataError`` when the file is PRESENT but
    unusable, with a distinct ``reason``:

      * ``data_unreadable`` — the path could not be opened at all
        (permissions, a directory in place of the file).
      * ``data_malformed`` — it opened but is not this dataset: the
        required ``word`` / ``conc_mean`` columns are absent, the bytes
        are not UTF-8, or it yields fewer than ``MIN_USABLE_ROWS``
        usable (word, rating) pairs. A 0-byte file, a header-only file,
        and an all-empty ``conc_mean`` column all land here rather than
        loading to ``{}`` and reporting success.
    """
    csv_path = Path(path) if path else _DEFAULT_DATA_PATH
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Brysbaert concreteness CSV not found at {csv_path}. "
            "Regenerate via: python3 "
            "plugins/setec-voiceprint/scripts/fetch_brysbaert.py "
            f"--output {csv_path}"
        )
    result: dict[str, float] = {}
    try:
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            missing = [c for c in REQUIRED_COLUMNS if c not in fieldnames]
            if missing:
                raise ConcretenessDataError(
                    f"Brysbaert concreteness CSV at {csv_path} is missing "
                    f"required column(s) {missing!r}; found {fieldnames!r}. "
                    f"{MALFORMED_DATA_GUIDANCE}",
                    DATA_MALFORMED,
                )
            for row in reader:
                raw_word = row.get("word")
                if not raw_word:
                    continue
                try:
                    conc = float(row["conc_mean"])
                except (KeyError, ValueError, TypeError):
                    continue
                result[raw_word.lower()] = conc
    except FileNotFoundError:
        # Raced away between exists() and open(): still "not installed".
        raise
    except UnicodeDecodeError as exc:
        raise ConcretenessDataError(
            f"Brysbaert concreteness CSV at {csv_path} is not valid UTF-8 "
            f"({exc}). {MALFORMED_DATA_GUIDANCE}",
            DATA_MALFORMED,
        ) from exc
    except OSError as exc:
        raise ConcretenessDataError(
            f"Brysbaert concreteness CSV at {csv_path} could not be read "
            f"({type(exc).__name__}: {exc}). {UNREADABLE_DATA_GUIDANCE}",
            DATA_UNREADABLE,
        ) from exc
    if len(result) < MIN_USABLE_ROWS:
        raise ConcretenessDataError(
            f"Brysbaert concreteness CSV at {csv_path} carries "
            f"{len(result)} usable rating row(s); at least "
            f"{MIN_USABLE_ROWS} is required. {MALFORMED_DATA_GUIDANCE}",
            DATA_MALFORMED,
        )
    return result


def get_concreteness(
    word: str, data_path: Optional[Path | str] = None,
) -> Optional[float]:
    """Return the mean concreteness rating for ``word``, or ``None``.

    Concreteness is on a 1-5 scale (5 = most concrete; 1 = most
    abstract). Returns ``None`` for words not in the Brysbaert
    dataset — callers must handle the None case (skip the pair,
    treat as unknown, or fall back to a domain default).

    Case-insensitive. Multi-word phrases (e.g., "zero tolerance")
    work if the full phrase is in the dataset.

    ``data_path`` overrides the default CSV location; useful for
    tests with synthetic fixtures.
    """
    path_str = str(data_path) if data_path else ""
    table = _load_concreteness_dict(path_str)
    return table.get(word.lower())


def concreteness_gap(
    word_a: str, word_b: str, data_path: Optional[Path | str] = None,
) -> Optional[float]:
    """Return ``|concreteness(a) - concreteness(b)|`` or ``None``.

    The gap is the core AIC-8 image-conjunction signal: large gaps
    pair abstract words with concrete words ("the machinery of
    grief": machinery ≈ 4.9, grief ≈ 1.5, gap ≈ 3.4). Returns
    ``None`` if either word is missing from the dataset; the caller
    decides how to handle missing inputs.
    """
    a = get_concreteness(word_a, data_path)
    b = get_concreteness(word_b, data_path)
    if a is None or b is None:
        return None
    return abs(a - b)


def vocab_size(data_path: Optional[Path | str] = None) -> int:
    """Return the number of entries in the loaded concreteness table.

    Useful for diagnostics; the canonical Brysbaert 2014 dataset
    has 39,954 entries.
    """
    path_str = str(data_path) if data_path else ""
    return len(_load_concreteness_dict(path_str))


def availability_reason(data_path: Optional[Path | str] = None) -> Optional[str]:
    """Return ``None`` when the optional data is usable, else the reason code.

    This is the availability oracle: it does not merely prove the file
    opens, it proves the file IS a concreteness table (required columns
    present, at least ``MIN_USABLE_ROWS`` parsed ratings). A 0-byte or
    header-only CSV therefore reports ``data_malformed`` instead of
    loading to ``{}`` and letting every AIC-8 detector emit 0.0.

    Never raises — every failure becomes a reason code, so an audit that
    calls this at import time (or in a pytest ``skipif``) cannot explode.
    """
    path_str = str(data_path) if data_path else ""
    try:
        _load_concreteness_dict(path_str)
    except FileNotFoundError:
        return DATA_NOT_INSTALLED
    except ConcretenessDataError as exc:
        return exc.reason
    except Exception:  # noqa: BLE001 — never raise out of the guard
        return DATA_MALFORMED
    return None


def unavailable_guidance(data_path: Optional[Path | str] = None) -> str:
    """Human guidance for why the data is unusable (``""`` when it is fine)."""
    return guidance_for(availability_reason(data_path))


def is_available(data_path: Optional[Path | str] = None) -> bool:
    """Return whether optional concreteness data is locally available AND usable.

    The public name. ``True`` only when the file exists, opens, and parses
    into a real rating table — presence alone is not availability.
    """
    return availability_reason(data_path) is None


def is_loaded(data_path: Optional[Path | str] = None) -> bool:
    """Backwards-compatible alias for :func:`is_available`.

    The AIC-8 detectors and the composite audits all call the public
    ``is_available()`` soft check; ``is_loaded`` is retained only so
    older callers keep working. Like ``is_available`` it never raises.
    """
    return is_available(data_path)
