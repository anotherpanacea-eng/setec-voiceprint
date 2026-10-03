### Changed

- Relocate whole `narrative_decision_audit` and `narrative_decision_long_form`
  surfaces into `setec.surfaces`, retaining
  permanent schema-1.x launchers and shared module/monkeypatch identity. Base
  normalized JSON delivery remains available; long-form remains experimental
  without normalized delivery or licensed work-level aggregates.
- Retarget the existing long-form-to-base layer exemption and document the two
  visible launcher edges and required detached-runpy bootstraps. The honestly
  counted bootstrap ceiling rises from 189 to 191; no exclusions are added.
- Add model-free synthetic compatibility and bare-copy checks without changing
  judge, manifest, cache, license, scoring, or refusal behavior. This bounded P4
  family does not qualify full P3/P4 acceptance or consumer fixture/lock parity.
