### Added

Planning spec for a usable offline Voiceprint app (fleet card VP-APP-1), in `specs/vp-offline-app.md`.

- **First workflow:** "does this draft still sound like me?", against a local baseline.
- **Tiers:** alpha, optional, future and not-offline, by utility, hardware and readiness.
- **Minimum candidates:**
  - LuAR: LUAR-MUD, Apache-2.0, about 330 MB.
  - Surprisal: TinyLlama 1.1B, Apache-2.0, about 4.4 GB.
  - LAPD: not yet a registered capability, so Future until reconciled.
- **Install:** routes using model packs verified by sha256, plus a hardware measurement protocol (unmeasured here, since no model runs are authorized).
- **Acceptance:** disconnected real-model acceptance.
- **Packaging:** three options compared, with decisions listed for the owner.

Nothing is built.
