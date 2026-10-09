### Fixed

`setec-voiceprint`: `mimicry_cosplay_audit` now counts idiolect preservation phrases only as whole phrases, bounded by the `[A-Za-z']` word class that `idiolect_detector` builds them from. It used plain substring counting, so a phrase also matched inside longer words (`"the"` counted 3 times in `"The other mother."`), which inflated `n_matched`, `n_total_occurrences`, the survival rate and both phrase densities. The envelope's claim-license caveat now says so; it no longer claims to mirror `confounder_audit`, whose survival check still uses substring matching.

Match detector-produced ASCII phrases against the original target text with ASCII case-insensitive matching. Unicode lowercase expansion cannot erase their boundaries or turn a non-ASCII separator into a detector word. Explicit non-ASCII phrases retain the existing lowercase matching behavior.

Retain the existing `confounder_audit` companion reference while explaining that its substring survival check can differ. Regenerate the mimicry contract fixture with this caveat; its scoring results and counts stay unchanged.
