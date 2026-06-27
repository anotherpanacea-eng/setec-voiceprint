### Fixed

- **`argmove_profile` no longer hidden** — its `capabilities.d` fragment carried `status: todo`
  (which excludes an entry from the default capability listing) despite the shipped
  `argmove_profile.py` self-declaring `calibration_status: empirically_oriented`. Promoted the
  fragment + its golden to `empirically_oriented` to match the script, so the surface now appears
  in the default listing. (Audited the full `status: todo` set while here: the remaining 54 are
  correct — 48 are intentionally-hidden unfilled metadata stubs, and the acquisition utilities are
  deliberately hidden, not recommendable detection surfaces.)
