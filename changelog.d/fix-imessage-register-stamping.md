### Fixed

**Producer-side register stamping — iMessage acquisition now lands in the
profile-only tier it is supposed to be guarded by.** PR #369's closed
register-tier taxonomy made tier `private_dyadic` the key for every
profile-only guard: `manifest_validator`'s rejection of `baseline` use and
`stylometry_core.assert_personal_register_isolated`'s refusal of pooled
references. Both iMessage producers sat outside that key.

`acquire_imessage_sent.py` hard-coded `register="personal"` on every emitted
row and offered `--register` with `choices=["personal"]`. `personal` is
`private_composed`, not `private_dyadic`, so no tier-driven guard fired for a
freshly acquired row: private conversational material could be pooled with
`blog_essay` references and could carry `use: baseline`, silently. It now
stamps `message.imessage`, and `--register` offers only that leaf.

`acquire_imessage_sent_atomic.py` declared `--register` with no `choices`, no
default, and no reference to any register vocabulary. The value is copied
verbatim into every emitted row and bound into the sealed run's source-config
fingerprint, and completed-run bindings are immutable — so an operator could
seal an iMessage run as `--register blog_essay` and freeze the mislabel. The
validator only warns on an *unknown* value and said nothing about a
valid-but-wrong leaf. `main()` now refuses, at option-parse time, any
`--register` that does not resolve to tier `private_dyadic`, before the HMAC
key is loaded and before any run state is written. The gate is deliberately
CLI-only: `semantic_options_payload` is untouched, so every already-sealed run
keeps its exact fingerprint and still revalidates.

**Migration — draft manifests produced by `acquire_imessage_sent.py` before
this change.** Any `draft_manifest.jsonl` (and its `.meta.json` sidecars)
emitted by that acquirer carries `register: "personal"` on rows that are in
fact private dyadic conversation. Those rows must be restamped to
`message.imessage` before they are used to build any profile, or they will
continue to evade the profile-only guards. This is a *draft* artifact
migration only: the registered private corpus is unaffected. Its rows were
composed by the one-off contiguous-turn proposal path
(`specs/imessage-contiguous-turn-proposal.md`) and were already set to
`message.imessage` at registration per `specs/77-imessage-register-isolation.md`
§"Registration procedure", so no published row needs restamping. Sealed
`acquire_imessage_sent_atomic` runs are likewise untouched — the new gate
refuses future mislabels and never rewrites an existing binding.

**RAID calibration manifests no longer claim a private tier for public web
text.** `calibration/raid_to_manifest.py` carried a stale six-leaf comment and
mapped `reviews`, `reddit`, and `recipes` to `personal` (`private_composed`),
putting third-party public prose in a private tier and poisoning
`register_tier_counts` on every RAID-derived calibration manifest. `reviews`
and `reddit` now map to `forum_metafilter` (`public_responsive`); `recipes` has
no honest leaf and now omits the field, as `code`/`czech`/`german` already did.
A regression test asserts that no RAID domain may map to a `private_*` tier and
that every mapped leaf exists in the closed registry.
