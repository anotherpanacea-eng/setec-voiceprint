#!/usr/bin/env python3
"""Closed, no-prose producer adapter for the Gmail author-corpus recipe.

This is deliberately a launcher, not a second implementation of acquisition,
manifest validation, deduplication, or author export.  It accepts the
voicewright-owned request envelope, derives one fixed argv vector, captures the
domain program's streams, and returns only receipt identities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Imported surfaces use the shared packaging resolver.  A direct nested-script
# launch cannot import the package until bootstrapped, so fall back to this
# file's own fixed position: scripts/setec/surfaces/<module>.py.  Anchoring on
# __file__ rather than sys.path[0] keeps the fallback correct when the module is
# imported, run with -m, or run with -c, none of which put the script directory
# on sys.path[0].
try:
    from setec.paths import scripts_dir
except ModuleNotFoundError:  # direct launch with an empty PYTHONPATH
    SCRIPTS = Path(__file__).resolve().parents[2]
else:
    SCRIPTS = scripts_dir()
if not (SCRIPTS / "acquire_gmail_sent.py").is_file():  # pragma: no cover
    raise RuntimeError("SETEC scripts directory not found")

REQUEST_SCHEMA = "setec-gmail-author-pipeline-request/1"
TASK_SURFACE = "voice_coherence_acquisition"
RESULT_KEYS = {"request_schema", "action", "stage_id", "status", "config_sha256",
               "input_identities", "domain_receipts", "output_identities", "lineage_receipt_sha256", "reason_category"}
STAGES = ("01_source_smoke", "02_source_approval", "03_source_acquire",
          "04_manifest_validate", "05_near_duplicate_filter", "06_package_smoke",
          "07_author_package")
ALLOWED = {
    "01_source_smoke": {"run", "verify"}, "02_source_approval": {"approve", "verify"},
    "03_source_acquire": {"run", "resume", "verify"}, "04_manifest_validate": {"run", "verify"},
    "05_near_duplicate_filter": {"run", "verify"}, "06_package_smoke": {"approve", "verify"},
    "07_author_package": {"run", "verify"},
}
SOURCE_KEYS = {"adapter", "mbox", "own_addresses", "persona", "author", "register", "since",
    "until", "sent_label_token", "recipient_map", "name_map", "own_signature_lines",
    "consent_status", "min_words_per_piece", "max_items", "allow_empty", "output_dir",
    "manifest", "smoke_dir", "smoke_since", "smoke_until"}
CORPUS_KEYS = {"strict_manifest", "check_conflict_copies", "dedup_manifest", "dedup_report",
    "dedup_threshold", "dedup_num_perm", "dedup_shingle_size", "hmac_key", "allowed_ai_status",
    "register_map", "package_smoke_dir", "package_dir", "producer_envelope",
    "smoke_max_records", "smoke_max_text_bytes"}
LINEAGE_KEYS = {"schema", "stage_id", "config_sha256", "input_identities",
                "domain_receipts", "output_identities", "domain_identity"}
IDENTITY_KEYS = {"kind", "label", "identity_kind", "identity"}
AUTHOR_ENVELOPE_KEYS = {"schema_version", "task_surface", "tool", "version", "available",
                        "target", "baseline", "results", "claim_license",
                        "claim_license_rendered", "warnings", "ai_status"}

class Refusal(ValueError): pass

def _canon(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8") + b"\n"

def _sha_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def _is_sha256(value: Any) -> bool:
    return (type(value) is str and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))

def _owner_file(path: Path, *, root: Path | None = None) -> None:
    try: st = path.lstat()
    except OSError as exc: raise Refusal("private artifact unavailable") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refusal("private artifact policy refused")
    if root is not None:
        try: path.resolve().relative_to(root)
        except ValueError as exc: raise Refusal("private artifact escapes root") from exc

def _owner_dir(path: Path, *, root: Path) -> None:
    try: st = path.lstat()
    except OSError as exc: raise Refusal("private directory unavailable") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refusal("private directory policy refused")
    try: path.resolve().relative_to(root)
    except ValueError as exc: raise Refusal("private directory escapes root") from exc

def _safe_dir(parent: Path, name: str, *, root: Path) -> Path:
    path = parent / name
    if path.exists(): _owner_dir(path, root=root)
    else:
        try: path.mkdir(mode=0o700)
        except OSError as exc: raise Refusal("private directory creation refused") from exc
        _owner_dir(path, root=root)
    return path

def _preflight_logs(logs: Path, stages: list[str], *, root: Path) -> None:
    """Reject even dangling symlink leaves before any domain process starts."""
    for stage in stages:
        for suffix in ("stdout", "stderr"):
            candidate = logs / f"{stage}.domain.{suffix}"
            try:
                candidate.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise Refusal("domain log policy refused") from exc
            _owner_file(candidate, root=root)

def _preflight_receipts(directory: Path, prior_stages: list[str], current_stage: str,
                        *, root: Path) -> None:
    """Resolve every caller-addressable receipt leaf before a child starts."""
    for stage in prior_stages + [current_stage]:
        candidate = directory / f"{stage}.json"
        try:
            candidate.lstat()
        except FileNotFoundError:
            if stage in prior_stages:
                raise Refusal("prior lineage receipt unavailable")
            continue
        except OSError as exc:
            raise Refusal("lineage receipt policy refused") from exc
        _owner_file(candidate, root=root)

def _append_log(path: Path, action: str, data: str) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise Refusal("domain log policy refused") from exc
    with os.fdopen(fd, "ab") as fh:
        fh.write(f"\n[setec-pipeline {action}]\n".encode("ascii"))
        fh.write(data.encode("utf-8"))

def _run_child(logs: Path, stage: str, action: str, argv: list[str], *,
               inherit_stdin: bool = False) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            argv, stdin=None if inherit_stdin else subprocess.DEVNULL,
            capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refusal("domain verifier unavailable") from exc
    _append_log(logs / f"{stage}.domain.stdout", action, proc.stdout)
    _append_log(logs / f"{stage}.domain.stderr", action, proc.stderr)
    return proc

def _private_root(raw: Any) -> Path:
    if type(raw) is not str or not Path(raw).is_absolute(): raise Refusal("private_root must be absolute")
    root = Path(raw)
    try: st = root.lstat()
    except OSError as exc: raise Refusal("private root unavailable") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refusal("private root policy refused")
    return root.resolve()

def _rel(root: Path, raw: Any, *, exists: bool = False) -> Path:
    if type(raw) is not str or not raw or Path(raw).is_absolute() or ".." in Path(raw).parts:
        raise Refusal("invalid private relative path")
    path = root / raw
    current = root
    for part in Path(raw).parts:
        current = current / part
        if current.exists() and current.is_symlink(): raise Refusal("symlinked private path refused")
    try: path.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc: raise Refusal("private path escapes root") from exc
    if exists: _owner_file(path, root=root)
    return path

def _validate(req: Any) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str, str]:
    if not isinstance(req, dict) or set(req) != {"schema", "action", "stage_id", "private_root", "run_root", "config", "prior"}:
        raise Refusal("request shape refused")
    if req["schema"] != REQUEST_SCHEMA or req["stage_id"] not in STAGES or req["action"] not in {"run","approve","resume","verify"}:
        raise Refusal("request selector refused")
    if req["action"] not in ALLOWED[req["stage_id"]]: raise Refusal("action refused for stage")
    root = _private_root(req["private_root"])
    run_dir = _rel(root, req["run_root"])
    try: run_st = run_dir.lstat()
    except OSError as exc: raise Refusal("run root unavailable") from exc
    if stat.S_ISLNK(run_st.st_mode) or not stat.S_ISDIR(run_st.st_mode) or run_st.st_uid != os.getuid() or run_st.st_mode & 0o077:
        raise Refusal("run root policy refused")
    config = req["config"]
    if not isinstance(config, dict) or set(config) != {"source", "corpus"}: raise Refusal("config shape refused")
    source, corpus = config["source"], config["corpus"]
    if not isinstance(source, dict) or set(source) != SOURCE_KEYS or not isinstance(corpus, dict) or set(corpus) != CORPUS_KEYS:
        raise Refusal("config keys refused")
    if source["adapter"] != "gmail_takeout_sent/1" or source["register"] != "personal" or source["consent_status"] != "author_consent": raise Refusal("source policy refused")
    for key in ("persona", "sent_label_token"):
        if type(source[key]) is not str or not source[key]: raise Refusal("source string refused")
    for key in ("author", "since", "until", "recipient_map", "name_map", "own_signature_lines"):
        if source[key] is not None and type(source[key]) is not str: raise Refusal("source optional refused")
    for key in ("since", "until", "smoke_since", "smoke_until"):
        if source[key] is not None:
            try: date.fromisoformat(source[key])
            except (TypeError, ValueError) as exc: raise Refusal("date refused") from exc
    if source["smoke_since"] is None and source["smoke_until"] is None: raise Refusal("bounded smoke window required")
    if type(source["own_addresses"]) is not list or not source["own_addresses"] or any(type(x) is not str or not x for x in source["own_addresses"]): raise Refusal("own_addresses refused")
    if type(source["allow_empty"]) is not bool or type(source["max_items"]) is not int or source["max_items"] <= 0 or type(source["min_words_per_piece"]) is not int or source["min_words_per_piece"] <= 0: raise Refusal("source type refused")
    if type(corpus["strict_manifest"]) is not bool or corpus["strict_manifest"] is not True or type(corpus["check_conflict_copies"]) is not bool or corpus["check_conflict_copies"] is not True: raise Refusal("corpus policy refused")
    if type(corpus["register_map"]) is not dict or set(corpus["register_map"]) != {"personal"} or type(corpus["register_map"]["personal"]) is not str: raise Refusal("register map refused")
    if type(corpus["allowed_ai_status"]) is not list or not corpus["allowed_ai_status"] or corpus["allowed_ai_status"] != sorted(set(corpus["allowed_ai_status"])): raise Refusal("ai status refused")
    if any(type(x) is not str or not x for x in corpus["allowed_ai_status"]): raise Refusal("ai status refused")
    for key in ("dedup_num_perm", "dedup_shingle_size", "smoke_max_records", "smoke_max_text_bytes"):
        if type(corpus[key]) is not int or corpus[key] <= 0: raise Refusal("corpus integer refused")
    if type(corpus["dedup_threshold"]) not in (int, float) or isinstance(corpus["dedup_threshold"], bool) or not 0 < corpus["dedup_threshold"] <= 1: raise Refusal("dedup threshold refused")
    for key in ("mbox", "hmac_key"):
        _rel(root, source[key] if key == "mbox" else corpus[key], exists=True)
    for key in ("output_dir","manifest","smoke_dir"):
        _rel(root, source[key])
    for key in ("dedup_manifest","dedup_report","package_smoke_dir","package_dir","producer_envelope"):
        _rel(root, corpus[key])
    paths = [str(_rel(root, source[key])) for key in ("output_dir", "manifest", "smoke_dir")] + [str(_rel(root, corpus[key])) for key in ("dedup_manifest", "dedup_report", "package_smoke_dir", "package_dir", "producer_envelope")]
    if len(paths) != len(set(paths)) or Path(corpus["package_smoke_dir"]).parent != Path(corpus["package_dir"]).parent: raise Refusal("output collision refused")
    prior = req["prior"]
    if type(prior) is not list or any(
        type(x) is not dict or set(x) != {"stage_id", "receipt_sha256", "domain_identity"}
        or x["stage_id"] not in STAGES or not _is_sha256(x["receipt_sha256"])
        or not _is_sha256(x["domain_identity"])
        for x in prior
    ):
        raise Refusal("prior refused")
    return root, run_dir, source, corpus, req["action"], req["stage_id"]

def _argv(root: Path, s: dict[str, Any], c: dict[str, Any], action: str, stage: str) -> tuple[list[str], Path]:
    gmail = [sys.executable, str(SCRIPTS / "acquire_gmail_sent.py")]
    base = ["--mbox-path", str(_rel(root,s["mbox"],exists=True)), "--own-address", *s["own_addresses"], "--persona", s["persona"], "--register", "personal", "--consent-status", "author_consent", "--max-items", str(s["max_items"]), "--min-words-per-piece", str(s["min_words_per_piece"]), "--sent-label-token", s["sent_label_token"]]
    for flag,key in (("--author","author"),("--since","since"),("--until","until"),("--recipient-map-path","recipient_map"),("--name-map","name_map"),("--own-signature-lines","own_signature_lines")):
        if s[key] is not None: base += [flag, str(_rel(root,s[key])) if key.endswith("map") or key == "own_signature_lines" else s[key]]
    if s["allow_empty"]: base.append("--allow-empty")
    if stage == "01_source_smoke":
        if action == "verify": return gmail + ["validate-smoke", "--mbox-path", str(_rel(root,s["mbox"],exists=True)), "--smoke-dir", str(_rel(root,s["smoke_dir"]))], _rel(root,s["smoke_dir"]) / ".smoke_descriptor.json"
        argv = gmail + ["smoke", *base]
        if s["smoke_since"] is not None: argv += ["--since", s["smoke_since"]]
        if s["smoke_until"] is not None: argv += ["--until", s["smoke_until"]]
        argv += ["--output-dir", str(_rel(root,s["smoke_dir"]))]
        return argv, _rel(root,s["smoke_dir"]) / ".smoke_descriptor.json"
    if stage == "02_source_approval": return gmail + (["verify-approval", *base, "--output-dir", str(_rel(root,s["output_dir"]))] if action == "verify" else ["approve-smoke", "--mbox-path", str(_rel(root,s["mbox"],exists=True)), "--smoke-dir", str(_rel(root,s["smoke_dir"])), "--output-dir", str(_rel(root,s["output_dir"]))]), _rel(root,s["output_dir"]) / ".live_smoke_passed"
    if stage == "03_source_acquire": return gmail + (["verify-acquisition" if action == "verify" else "acquire", *base, "--output-dir", str(_rel(root,s["output_dir"])), "--emit-manifest", str(_rel(root,s["manifest"]))]), _rel(root,s["manifest"])
    if stage == "04_manifest_validate": return [sys.executable,str(SCRIPTS/"manifest_validator.py"),str(_rel(root,s["manifest"])),"--strict","--check-conflict-copies","--json"], _rel(root,s["manifest"])
    if stage == "05_near_duplicate_filter":
        argv=[sys.executable,str(SCRIPTS/"near_dup_dedup.py"),str(_rel(root,s["manifest"])),"--threshold",str(c["dedup_threshold"]),"--num-perm",str(c["dedup_num_perm"]),"--shingle-size",str(c["dedup_shingle_size"]),"--json"]
        if action == "verify": argv += ["--dry-run","--verify-out",str(_rel(root,c["dedup_manifest"])),"--verify-report",str(_rel(root,c["dedup_report"]))]
        else: argv += ["--out",str(_rel(root,c["dedup_manifest"]))]
        return argv, _rel(root,c["dedup_manifest"])
    out = c["package_smoke_dir"] if stage == "06_package_smoke" else c["package_dir"]
    argv = [sys.executable,str(SCRIPTS/"author_corpus_export.py"),"--source-manifest",f"gmail_sent={_rel(root,c['dedup_manifest'])}","--register-map",f"gmail_sent:personal={c['register_map']['personal']}","--persona",s["persona"],"--hmac-key",str(_rel(root,c["hmac_key"],exists=True)),"--output-dir",str(_rel(root,out))]
    for status in c["allowed_ai_status"]: argv += ["--allowed-ai-status",status]
    if stage == "06_package_smoke": argv += ["--max-records",str(c["smoke_max_records"]),"--max-text-bytes",str(c["smoke_max_text_bytes"]),"--live-smoke-confirmed"]
    if action == "verify":
        argv = [item for item in argv if item != "--live-smoke-confirmed"] + ["--verify-existing"]
    argv.append("--json")
    return argv, _rel(root,out) / "producer_receipt.json"

def _config_sha(config: dict[str, Any]) -> str:
    return hashlib.sha256(_canon(config)).hexdigest()

def _domain_identity(stage: str, config_sha: str, output: list[dict[str, Any]]) -> str:
    payload = {"stage_id":stage,"config_sha256":config_sha,"output_identities":output}
    return hashlib.sha256(b"setec-gmail-author-pipeline-domain/1\n" + _canon(payload)).hexdigest()

def _output_paths(root: Path, s: dict[str, Any], c: dict[str, Any], stage: str) -> list[tuple[str, Path]]:
    if stage == "01_source_smoke": return [("smoke_descriptor",_rel(root,s["smoke_dir"])/".smoke_descriptor.json"),("smoke_manifest",_rel(root,s["smoke_dir"])/"draft_manifest.jsonl")]
    if stage == "02_source_approval": return [("source_approval_receipt",_rel(root,s["output_dir"])/".live_smoke_passed")]
    if stage == "03_source_acquire": return [("source_manifest",_rel(root,s["manifest"]))]
    if stage == "04_manifest_validate": return [("validated_manifest",_rel(root,s["manifest"]))]
    if stage == "05_near_duplicate_filter": return [("dedup_manifest",_rel(root,c["dedup_manifest"])),("dedup_report",_rel(root,c["dedup_report"]))]
    if stage == "06_package_smoke": return [("bounded_producer_receipt",_rel(root,c["package_smoke_dir"])/"producer_receipt.json"),("package_smoke_receipt",_rel(root,c["package_smoke_dir"]).parent/".author_corpus_export_live_smoke.json")]
    return [("producer_receipt",_rel(root,c["package_dir"])/"producer_receipt.json"),("producer_envelope",_rel(root,c["producer_envelope"]))]

def _output_identities(root: Path, s: dict[str, Any], c: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    out=[]
    for label,path in _output_paths(root,s,c,stage):
        _owner_file(path, root=root)
        out.append({"kind":"file","label":label,"identity_kind":"sha256","identity":_sha_path(path)})
    return out

def _lineage(stage: str, config: dict[str, Any], output: list[dict[str, Any]], inputs: list[dict[str, Any]]) -> dict[str, Any]:
    config_sha = _config_sha(config)
    domain = _domain_identity(stage, config_sha, output)
    return {"schema":"setec-gmail-author-pipeline-lineage/1","stage_id":stage,
            "config_sha256":config_sha,"input_identities":inputs,
            "domain_receipts":[{"label":"domain_identity","relative_path":None,"sha256":None,"domain_identity":domain}],
            "output_identities":output,"domain_identity":domain}

def _envelope(action: str, stage: str, config: dict[str, Any], status: str, output: list[dict[str, Any]] | None = None, lineage_sha: str | None = None, reason: str | None = None, inputs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    output = output or []
    config_sha = _config_sha(config)
    domain = _domain_identity(stage, config_sha, output) if status == "completed" else None
    receipts = [] if domain is None else [{"label":"domain_identity","relative_path":None,"sha256":None,"domain_identity":domain}]
    results = {"request_schema":REQUEST_SCHEMA,"action":action,"stage_id":stage,"status":status,"config_sha256":config_sha,"input_identities":inputs or [],"domain_receipts":receipts,"output_identities":output,"lineage_receipt_sha256":lineage_sha,"reason_category":reason}
    return {"schema_version":"1.0","available":status == "completed","results":results}

def _write_receipt(run_dir: Path, stage: str, lineage: dict[str, Any]) -> str:
    directory = _safe_dir(run_dir, "producer-receipts", root=run_dir)
    target = directory / f"{stage}.json"
    payload = _canon(lineage)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _owner_file(target)
        if target.read_bytes() != payload: raise Refusal("existing lineage receipt mismatched")
        return _sha_path(target)
    with os.fdopen(fd, "wb") as fh: fh.write(payload)
    return hashlib.sha256(payload).hexdigest()

def _valid_identity_list(value: Any, *, prior: bool = False) -> bool:
    if type(value) is not list:
        return False
    expected_kind = "prior_domain" if prior else "file"
    for item in value:
        if (type(item) is not dict or set(item) != IDENTITY_KEYS
                or item["kind"] != expected_kind
                or type(item["label"]) is not str or not item["label"]
                or item["identity_kind"] != "sha256"
                or not _is_sha256(item["identity"])):
            return False
    return True

def _strict_author_envelope(value: Any, receipt: dict[str, Any], *, verifying: bool) -> dict[str, Any]:
    """Validate the domain command's exact ordinary normalized envelope.

    The domain process has already rebuilt and verified the package.  This
    adapter check closes only the public-envelope binding that the exporter
    intentionally owns: exact normalized shape, receipt, claim license, and
    the two action-specific warning sets.
    """
    if type(value) is not dict or set(value) != AUTHOR_ENVELOPE_KEYS:
        raise Refusal("author export envelope shape refused")
    if (value["schema_version"] != "1.0" or value["task_surface"] != TASK_SURFACE
            or value["tool"] != "author_corpus_export" or value["available"] is not True
            or type(value["version"]) is not str or not value["version"]
            or value["target"] != {"path": None, "words": 0}
            or value["baseline"] is not None or value["ai_status"] is not None
            or type(value["results"]) is not dict
            or set(value["results"]) != {"producer_receipt"}
            or value["results"]["producer_receipt"] != receipt):
        raise Refusal("author export envelope binding refused")
    license_value = value["claim_license"]
    if type(license_value) is not dict or set(license_value) != {
        "task_surface", "licenses", "does_not_license", "comparison_set",
        "length_range_words", "register_match", "language_match", "fpr_target",
        "confidence_interval_95", "additional_caveats", "references",
    }:
        raise Refusal("author export claim license shape refused")
    counts = receipt.get("counts")
    registers = counts.get("by_register") if type(counts) is dict else None
    if (license_value["task_surface"] != TASK_SURFACE
            or license_value["comparison_set"] != {"records": counts.get("records") if type(counts) is dict else None}
            or license_value["register_match"] != (sorted(registers) if type(registers) is dict else None)
            or type(license_value["licenses"]) is not str
            or type(license_value["does_not_license"]) is not str
            or type(value["claim_license_rendered"]) is not str
            or not value["claim_license_rendered"]):
        raise Refusal("author export claim license binding refused")
    base_warnings = []
    if receipt.get("record_atomic_degraded") is True:
        base_warnings.append(
            "record_atomic_degraded: stable grouping was unavailable; consumers "
            "must restrict this package to train-only, non-comparative use."
        )
    expected_warnings = base_warnings + (["verify-existing: package was verified without publication"] if verifying else [])
    if value["warnings"] != expected_warnings:
        raise Refusal("author export warning posture refused")
    return value

def _check_prior(root: Path, run_dir: Path, logs: Path, s: dict[str, Any], c: dict[str, Any], stage: str, prior: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-verify every predecessor through its owning domain verifier.

    Hashing the lineage receipt and the artifacts it lists is NOT sufficient
    here.  A stage's `output_identities` name only its headline artifacts -- for
    stage 03, the manifest -- while its verifier also revalidates the committed
    per-record text files, sidecars, hashes, and locators that no receipt hash
    covers.  Dropping the re-run makes predecessor authentication quadratic-free
    but blind to tampering with those files.  See the handoff for the
    measurement and the proposed narrower fix.
    """
    expected = list(STAGES[:STAGES.index(stage)])
    if [x["stage_id"] for x in prior] != expected: raise Refusal("prior ordering refused")
    inputs: list[dict[str, Any]] = []
    config = {"source": s, "corpus": c}
    for item in prior:
        prior_stage = item["stage_id"]
        path = run_dir / "producer-receipts" / f"{prior_stage}.json"
        _owner_file(path, root=run_dir)
        if _sha_path(path) != item["receipt_sha256"]: raise Refusal("prior receipt hash refused")
        try:
            raw = path.read_bytes()
            stored = json.loads(raw)
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            raise Refusal("prior receipt unreadable") from exc
        if (type(stored) is not dict or set(stored) != LINEAGE_KEYS
                or not _valid_identity_list(stored.get("input_identities"), prior=True)
                or not _valid_identity_list(stored.get("output_identities"))):
            raise Refusal("prior lineage shape refused")
        argv, _ = _argv(root, s, c, "verify", prior_stage)
        proc = _run_child(logs, prior_stage, "verify", argv)
        child_json = _safe_json(proc.stdout)
        if proc.returncode or (type(child_json) is dict and child_json.get("available") is False):
            raise Refusal("prior verifier refused")
        output = _output_identities(root, s, c, prior_stage)
        expected_lineage = _lineage(prior_stage, config, output, inputs)
        if (stored != expected_lineage or raw != _canon(expected_lineage)
                or item["domain_identity"] != expected_lineage["domain_identity"]):
            raise Refusal("prior lineage refused")
        inputs.append({"kind":"prior_domain","label":prior_stage,"identity_kind":"sha256",
                       "identity":expected_lineage["domain_identity"]})
    return inputs

