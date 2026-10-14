### Changed

**Register-sweep refusal types and shared limits moved to `setec.core.register_sweep_errors` (CB-5).**
`SweepRefusal`, `BadInput`, `InternalError` and five shared string-domain limits
moved verbatim out of the `register_sweep` surface, which re-exports them as the
same objects, so `except register_sweep.BadInput` still catches them.
`manifest_validator` now imports them from core instead of importing the
`register_sweep` surface at load time. The layering exemption is deleted, and
one load-time direction of the two surfaces' mutual dependency is gone.
