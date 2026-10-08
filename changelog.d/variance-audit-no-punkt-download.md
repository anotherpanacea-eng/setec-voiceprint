### Fixed

**`variance_audit.py` no longer downloads NLTK Punkt data at import.** Importing
`variance_audit` (and so `stylometry_core`) on a host without the data used to
call `nltk.download("punkt")`, a silent network request. On NLTK 3.9 that
fetched the legacy `punkt` resource, which `sent_tokenize` no longer reads, so
splitting never depended on it. Punkt is now explicit setup
(`python -m nltk.downloader punkt_tab` on NLTK 3.9+, `punkt` on older 3.8.x;
noted in `requirements.txt`). `split_sentences` is unchanged: it uses Punkt
when the data is installed and the regex splitter when it is missing. The one
behavior change is on an NLTK 3.8.x host that relied on the first-run fetch:
it now uses the regex splitter until `punkt` is installed.
