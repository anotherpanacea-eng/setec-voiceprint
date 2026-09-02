# Optional local data resources

External datasets that a user may acquire for optional runtime features. Each entry below names the source, license posture, and how to regenerate a local cache.

## `brysbaert_concreteness.csv`

Per-word concreteness ratings on a 1-5 scale (5 = most concrete) covering 39,954 English words and two-word phrases. Used by `scripts/concreteness.py` as the optional concreteness lookup that drives the AIC-8 image-conjunction detector. SETEC does not distribute this converted CSV; without a locally acquired copy, the dependent AIC-8 detectors report `data_not_installed`.

### Citation

Brysbaert, M., Warriner, A. B., & Kuperman, V. (2014). Concreteness ratings for 40 thousand generally known English word lemmas. *Behavior Research Methods*, 46(3), 904-911. https://doi.org/10.3758/s13428-013-0403-5

The dataset is the supplementary material (`MOESM1_ESM.xlsx`) attached to the paper's open-access record on SpringerLink.

### Schema

| Column | Type | Description |
|---|---|---|
| `word` | string | Single word or two-word phrase. |
| `is_bigram` | 0 or 1 | 1 if the entry is a two-word phrase (e.g., "zero tolerance"); 0 otherwise. |
| `conc_mean` | float | Mean concreteness rating across raters. 1.0 = most abstract; 5.0 = most concrete. **Validated on load:** every parsed value must be finite and within 1.0-5.0 inclusive; one value outside that scale marks the whole file `data_malformed` and every dependent detector reports unavailable rather than scoring against fabricated ratings. |
| `conc_sd` | float | Standard deviation across raters. High SD = disagreement. |
| `unknown_count` | int | Number of raters who marked the word unknown. |
| `total_raters` | int | Total raters who saw the word. |
| `percent_known` | float | `(total_raters - unknown_count) / total_raters`. |
| `subtlex_freq` | int | SUBTLEX-US frequency count for context; 0 for words not in SUBTLEX. |

### Content floor

`scripts/concreteness.py` accepts a file at this conventional path only if it carries at least
`MIN_USABLE_ROWS` (10,000) **distinct** usable words — the loaded table is a dict keyed by the
lowercased word, so duplicated rows collapse and a 12,000-row table over 6,000 distinct words is
a 6,000-entry table — well below the published table's
39,954, and the SAME constant `fetch_brysbaert.MIN_CONVERTED_ROWS` enforces before installing.
The fetcher counts the same quantity with the same helper (`concreteness.rating_key`), applies
the loader's own value oracle (`concreteness.is_valid_rating`) to every `Conc.M` cell, and
refuses a `--min-rows` below the loader floor when writing to this conventional path, so a fetch
that succeeds cannot leave a file the loader would reject. (A
`--min-rows` override IS allowed for any other `--output`: that is the experiment seam, and a
file produced that way is not expected to load from the conventional path.) This matters because the CSV may
legitimately be acquired by a route other than the fetcher: a truncated or hand-assembled table
would otherwise load, and every AIC-8 detector would score confidently against a handful of
rows. A caller that passes an explicit `data_path` (the bring-your-own / test seam) is held to a
floor of 1 instead — naming a path is a deliberate act; the conventional path is not.

### License posture

The original XLSX is hosted as Springer supplementary material attached to a published article. Springer does not attach an explicit redistribution license to supplementary data files. SETEC therefore does not grant permission to download, use, or redistribute the source data. A person who chooses to fetch it is responsible for the publisher's source terms.

### Regenerating the cache

```bash
python3 plugins/setec-voiceprint/scripts/fetch_brysbaert.py \
    --output plugins/setec-voiceprint/data/brysbaert_concreteness.csv
```

The fetcher downloads `MOESM1_ESM.xlsx` from Springer's static-content CDN, converts to CSV using the schema above, and writes to the ignored target path. Requires `openpyxl` (listed in `requirements.txt`). Do not commit or redistribute the generated file.
