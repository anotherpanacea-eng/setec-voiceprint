"""Focused contract guards for the closed Gmail recipe facade."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
FACADE = SCRIPTS / "setec" / "surfaces" / "gmail_author_pipeline.py"
spec = importlib.util.spec_from_file_location("gmail_author_pipeline", FACADE)
assert spec and spec.loader
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def test_action_matrix_is_closed():
    assert P.ALLOWED["01_source_smoke"] == {"run", "verify"}
    assert P.ALLOWED["02_source_approval"] == {"approve", "verify"}
    assert P.ALLOWED["03_source_acquire"] == {"run", "resume", "verify"}
    assert P.ALLOWED["06_package_smoke"] == {"approve", "verify"}


def test_relative_path_refuses_symlink_component_before_child(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    os.chmod(root, 0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(P.Refusal):
        P._rel(root, "link/output")


def test_domain_identity_and_lineage_are_action_invariant():
    output = [{"kind": "file", "label": "x", "identity_kind": "sha256", "identity": "a" * 64}]
    config = {"source": {}, "corpus": {}}
    lineage = P._lineage("01_source_smoke", config, output, [])
    assert lineage["schema"] == "setec-gmail-author-pipeline-lineage/1"
    assert lineage["domain_identity"] == P._domain_identity("01_source_smoke", lineage["config_sha256"], output)


def test_stage05_verify_uses_read_only_verifier(tmp_path):
    root = tmp_path / "root"; root.mkdir(); os.chmod(root, 0o700)
    for name in ("m", "key"):
        path = root / name; path.write_text("x"); os.chmod(path, 0o600)
    source = {"mbox":"m","own_addresses":["a"],"persona":"p","author":None,"register":"personal","since":None,"until":None,"sent_label_token":"Sent","recipient_map":None,"name_map":None,"own_signature_lines":None,"consent_status":"author_consent","min_words_per_piece":1,"max_items":1,"allow_empty":False,"output_dir":"out","manifest":"out/m.jsonl","smoke_dir":"smoke","smoke_since":"2020-01-01","smoke_until":None}
    corpus = {"dedup_manifest":"out/d.jsonl","dedup_report":"out/r.json","dedup_threshold":0.8,"dedup_num_perm":8,"dedup_shingle_size":2,"package_smoke_dir":"pkg/smoke","package_dir":"pkg/full","register_map":{"personal":"email.personal"},"hmac_key":"key","allowed_ai_status":["unknown"],"smoke_max_records":1,"smoke_max_text_bytes":1,"strict_manifest":True,"check_conflict_copies":True,"producer_envelope":"out/e.json"}
    argv, _ = P._argv(root, source, corpus, "verify", "05_near_duplicate_filter")
    assert "--dry-run" in argv and "--verify-out" in argv and "--verify-report" in argv and "--out" not in argv
