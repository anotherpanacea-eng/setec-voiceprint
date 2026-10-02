### Changed

Relocate preprocessing, verbatim-cover primitives, and the segmentation feature
lens into `setec.core` without changing their algorithms or defaults. Permanent
legacy aliases preserve module identity and shared monkeypatch behavior. Existing
pool/register checks follow the moved implementations; packaging checks retain
their existing scope with the three required compatibility bootstraps counted.
