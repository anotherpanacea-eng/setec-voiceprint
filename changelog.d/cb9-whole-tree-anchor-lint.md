### Fixed

`tools/spec_anchor_lint.py` checks a bare-basename `name.py:N` citation against every file with that name, so a short compatibility launcher no longer hides the relocated module it forwards to. Specs now resolve every gating anchor: shorthand paths are repo-relative, stale line citations are corrected, and stale status lines match what landed. CI runs the anchor lint over every spec instead of only the specs a PR changes. That drops the old changed-spec wrapper's removed/renamed-spec check: a surviving spec that cites a deleted or renamed spec by full path now passes as long as the spec number still exists (`tools/check_changed_spec_anchors.py` stays in the tree, unused by CI).

Release impact: PATCH.
