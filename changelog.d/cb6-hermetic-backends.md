### Added

**Hermetic backend mode.** With `setec_hermetic_backend=stub` set, the API judge
factory and the embedding and surprisal loaders refuse with their existing error
types, before any provider setup, cached model or dependency import. Through
`setec run` an API-judge surface returns its usual unavailable envelope, and the
direct launchers exit 3 with their usual error prefix. Mock and manifest judges
keep working. With the variable unset, nothing changes. This lets the zero-install
gate run real consumer cases without models (CB-6).
