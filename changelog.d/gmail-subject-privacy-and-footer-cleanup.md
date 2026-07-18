### Fixed

- `acquire_gmail_sent` redacts email addresses embedded in Subject lines before
  publishing title metadata and removes recognized trailing multi-line service
  footers while preserving authored service citations in the message body.
