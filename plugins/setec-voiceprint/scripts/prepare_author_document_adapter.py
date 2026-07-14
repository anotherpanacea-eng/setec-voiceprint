#!/usr/bin/env python3
"""Materialize exact-byte, attested private documents for author-corpus export."""
from __future__ import annotations

import argparse, hashlib, json, os, shutil, tempfile
from collections import Counter
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any
import author_corpus_export as exporter

SCHEMA_MAP = "setec-author-document-map/1"
SCHEMA_ATTEST = "setec-author-document-attestation/1"

def sha(b: bytes) -> str: return "sha256:" + hashlib.sha256(b).hexdigest()
def private(p: Path) -> None:
    # Resolve symlinks and `..` first: a lexical parts check on the unresolved path lets a
    # symlink component or `..` escape place copied prose outside the protected directory.
    real = p.expanduser().resolve()
    if "ai-prose-baselines-private" not in {x.casefold() for x in real.parts}: raise ValueError("private path required")
def assignment(s: str, flag: str) -> tuple[str, str]:
    a, sep, b = s.partition("=")
    if not sep or not a or not b: raise ValueError(f"{flag} must be NAME=PATH")
    return a, b
def _within(child: Path, parent: Path) -> bool:
    try: child.relative_to(parent); return True
    except ValueError: return False
def resolve(manifest: Path, raw: str) -> Path:
    q = Path(raw)
    if q.is_absolute() or ".." in q.parts: raise ValueError("unsafe source path")
    for root in (manifest.parent, manifest.parent.parent):
        p = root / q
        if not p.is_file() or p.is_symlink(): continue
        # An intermediate-directory symlink can make `p` resolve outside the authorized
        # root even when the final component is not itself a symlink; require the fully
        # resolved real path to stay within the resolved root AND the private tree.
        real, root_real = p.resolve(), root.resolve()
        if not _within(real, root_real): continue
        if "ai-prose-baselines-private" not in {x.casefold() for x in real.parts}: continue
        return p
    raise ValueError(f"missing source text: {raw}")