def _safe_json(text: str) -> Any:
    try: return json.loads(text)
    except (TypeError, ValueError): return None

def _publish_file_noreplace(path: Path, payload: bytes, *, root: Path) -> None:
    if path.parent.exists():
        _owner_dir(path.parent, root=root)
    else:
        try:
            path.parent.mkdir(mode=0o700, parents=True)
        except OSError as exc:
            raise Refusal("private output directory creation refused") from exc
        _owner_dir(path.parent, root=root)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        raise Refusal("private output publication refused") from exc
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)

def _parse_request(request_path: Path) -> tuple[Path, Path, dict[str, Any], dict[str, Any], str, str, list[dict[str, Any]]]:
    """Read and validate the request itself.  Refusals here are bad input (exit 2)."""
    _owner_file(request_path)
    try: req = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise Refusal("request unreadable") from exc
    root,run_dir,s,c,action,stage = _validate(req)
    _owner_file(request_path, root=root)
    if request_path.parent.resolve() != run_dir.resolve(): raise Refusal("request must be a direct run-root child")
    return root, run_dir, s, c, action, stage, req["prior"]

def _execute(root: Path, run_dir: Path, s: dict[str, Any], c: dict[str, Any],
             action: str, stage: str, prior: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    """Perform at most one stage side effect.  Refusals here are policy (exit 3)."""
    config = {"source": s, "corpus": c}
    inputs: list[dict[str, Any]] = []
    # Create/validate both stateful directories before a child can run.
    receipt_dir = _safe_dir(run_dir, "producer-receipts", root=run_dir)
    logs = _safe_dir(run_dir, "logs", root=run_dir)
    prior_stages = [x["stage_id"] for x in prior]
    _preflight_logs(logs, prior_stages + [stage], root=run_dir)
    _preflight_receipts(receipt_dir, prior_stages, stage, root=run_dir)
    inputs = _check_prior(root, run_dir, logs, s, c, stage, prior)
    current_receipt = receipt_dir / f"{stage}.json"
    try:
        current_receipt.lstat()
    except FileNotFoundError:
        receipt_exists = False
    except OSError as exc:
        raise Refusal("current lineage receipt unavailable") from exc
    else:
        _owner_file(current_receipt, root=run_dir)
        receipt_exists = True
    if action in {"run", "approve"} and receipt_exists:
        raise Refusal("completed stage cannot repeat its side effect")
    if stage == "03_source_acquire" and action == "verify" and not receipt_exists:
        raise Refusal("stage 03 verify requires an existing lineage receipt")
    # A resumed, already-complete acquisition is a re-verification, never a
    # second acquisition.  Resume enters the acquisition continuation only
    # while the adapter lineage receipt is absent.
    domain_action = "verify" if stage == "03_source_acquire" and action == "resume" and receipt_exists else action
    argv, _ = _argv(root,s,c,domain_action,stage)
    if action == "approve" and not sys.stdin.isatty(): raise Refusal("interactive TTY required")
    if action == "approve" and stage == "06_package_smoke":
        print("The bounded package smoke will request one confirmation. Continue? [y/N]", file=sys.stderr)
        try: answer = input().strip().lower()
        except EOFError: answer = ""
        if answer not in {"y", "yes"}: raise Refusal("owner declined bounded package smoke")
    proc = _run_child(logs, stage, domain_action, argv, inherit_stdin=action == "approve")
    if proc.returncode != 0: raise Refusal("domain child refused")
    child_json = _safe_json(proc.stdout)
    if isinstance(child_json, dict) and child_json.get("available") is False:
        raise Refusal("domain child reported unavailable")
    if stage == "07_author_package":
        try:
            package_receipt = json.loads((_rel(root,c["package_dir"]) / "producer_receipt.json").read_text(encoding="utf-8"))
            verified_envelope = _strict_author_envelope(child_json, package_receipt, verifying=action == "verify")
            if action == "verify":
                producer_envelope = json.loads(_rel(root,c["producer_envelope"]).read_text(encoding="utf-8"))
                stored_envelope = _strict_author_envelope(producer_envelope, package_receipt, verifying=False)
                for key in AUTHOR_ENVELOPE_KEYS - {"warnings"}:
                    if stored_envelope[key] != verified_envelope[key]:
                        raise Refusal("stored author envelope differs from verified projection")
        except (OSError, UnicodeError, ValueError, KeyError, TypeError) as exc:
            raise Refusal("author package envelope refused") from exc
    if stage == "05_near_duplicate_filter" and action != "verify":
        _publish_file_noreplace(_rel(root,c["dedup_report"]), proc.stdout.encode("utf-8"), root=root)
    if stage == "07_author_package" and action != "verify":
        _publish_file_noreplace(_rel(root,c["producer_envelope"]), proc.stdout.encode("utf-8"), root=root)
    output = _output_identities(root,s,c,stage)
    lineage = _lineage(stage,config,output,inputs)
    receipt_sha = _write_receipt(run_dir,stage,lineage)
    return 0, _envelope(action,stage,config,"completed",output,receipt_sha,inputs=inputs)

def run_request(request_path: Path) -> tuple[int, dict[str, Any]]:
    root, run_dir, s, c, action, stage, prior = _parse_request(request_path)
    try:
        return _execute(root, run_dir, s, c, action, stage, prior)
    except Refusal:
        return 3, _envelope(action, stage, {"source": s, "corpus": c},
                            "refused", reason="policy_refused")

def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(prog="gmail_author_pipeline", add_help=True); p.add_argument("--json",action="store_true"); p.add_argument("--request",required=True)
    args=p.parse_args(argv)
    try: rc,envelope=run_request(Path(args.request))
    except (Refusal, OSError, UnicodeError, subprocess.SubprocessError, ValueError, TypeError):
        rc=2; envelope={"schema_version":"1.0","available":False,"results":{"request_schema":REQUEST_SCHEMA,"action":None,"stage_id":None,"status":"refused","config_sha256":None,"input_identities":[],"domain_receipts":[],"output_identities":[],"lineage_receipt_sha256":None,"reason_category":"bad_input"}}
    print(json.dumps(envelope,sort_keys=True,separators=(",",":")))
    return rc

if __name__ == "__main__": raise SystemExit(main())
