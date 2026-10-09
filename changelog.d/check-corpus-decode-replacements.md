### Fixed

`setec-voiceprint`: `check_corpus` no longer drops bytes that aren't valid UTF-8 before scoring a file. It used to read with `errors="ignore"`, so a cp1252 file or a stray binary chunk could pass as clean. Bad bytes now decode to U+FFFD, the strip rules see them, and each file record carries `decode_replacements`, the number of replacements made (genuine U+FFFD characters in the file are not counted). Status thresholds are unchanged. The records-cache version moves to 1.2, so existing caches are rescored rather than reused.
