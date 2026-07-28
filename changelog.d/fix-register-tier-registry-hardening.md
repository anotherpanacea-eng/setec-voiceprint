### Fixed

**`register_taxonomy` — the register-tier registry now fails closed on every
import path.** The drop-in registry is a privacy control (the `private_dyadic`
tier is what keeps iMessage and Messenger material out of pooled author
references), but a *short* or *retiered* registry loaded silently. Four
hardening fixes.

(1) **Self-validating registry.** `load_register_tiers` refused an empty
directory but happily returned 20 of 21 leaves. A leaf lost to a deleted
fragment — or to a case rename, since `pathlib.glob("*.json")` is
case-sensitive on POSIX while APFS is not — then resolved to `None`, dropped
out of `PROFILE_ONLY_REGISTERS`, and `stylometry_core`'s
`assert_personal_register_isolated` read that `None` as benign, pooling
private-dyadic material with `blog_essay` without raising. The only tripwire
was `validate_registry_closure(ALLOWED_REGISTER)`, an import-time side effect
of `manifest_validator` — a module that `voice_distance`, `voice_profile`,
`general_imposters` and 20 of the 27 other `stylometry_core` consumers never
import. The module now checks the loaded mapping against two pins
(`EXPECTED_REGISTER_LEAVES` and `EXPECTED_REGISTRY_DIGEST`) immediately after
the load, so a bare `import register_taxonomy` refuses. `load_register_tiers`
itself stays pure and unpinned for drop-in callers and tests.

(2) **Tier *values* are covered, not just key sets.** `validate_registry_closure`
compares key sets, so editing `message.imessage.json` to
`"tier": "public_composed"` kept the key set identical, passed closure,
silently emptied that leaf out of `PROFILE_ONLY_REGISTERS`, and disabled both
the pooled-reference guard and the `use: baseline` rejection — caught only by
a CI test, never by a deployed install. `EXPECTED_REGISTRY_DIGEST` pins the
sha256 of the canonical (sorted, compact) bytes of the whole leaf-to-tier
mapping, so a retiering fails at import. `validate_registry_closure` is
unchanged and still cross-checks the registry against `manifest_validator`'s
vocabulary.

(3) **A FIFO in the registry directory no longer hangs every import.**
`_read_fragment` checks `S_ISREG` *after* `os.open`, and a blocking
`O_RDONLY` open on a FIFO waits for a writer indefinitely. The open now sets
`O_NONBLOCK` (a no-op for regular files), so it returns and the existing
regular-file check rejects the FIFO immediately instead of blocking forever.

(4) **Seam agreement on `str` subclasses.** `stylometry_core._entry_register`
admits a register via `isinstance`, while `resolve_register_tier` rejected it
via `type(...) is not str` — so a `str` subclass resolved to `None` and failed
open at the guard. `resolve_register_tier` now admits any `str` and resolves
it through the base `str` payload, which is the only option that returns the
real leaf: a bare `isinstance` lookup would let an overridden
`__hash__`/`__eq__` choose the tier instead. Not reachable today (registers
arrive from `json.loads`); fixed so the two seams cannot drift apart.

Both pins must be updated in the same commit as any legitimate registry
change. That maintenance cost is deliberate — retiering a leaf out of
`private_dyadic` turns off a privacy guard and should not be possible by
editing one JSON file — and there is no bypass flag, since a bypass would
reintroduce the fail-open. The raised `ValueError` reports the offending
leaves, the surviving `private_dyadic` set, and a paste-ready replacement
digest, so updating a pin needs no separate tool.

**Mutation-tested guards.** A mutation audit of the taxonomy found two
behaviours that nothing in the 8,940-test suite pinned; both are now covered.
(a) `MAX_FRAGMENT_BYTES` was pinned only by its *existence*. Every size test
sized its fixture as `MAX_FRAGMENT_BYTES + 1`, so raising the cap 1024x to
1 MiB raised the fixtures with it and the suite stayed green. The value is now
asserted literally, and a second fixture uses an absolute 2 KiB body that does
not track the constant — and, unlike the 1025-spaces fixture (not valid JSON,
so it still raised from the parser with size enforcement deleted, making that
kill message-coupled), it is well-formed, correctly named, correctly tiered
JSON that loads silently once the cap is gone. (b) `REGISTER_TO_TIER`'s
`MappingProxyType` wrapper could be replaced with the bare `dict` without
failing anything, because the existing assertions read through
`dict(...)`/`set(...)`, which is blind to container type. The registry is a
process-wide singleton feeding the pooling guard, so any importer could have
run `REGISTER_TO_TIER["message.imessage"] = "public_composed"` and disarmed
that guard for the rest of the process with both import-time pins already
satisfied and nothing left to re-check. Immutability of the module singleton,
of `load_register_tiers`' return value, and of `PROFILE_ONLY_REGISTERS` (a
real `frozenset` — also unpinned) is now asserted directly. Two narrower gaps
closed alongside: the duplicate-JSON-key test only duplicated `register`,
which the filename-stem check catches independently, so the case with no
second line of defence went untested — a duplicate `tier` resolves under
last-wins parsing to `public_composed` on a correctly named
`message.demo.json`, stripping the privacy tier with every other check
passing; and `resolve_register_tier`'s happy path had no direct positive
assertion.

Also: `.gitignore`'s `*private*` privacy glob would have silently swallowed a
future fragment named like `message.private_dm.json`. No shipped fragment
matched, but `register_tiers.d/` is the one drop-in directory whose whole
subject is privacy. Added the re-include exception alongside the two the repo
already carries for `contract_fixtures/` and `_golden_capabilities/`.
