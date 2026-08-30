### Fixed

`setec-voiceprint`: stopped redistributing the Brysbaert concreteness CSV. The optional AIC-8 paths now report explicit unavailable data status until a user fetches the publisher's supplementary file locally.

The first release intended to exclude the converted dataset is `v1.133.0`. It supersedes `v1.132.0` and all earlier releases for redistribution purposes; historical tags remain unchanged. Users who choose to enable the optional detectors must run `python3 plugins/setec-voiceprint/scripts/fetch_brysbaert.py` themselves and are responsible for the publisher's source terms.
