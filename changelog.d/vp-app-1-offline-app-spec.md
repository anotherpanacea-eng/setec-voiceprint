### Added

Planning spec for a usable offline Voiceprint app (fleet card VP-APP-1), in `specs/vp-offline-app.md`.

- **First workflow:** "does this draft still sound like me?", against a local baseline.
- **Tiers:** alpha, optional, future and not-offline, by product scope, with readiness tracked separately.
- **Minimum alpha requirements:**
  - LuAR: LUAR-MUD, Apache-2.0, about 330 MB.
  - Surprisal: TinyLlama 1.1B, Apache-2.0, about 4.4 GB.
  - LAPD: required for alpha but blocked pending capability registration, verified weights/licensing and offline acceptance. Not implemented or available; not deferred to Future.
- **Install:** routes using model packs verified by sha256, plus a hardware measurement protocol (unmeasured here, since no model runs are authorized).
- **Acceptance:** disconnected real-model acceptance.
- **Packaging:** three options compared, with decisions listed for the owner.

Nothing is built.
