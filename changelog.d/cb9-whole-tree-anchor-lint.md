### Fixed

`tools/spec_anchor_lint.py` checks a bare-basename `name.py:N` citation against every file with that name, so a short compatibility launcher no longer hides the relocated module it forwards to. Specs now resolve every gating anchor: shorthand paths are repo-relative, stale line citations are corrected, and stale status lines match what landed. CI runs the anchor lint over every spec instead of only the specs a PR changes. `tools/check_changed_spec_anchors.py` still runs as its own step, because the whole-tree lint checks sibling-spec references by number only and would miss a surviving spec that cites a deleted or renamed spec by path.

Release impact: PATCH.
