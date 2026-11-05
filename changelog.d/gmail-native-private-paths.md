### Fixed

Fix native Windows Gmail pipeline execution with real owner-only ACL validation
and protected private creation for requests, logs, receipts, and acquisition
output. Unsafe caller-provided state is refused without permission repair.
Shared private writers retain deliberate hardening of owned output nodes,
exact bytes, append/exclusive/truncate behavior, and atomic replacement. Gmail's
generated README now uses the private writer on every supported platform.
