### PATCH — whole argument-quality/calibration relocation

Move `argquality_dimension_profile` and `argument_certainty_calibration` into
`setec.surfaces`, retaining permanent legacy module aliases and scripts-root
resolution in bare copied plugins. Public/private APIs, shared monkeypatches,
offline judging, licenses, independent bands, evidence defenses, CLI artifacts
and refusals remain unchanged. The two registered families remain distinct and
consumer-disabled, without `json_delivery`; normalized dispatch stays refused.

Update only the existing migration metadata and copied-plugin conformance
checks. Two implementation bootstraps become two launcher bootstraps, with no
counter exclusions or ceiling increase. This bounded relocation does not claim
live-judge fidelity, consumer clearance, or full P3/P4 acceptance.
