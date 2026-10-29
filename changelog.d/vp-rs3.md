### Added

**`voice_fingerprint` — STAR style encoder (VP-RS3).** `--model star` selects
`AIDA-UPM/star` (Huertas-Tato et al., arXiv:2310.11081), the style-embedding scorer that the
2026 author-style-transfer papers report, so our numbers can sit beside theirs. It loads through
the same seam, dependency gate and envelope as StyleDistance, embeds with `pooler_output` (STAR
was trained through its pooler) at up to 512 tokens, and carries its own content-control caveat.
The weights card and code repository declare no license; STAR loads for private research use and
its caveat names the missing license. The default encoder and every existing envelope are
unchanged.
