### Changed

- Add opt-in, source-bound Mirrulations comment metadata verification to
  acquire_mirrulations. Successful future acquisitions record separate
  source-reported received, posted, postmark and modification evidence with
  raw text/JSON byte hashes in the private sidecar; date_written remains
  unknown. Default off mode retains the existing text route.
- Correct the raw-data description: it includes comment JSON alongside
  attachments. Standard-mode metadata failures are logged per item and
  never silently attached to another comment.
