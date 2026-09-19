# Mirrulations comment metadata provenance

The Mirrulations acquirer reads pre-extracted attachment text from
derived-data/ in the public bucket. raw-data/ also holds per-comment JSON;
it is not a binary-only tree. The default --metadata-mode off retains the
existing text-only route, including custom buckets, key patterns, and
preprocessing. --metadata-mode standard is an explicit request to verify
one supported text key against its related comment metadata in the default
mirrulations bucket. Unsupported keys and failed joins are skipped with
a metadata-* reason; there is no silent off-mode fallback.

## Supported join

Standard mode accepts only a derived-data/<agency>/<docket>/mirrulations/
extracted_txt/comments_extracted_text/<engine>/<comment>_attachment_<n>_
extracted.txt key with reviewed ASCII segments. The comment ID must be the
docket plus a decimal suffix. The attachment number is a positive canonical
decimal without leading zeros; the comment suffix retains any leading zeros.
The corresponding metadata key is exactly raw-data/<agency>/<docket>/
text-<docket>/comments/<comment>.json. The acquirer makes no broad raw-data
listing or guessed filename join.

The JSON data must identify that comment and docket, type comments, and
contain exactly one related included attachment with a PDF fileFormat whose
fileUrl is https://downloads.regulations.gov/<comment>/attachment_<n>.pdf.
Duplicate relationship IDs, duplicate related included IDs, or multiple
matching PDF fileFormats make the relationship ambiguous. The JSON is read
with a 2 MiB maximum and a separately configured metadata S3 client with
finite connect/read timeouts and at most two total attempts. The raw JSON is
not archived by this mode.

On success, the private sidecar contains a top-level source_metadata receipt
with schema setec.mirrulations_source_metadata.v1 and status binding_verified.
It binds the fetched raw text-object bytes and the parsed JSON bytes with
separate SHA-256 hashes, byte counts, object keys, bucket, and the exact
comment/docket/attachment relationship. A decoded-body digest is transient
within the acquirer and is not a substitute for the raw object hash. The
existing cleaned content_hash and legacy raw_byte_length fields retain
their definitions. The sidecar is written once through the shared atomic
writer. No source PDF, metadata JSON, prose, or submitter details are embedded.

A hash without archived source bytes is a fetch receipt. binding_verified
means this fetch's identity relationship passed; it does not prove an
author's composition date, the historical identity of current extracted
text bytes, independent authorship, rights, AI status, or corpus admission.

## Source-reported timestamps

The receipt keeps these fields independently, each with valid, missing, or
invalid status and an exact value only when valid:

| Receipt field | Source field | Meaning |
| --- | --- | --- |
| received | comment receiveDate | Agency receipt |
| posted | comment postedDate | Public posting |
| postmark | comment postmarkDate | Mail postmark |
| comment_modified | comment modifyDate | Comment modification |
| attachment_modified | related attachment modifyDate | Attachment modification |

A valid value is an explicit timezone-aware ISO date-time. Date-only,
timezone-naive, non-string, and invalid-calendar values are invalid; absent
or null is missing. Invalid raw values are not copied into the receipt.
Mirror HTTP Last-Modified is storage metadata, not a posting date. Neither
received nor posted proves when the author wrote the text or that the
currently fetched text bytes existed at that time. The manifest keeps
date_written absent and era remains the operator's selection. These fields
may inform a later separately reviewed temporal decision.

## Failure and custody behavior

In standard mode, missing, transport-failed, oversized, malformed, wrong
identity, ambiguous attachment, unsupported key, and handoff-custody failures
skip that item before output. The run continues; the existing zero-output
failure rule still applies. A verified metadata object with a missing or
invalid individual timestamp can emit a receipt with that field's status.
The run summary's existing skip_log holds exact fixed metadata-* reasons.
No raw exception body or remote prose enters the skip reason.

An internal one-use receipt binds text key, bucket, mode, raw and decoded
hash domains, then the processed piece's source and cleaned content hash.
It is cleared on filtering, dedupe, errors, dry-run, write failure, and
successful emission. Dry-run verifies the join but persists no receipt.
