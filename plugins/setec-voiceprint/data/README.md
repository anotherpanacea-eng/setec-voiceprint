# Shipped data resources

External datasets used by the framework at runtime. Each entry below names the source, license posture, and how to regenerate the local cache.

## `brysbaert_concreteness.csv`

Per-word concreteness ratings on a 1-5 scale (5 = most concrete) covering 39,954 English words and two-word phrases. Used by `scripts/concreteness.py` as the concreteness lookup that drives the AIC-8 image-conjunction detector. Required when running `--aic8` or its component detectors.

### Citation

Brysbaert, M., Warriner, A. B., & Kuperman, V. (2014). Concreteness ratings for 40 thousand generally known English word lemmas. *Behavior Research Methods*, 46(3), 904-911. https://doi.org/10.3758/s13428-013-0403-5

The dataset is the supplementary material (`MOESM1_ESM.xlsx`) attached to the paper's open-access record on SpringerLink.

### Schema

| Column | Type | Description |
|---|---|---|
| `word` | string | Single word or two-word phrase. |
| `is_bigram` | 0 or 1 | 1 if the entry is a two-word phrase (e.g., "zero tolerance"); 0 otherwise. |
| `conc_mean` | float | Mean concreteness rating across raters. 1.0 = most abstract; 5.0 = most concrete. |
| `conc_sd` | float | Standard deviation across raters. High SD = disagreement. |
| `unknown_count` | int | Number of raters who marked the word unknown. |
| `total_raters` | int | Total raters who saw the word. |
| `percent_known` | float | `(total_raters - unknown_count) / total_raters`. |
| `subtlex_freq` | int | SUBTLEX-US frequency count for context; 0 for words not in SUBTLEX. |

### License posture

The original XLSX is hosted as Springer supplementary material attached to a published article. Springer does not attach an explicit redistribution license to supplementary data files. The framework ships this converted CSV in-repo under the assumption that academic-research supplementary data is intended for downstream research use; operators redistributing the framework with this data should cite Brysbaert et al. 2014 (citation above) and link the original record.

If your local redistribution context cannot include this CSV, `scripts/fetch_brysbaert.py` re-downloads the XLSX from Springer and regenerates the CSV. Operators who delete the CSV and run the fetcher get the same data.

### Regenerating the cache

```bash
python3 plugins/setec-voiceprint/scripts/fetch_brysbaert.py \
    --output plugins/setec-voiceprint/data/brysbaert_concreteness.csv
```

The fetcher downloads `MOESM1_ESM.xlsx` from Springer's static-content CDN, converts to CSV using the schema above, and writes to the target path. Requires `openpyxl` (listed in `requirements.txt`).
