### Fixed

- Preserve the raw release bytes of `consumer_client.py` and `contract_fixtures/` that consumers vendor and hash, including on Windows checkouts with `core.autocrlf=true`. Existing checkouts need a fresh producer release checkout or verified-clean rematerialization of the protected paths to replace previously converted bytes. Voicewright #344 still requires a new producer release, consumer repin, and live CHECK 2 verification.
