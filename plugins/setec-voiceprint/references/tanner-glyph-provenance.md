# Tanner PDF glyph and language provenance

The direct PDF acquirer accepts an explicit artifact_profile value of
"tanner" in a URL-list row. Its existing slash-glyph and letter-gap cleanup
still runs for every Tanner-profile PDF. One reviewed source additionally
qualifies for a font-aware correction after pypdf has decoded and laid out
text. The default profile has no Tanner cleanup, and the local inventory/OCR
routes do not opt in.

## Reviewed glyph policy

The source PDF SHA-256 is
147658aa934fa5f04d6ad78d3cd87c54e417c55594caaf9bd5431fb65ecd7494.
For that exact byte sequence, the two allowed embedded font-program SHA-256 /
decoded whole ToUnicode CMap SHA-256 pairs are:

| Font | Font program | ToUnicode CMap |
| --- | --- | --- |
| Regular | b66aa246c5aed81d8165cb8e15d5b0470fcf0123b009a20aa2af0e8f855f5f6b | a173dfd7ac8800ea1c127bec6842104f6cb1a88c49555b54f05aa72bb35a9b57 |
| Italic | ca79bc9b55d66be89e15abe956c8383b943a5879e857f7c05fc2a65afe5868b5 | fe7305975f32364e46f640f4132a09a63ddf0f57c20228238354f13cbadcbec5 |

Only U+0160 decoded through one of those fonts changes to ASCII "fi". The
reviewed source has 78 regular and 8 italic occurrences. The exact source hash
also binds the PDF's Encoding, which the two font-stream signatures do not.
A font name, glyph name, codepoint, or matching font/CMap pair in another PDF
is insufficient. An unknown or malformed font keeps pypdf's original page
text. A page is corrected only when concatenating all original visitor
fragments reproduces the ordinary pypdf page return exactly. Otherwise that
return is retained. No PDF stream or original source bytes are rewritten.

The direct acquirer's .meta.json preprocessing block records
source_pdf_sha256 from fetched bytes and the explicit artifact_profile
when a successful extraction is passed unchanged into processing. These
fields identify input and routing; neither proves a glyph repair occurred.
content_hash remains the hash of cleaned emitted UTF-8 text. Sidecars from
this changed acquirer carry scraper_version 1.1. Keep source PDFs, font
programs, corpus prose, and per-unit records in the private corpus area,
outside the repository.

## Language status

acquire_pdf_urls --language-status accepts native, non_native_advanced,
non_native_intermediate, learner, and unknown; its default is unknown. The
chosen value is passed into the draft manifest for every URL in that
invocation. Use native only with affirmative evidence about the author's
language background. English prose, original composition, no translation
credit, and nationality alone do not establish native-language authorship.
This acquirer's default does not change the shared manifest composer or other
acquirers. Review any status override before corpus admission.
