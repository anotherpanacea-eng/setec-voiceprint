### Changed

Relocate the complete reconstructibility probe-set builder into `setec.surfaces`
behind its historical launcher. Preserve actual executing-source identity and
checkpoint drift refusal; the relocated implementation has its own builder hash
and revision. Native publication remains Darwin-only, with unsupported-platform
refusal before private access.

Root the tracked-source Git pathspec at the repository top level. The previous
repository-relative lookup ran from the source subdirectory and incorrectly
refused the clean tracked builder. Dirty, untracked or changed producer identity
continues to fail closed.
