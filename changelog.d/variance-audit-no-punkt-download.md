### Fixed

**`variance_audit.py` no longer downloads NLTK Punkt data at import.** Importing
`variance_audit` (and so `stylometry_core`) on a host without the data used to
call `nltk.download("punkt")`, a silent network request that also fetched the
legacy `punkt` resource NLTK 3.9 no longer reads. Punkt is now explicit setup
(`python -m nltk.downloader punkt_tab`, noted in `requirements.txt`).
`split_sentences` is unchanged: it uses Punkt when the data is present and
falls back to the regex splitter when it is missing.
