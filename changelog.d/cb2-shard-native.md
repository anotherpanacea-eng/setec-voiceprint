### Fixed

- Fix native Windows shard-worker identity checks with read-only process creation
times, and serialize state updates with a real Windows byte-range lock. Keep
unverifiable/reused-PID refusal and POSIX behavior. Use the supported abrupt
process-stop route for Windows kill-all; Windows terminate-all remains abrupt
and does not promise cooperative checkpoint flushing. Run existing concurrent
claim/state tests with synchronized, spawn-safe workers on both platforms.
