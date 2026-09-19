### Fixed

- `shard_runner`: use a read-only process-handle wait for native Windows PID
  liveness instead of signal zero, which can interrupt or terminate processes.
  Unknown API and handle-cleanup failures retain claims conservatively. Existing
  process-start-time identity checks still govern intentional worker signals.
