### Fixed

- Enforce owner-private native Windows permissions throughout completed
  `acquire_imessage_sent_atomic` run validation, retain safe file handles while
  reading and report unsafe links or permissions through controlled refusals.
  Create newly owned private native output nodes with protected permissions.
  Preserve POSIX validation and the live-smoke receipt writer's platform limit.
