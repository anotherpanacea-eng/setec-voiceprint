### Fixed

- `acquire_gmail_sent`: canonicalize decoded MIME line endings before
  `format=flowed` processing, preserve authored/quoted reply boundaries, and
  write exact UTF-8 bytes so Windows does not silently change corpus content
  hashes. Legacy all-CRLF text-mode output remains deduplicated only when its
  normalized logical-LF bytes match the recorded hash; mixed or malformed
  newline states fail closed. Flowed space-stuffing and `delsp=yes` are now
  handled without joining authored text to reply attributions or turning soft
  line wraps into apparent blank paragraphs.
