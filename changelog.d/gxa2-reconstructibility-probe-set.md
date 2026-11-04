### Changed

Relocate the complete reconstructibility probe-set builder into `setec.surfaces`
behind its historical launcher. Preserve actual executing-source identity and
checkpoint drift refusal; the relocated implementation has its own builder hash
and revision. Native publication remains Darwin-only, with unsupported-platform
refusal before private access.
