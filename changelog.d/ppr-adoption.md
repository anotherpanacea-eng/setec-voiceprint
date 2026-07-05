### Changed

**`position_pair_register` — adopted as a downstream consumer surface.** Marked the
`position_pair_register` capability as consumed by **apodictic**: the
`capabilities.d/position_pair_register.yaml` fragment now carries
`consumers: [apodictic]`, the R1 normalized-entrypoint bundle
(`min_setec_version: "1.121.0"` — the first release tag carrying the surface —
plus `json_delivery: stdout` and a structured `inputs` list), and a committed R5
contract-fixture golden (`references/contract_fixtures/position_pair_register.json`,
a real mock-judge envelope through the surface's own `compose_envelope`). No
behavior change to the surface itself. Anchors unchanged: ContraDoc
(arXiv:2311.09182) contradiction-type taxonomy; BeliefShift (arXiv:2603.23848)
position-drift framing.
