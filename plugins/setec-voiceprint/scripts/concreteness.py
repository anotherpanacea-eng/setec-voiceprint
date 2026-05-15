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
The CSV ships with the framework. If absent, regenerate via
`scripts/fetch_brysbaert.py` which re-downloads from Springer.

Design notes:

  * **Lazy load, cached.** The CSV is ~1.5 MB and loads into a
    dict of ~40K entries. Loading takes ~100ms; subsequent lookups
    are O(1). The loader caches the dict at module level so
    repeated `get_concreteness()` calls don't re-read the file.
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


@lru_cache(maxsize=1)
def _load_concreteness_dict(path: str = "") -> dict[str, float]:
    """Load the concreteness CSV into a {word: conc_mean} dict.

    The path argument is a string (not Path) so ``lru_cache`` can
    hash it. Pass an empty string for the default location.

    Raises ``FileNotFoundError`` with operator-facing guidance when
    the CSV is missing — the message names the fetcher as the fix.
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
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            word = row["word"].lower()
            try:
                conc = float(row["conc_mean"])
            except (KeyError, ValueError, TypeError):
                continue
            result[word] = conc
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


def is_loaded(data_path: Optional[Path | str] = None) -> bool:
    """Return True if the CSV at ``data_path`` exists and is loadable.

    Does not raise; useful for graceful degradation in audits that
    don't strictly require concreteness (the AIC-8 detector should
    fail loud, but composite audits may want a soft check).
    """
    try:
        path_str = str(data_path) if data_path else ""
        _load_concreteness_dict(path_str)
        return True
    except (FileNotFoundError, OSError):
        return False
