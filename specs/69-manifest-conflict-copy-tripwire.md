# 69 - Manifest conflict-copy tripwire

> Add an opt-in, fail-closed sync-conflict preflight to the existing manifest validator so a Dropbox conflict fork cannot silently enter a corpus run.

- **Status:** In build (`codex/manifest-conflict-copy-check`)
- **Tier:** near-term
- **GPU required:** no
- **Source contract:** fleet refill packet B5 and the corpus `_conflict_check.ps1` tripwire
- **License decision:** N/A - stdlib-only local filesystem inspection

## Motivation

The curated corpus lives in a multi-device sync tree. Dropbox can preserve two
concurrent versions by creating a sibling such as
`file (DEVICE's conflicted copy 2026-07-21).txt`. A manifest or source fork can
therefore be consumed as if it were an independent file. The existing PowerShell
preflight catches these names; B5 promotes that load-bearing check into the
cross-platform validator operators already run.

This is a filesystem-integrity refusal, not a content or authorship signal. It
does not read corpus files, infer which fork is correct, or resolve a conflict.

## Contract

### CLI and default preservation

- Add `--check-conflict-copies` to
  `plugins/setec-voiceprint/scripts/manifest_validator.py`.
- When the flag is absent, no tree scan occurs. Existing validation results,
  output shape, progress behavior, and exit codes remain unchanged.
- When the flag is present, scan the manifest's parent tree before parsing the
  manifest. A clean scan continues through the existing validator. A conflict
  match or incomplete scan refuses before manifest parsing.

### Matching and traversal

- Match the case-insensitive basename substring `conflicted copy`. Do not match
  against a full relative path: a matching directory must not make every child
  appear to match. This covers both the short marker and Dropbox's real
  device/owner-prefixed form.
- Inspect directory-entry names recursively, including files, directories,
  symlinks, and Windows junctions/reparse points. Never read file contents.
- Do not follow directory symlinks or junctions. A matching link/junction is
  reported once; its target remains outside the traversal boundary.
- Include hidden entries. Sort traversal and final results deterministically by
  `(casefolded relative path, raw relative path)`.
- Report only manifest-parent-relative paths with `/` separators. Do not report
  the absolute corpus root, file contents, hashes, or manifest entry data.
- Any `OSError` that prevents a complete traversal is captured, sanitized to a
  relative location, and fails closed. It must not become a traceback or a
  false-clean result.

### Result and output

- With the flag, add `conflict_copy_check` to the validator result and JSON
  envelope. Its stable fields are:
  `checked`, `root` (always `.`), `n_matches`, `matches`,
  `n_scan_errors`, `scan_errors`, and `validation_ran`.
- On a clean scan, `validation_ran` is `true` and the remaining result is the
  ordinary validator result. On a refused preflight it is `false`;
  `n_entries`, `n_errors`, and `n_warnings` are JSON null, while `issues`,
  `tripwires`, and `summary` are empty. Markdown must state `Manifest
  validation: NOT RUN (preflight refused)` and must not say `Manifest is
  clean.` JSON consumers therefore distinguish a refused preflight from a
  parsed zero-entry manifest without interpreting the process exit code.
- Markdown gains a `Conflict-copy preflight` section. JSON remains one valid
  schema-version-1.0 envelope. `--out` writes the same selected report and keeps
  stdout empty.
- Conflict paths never enter progress stderr. Existing progress remains
  aggregate-only.
- Flag-mode stdout and `--out` bytes are byte-identical UTF-8 artifacts with LF
  line endings and exactly one terminal LF on every platform. JSON retains the
  existing `json.dumps(..., indent=2, default=str)` formatting (including its
  default ASCII escaping), followed by that one LF. Use `sys.stdout.buffer`
  when available, with a text-stream fallback for embedded/test callers; use
  binary file output for `--out`. When `--out` is selected stdout remains
  empty, and the file bytes equal the bytes stdout would otherwise receive.
  The legacy no-flag output path is untouched.

### Exit precedence

1. argparse usage errors retain exit 2;
2. any conflict match or incomplete scan returns 2;
3. otherwise existing manifest errors, or warnings under `--strict`, return 1;
4. otherwise return 0.

Exit 2 therefore means the requested preflight could not establish a clean
sync tree, whether because it found a conflict fork or could not inspect the
whole tree. Every match and sanitized scan failure is listed in the selected
report sink.

## Test contract

Use only synthetic `tmp_path` trees; no corpus fixture or corpus-derived name.

1. Flag absent: a nested conflict-named entry is ignored and legacy result,
   report, and exit behavior are unchanged.
2. Clean opt-in scans preserve ordinary exit 0/1 behavior.
3. Nested file and directory matches, Dropbox-style owner/device names, and
   mixed case are found; `conflicted`, `copy`, and `conflicted-copy` alone are
   not.
4. A matching directory is listed once without spuriously matching every
   descendant.
5. Results are unique, casefold/raw sorted, relative, and `/`-separated; they
   contain neither the absolute root nor file contents.
6. Directory symlinks and guarded `Path.is_junction()` entries are pruned; a
   matching link is listed, and a nonmatching link cannot expose an outside
   matching target.
7. An injected traversal error fails closed with sanitized output and no
   traceback.
8. Conflict plus a clean manifest, manifest error, or strict warning returns 2.
9. Markdown, JSON, and `--out` carry the same complete list; JSON stdout remains
   parseable while progress stderr remains aggregate-only. A refused preflight
   exposes `validation_ran: false`, null validation counts, and never emits the
   `Manifest is clean.` claim.
10. Flag-mode stdout and `--out` are byte-identical UTF-8/LF artifacts ending
    in exactly one LF. The focused test
    module runs in the repository's Windows CI job as well as ordinary CI.

## Paper trail and gates

- Update the manifest-validator capability fragment and regenerate its golden.
- Update the scripts README and manifest schema reference, including exit 2.
- Add a changelog fragment and reconcile the roadmap status.
- Run focused tests, the full suite with exact counts, capability drift,
  calibration-readiness generation/check, docs freshness, compile/diff checks,
  and the repository leak gate.
- Open one draft PR. Do not merge; Code-PC Claude owns the opposite-vendor and
  native-Windows merge gate.

## Out of scope

- Choosing, deleting, renaming, merging, or hashing conflict copies.
- Following links outside the manifest parent tree.
- Scanning when the flag is absent or wiring every manifest consumer to enable
  the mode automatically.
- Corpus prose, model inference, calibration, fiction, or GPU work.