def atomic(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as f:
        f.write(data); temp = Path(f.name)
    try: os.replace(temp, path)
    finally:
        if temp.exists(): temp.unlink()
def sanitize(data: bytes) -> tuple[bytes, int]:
    text = data.decode("utf-8")
    cleaned = "".join(ch for ch in text if ch not in exporter.BIDI_CONTROLS and (ord(ch) >= 32 or ch in "\t\n\r") and not 0x7F <= ord(ch) <= 0x9F)
    return cleaned.encode("utf-8"), len(text) - len(cleaned)
def canonical_date(value: Any) -> str | None:
    if value is None: return None
    if isinstance(value, str) and len(value) == 4 and value.isdigit(): return value + "-01-01"
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except (AttributeError, TypeError, ValueError):
            return None

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source-manifest", action="append", default=[], metavar="NAME=PATH")
    p.add_argument("--register-map", action="append", default=[], metavar="NAME:LEGACY=CANONICAL")
    p.add_argument("--persona", required=True); p.add_argument("--author-identity", action="append", required=True)
    p.add_argument("--legacy-persona-alias", action="append", default=[])
    p.add_argument("--output-dir", required=True, type=Path); p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv); out = args.output_dir.expanduser(); private(out)
    sources: dict[str, Path] = {}
    for raw in args.source_manifest:
        name, path = assignment(raw, "--source-manifest"); q = Path(path).expanduser(); private(q)
        if name in sources or not q.is_file() or q.is_symlink(): raise ValueError("invalid source manifest")
        sources[name] = q
    maps: dict[tuple[str,str],str] = {}
    for raw in args.register_map:
        k, value = assignment(raw, "--register-map"); name, sep, legacy = k.partition(":")
        if not sep or not name or not legacy or "." not in value or (name,legacy) in maps: raise ValueError("invalid register map")
        maps[name,legacy] = value
    rows: list[dict[str, Any]] = []; map_rows: list[dict[str, Any]] = []; copied: dict[str, bytes] = {}; skipped = Counter(); controls_removed = 0
    for name, manifest in sorted(sources.items()):
        for line, raw in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip(): continue
            entry = json.loads(raw)
            if entry.get("ai_status") != "pre_ai_human": skipped[str(entry.get("ai_status","missing"))] += 1; continue
            # Do NOT relabel non-baseline material as identity_baseline just because it is
            # pre_ai_human: if the source row declares a corpus role / use / split / consent,
            # it must agree with the baseline stamping below, or we refuse rather than
            # silently converting excluded/impostor/test material into training material.
            for field, expected in (("corpus_role", "identity_baseline"), ("split", "baseline"), ("consent_status", "author_consent"), ("use", ["voice_profile"])):
                if entry.get(field) is not None and entry.get(field) != expected: raise ValueError(f"source entry {entry.get('id')} declares {field}={entry.get(field)!r}; refusing to relabel as identity_baseline")
            # Never launder a conflicting identity or an impostor marker into a clean
            # author-baseline attestation: the row's own persona/author/role must match the
            # authorized identity, and any impostor marker refuses outright.
            if entry.get("impostor"): raise ValueError(f"source entry {entry.get('id')} is marked impostor; refusing to attest as author baseline")
            if entry.get("impostor_for") not in (None, ""): raise ValueError(f"source entry {entry.get('id')} carries an explicit impostor marker; refusing to attest as author baseline")
            if "register_match" in entry or "topic_match" in entry: raise ValueError(f"source entry {entry.get('id')} carries impostor-comparison metadata; refusing to attest as author baseline")
            if entry.get("role") is not None and entry.get("role") != "author": raise ValueError(f"source entry {entry.get('id')} declares role={entry.get('role')!r}, not author")
            authorized_personas = {args.persona, *args.legacy_persona_alias}
            if entry.get("persona") is not None and entry.get("persona") not in authorized_personas: raise ValueError(f"source entry {entry.get('id')} declares persona={entry.get('persona')!r}, outside the authorized personas")
            for who in (entry.get("author"), entry.get("identity")):
                if who is not None and who not in set(args.author_identity): raise ValueError(f"source entry {entry.get('id')} declares author {who!r} outside the authorized identities")
            key = name, entry.get("register")
            if key not in maps: raise ValueError(f"missing map for {name}:{entry.get('register')}")
            text_path = resolve(manifest, entry["path"]); raw = text_path.read_bytes()
            declared = entry.get("content_hash", entry.get("content_sha256"))
            if declared is not None and sha(raw) != declared: raise ValueError(f"source bytes for {entry.get('id')} drifted from the declared content hash")
            data, removed = sanitize(raw); controls_removed += removed; digest = sha(data); ident = f"{name}:{entry['id']}:{line}:{digest[7:19]}"
            if digest in copied: skipped["exact_duplicate"] += 1; continue
            copied[digest] = data; target = f"texts/{digest[7:9]}/{digest[9:11]}/{digest[7:]}.txt"
            rows.append({"id":ident,"path":target,"author":args.author_identity[0],"persona":args.persona,"register":maps[key],"date_written":canonical_date(entry.get("date_written")),"ai_status":"pre_ai_human","content_hash":digest,"corpus_role":"identity_baseline","use":["voice_profile"],"split":"baseline","source":f"private_registry:{name}"})
            # document_local deliberately represents every imported artifact as
            # a document; its closed map schema reserves email/message units for
            # the native acquisition routes.
            map_rows.append({"schema":SCHEMA_MAP,"source_id":ident,"private_document_locator":sha((name+":"+digest).encode()),"private_entry_locator":sha(ident.encode()),"unit_kind":"document","unit_index":0,"unit_count":1})
    rows.sort(key=lambda x:x["id"]); map_rows.sort(key=lambda x:x["source_id"])
    manifest_text = "".join(json.dumps(x,sort_keys=True)+"\n" for x in rows); map_text = "".join(json.dumps(x,sort_keys=True)+"\n" for x in map_rows)
    manifest_hash, map_hash = sha(manifest_text.encode()), exporter._document_map_hash(map_rows)
    attest = {"schema":SCHEMA_ATTEST,"source_manifest_sha256":manifest_hash,"document_map_hash":map_hash,"persona":args.persona,"authorized_by":args.persona,"basis":"self","attested_at":datetime.now(timezone.utc).replace(microsecond=0).isoformat(),"legacy_persona_aliases":sorted(set(args.legacy_persona_alias)) ,"author_identities":sorted(set(args.author_identity)),"corpus_role":"identity_baseline","use":["voice_profile"],"consent_status":"author_consent","allowed_ai_status":["pre_ai_human"]}
    summary={"records":len(rows),"unique_texts":len(copied),"controls_removed":controls_removed,"registers":dict(sorted(Counter(x["register"] for x in rows).items())),"skipped":dict(sorted(skipped.items())),"manifest_sha256":manifest_hash,"document_map_sha256":map_hash}
    if not args.dry_run:
        for digest,data in copied.items():
            dest=out / "texts" / digest[7:9] / digest[9:11] / f"{digest[7:]}.txt"; dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(data)
        atomic(out/"draft_manifest.jsonl",manifest_text); atomic(out/"document_map.jsonl",map_text); atomic(out/"document_attestation.json",json.dumps(attest,sort_keys=True,indent=2)+"\n"); atomic(out/"summary.json",json.dumps(summary,sort_keys=True,indent=2)+"\n")
    print(json.dumps(summary,sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
