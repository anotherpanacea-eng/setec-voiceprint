#!/usr/bin/env python3
"""Spec 73 / H2 register-composition sweep: behavioral tests.

The sweep reads a manifest, classifies each scoped document with the
receipt-bound H1 classifier, and publishes one aggregate count report. Most of
its failure modes refuse loudly with a golden error envelope. These tests cover
the ones that would not: wrong counts, the wrong document, a lost or doubled row
on resume, a changed scope that still commits, and private values leaking into
an output stream.

Nearly every test drives ``main()`` (or the real ``python3 register_sweep.py``
entry point) over a synthetic corpus and checks the report and streams it
produces. The aggregation equations are also checked directly against
``RegisterAggregate``, because a mixed corpus with every bucket populated is
easier to build there than through the classifier.

All fixtures are generated synthetic data. No private corpus, aggregate,
identifier, path, or prose enters the repository.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[2]
REGISTER_SWEEP_SCRIPT = SCRIPTS / "register_sweep.py"

import manifest_validator as mv  # type: ignore
import register_sweep as rs  # type: ignore

POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem semantics")


# --------------------------------------------------------------------------
# Goldens
# --------------------------------------------------------------------------

#: The three controlled error envelopes, byte for byte. Each is the complete
#: stdout of a refusal: no usage text, traceback, path, digest, filter value, or
#: caught exception text.
GOLDEN_BAD_INPUT = (
    b'{"ai_status":null,"available":false,"baseline":null,"claim_license":null,'
    b'"claim_license_rendered":null,"reason":"register composition sweep refused'
    b' invalid input","reason_category":"bad_input","results":{},'
    b'"schema_version":"1.0","target":{"path":null,"words":0},'
    b'"task_surface":"validation","tool":"register_sweep","version":"2.0.0",'
    b'"warnings":[]}\n'
)
GOLDEN_POLICY_REFUSED = (
    b'{"ai_status":null,"available":false,"baseline":null,"claim_license":null,'
    b'"claim_license_rendered":null,"reason":"register composition sweep refused'
    b' by policy","reason_category":"policy_refused","results":{},'
    b'"schema_version":"1.0","target":{"path":null,"words":0},'
    b'"task_surface":"validation","tool":"register_sweep","version":"2.0.0",'
    b'"warnings":[]}\n'
)
GOLDEN_INTERNAL_ERROR = (
    b'{"ai_status":null,"available":false,"baseline":null,"claim_license":null,'
    b'"claim_license_rendered":null,"reason":"register composition sweep '
    b'unavailable after internal failure","reason_category":"internal_error",'
    b'"results":{},"schema_version":"1.0","target":{"path":null,"words":0},'
    b'"task_surface":"validation","tool":"register_sweep","version":"2.0.0",'
    b'"warnings":[]}\n'
)

#: The frozen success ClaimLicense, written out here rather than read from the
#: module, so a change to the licensing posture fails a test.
GOLDEN_CLAIM_LICENSE = {
    "additional_caveats": [
        "Register family is a confounded heuristic proxy; this inventory can "
        "only prompt a human hand-check."
    ],
    "comparison_set": {},
    "confidence_interval_95": None,
    "does_not_license": (
        "Multimodality or semantic-mode explanation; calibration, accuracy, or "
        "a reportable distribution; source, source-family, or provenance "
        "analysis; corpus selection, exclusion, disposition, registration, "
        "activation, retagging, publication, or training authorization."
    ),
    "fpr_target": None,
    "language_match": [],
    "length_range_words": None,
    "licenses": (
        "Aggregate register-family count inventory for a hand-check of the "
        "explicitly scoped manifest slice."
    ),
    "references": [],
    "register_match": [],
    "task_surface": "validation",
}

#: The report's closed key set (spec order; the canonical bytes sort it).
SPEC_REPORT_KEYS = (
    "schema_version",
    "tool",
    "version",
    "taxonomy",
    "projected_manifest_sha256",
    "scoped_rows_sha256",
    "document_plan_sha256",
    "h1_receipt_sha256",
    "classifier_sha256",
    "mapping_sha256",
    "refusal_contract_sha256",
    "checkpoint_binding_sha256",
    "scope",
    "limits",
    "counts",
    "declared_family_inventory",
    "classified_family_inventory",
    "declared_by_classified_family",
    "refusal_inventory",
    "match_inventory",
    "assumptions",
    "claim_license",
    "warnings",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _complete_line(total: int) -> bytes:
    return (
        f"register sweep processing-complete: completed={total} total={total} "
        f"report_commit=pending\n"
    ).encode("ascii")


# --------------------------------------------------------------------------
# Fixtures and runners
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def binding() -> rs.H1Binding:
    """The real receipt-bound H1 namespace."""
    receipt_path, classifier_path = rs.default_h1_paths()
    return rs.load_h1_binding(
        receipt_path=receipt_path, classifier_path=classifier_path
    )


@pytest.fixture(scope="module")
def domains(binding: rs.H1Binding) -> rs.RegisterDomains:
    return rs.RegisterDomains.from_binding(binding).validate()


class _Sink:
    """A binary stream that records exactly what the runner wrote."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, data: Any) -> int:
        return self.buffer.write(data)

    def flush(self) -> None:
        return None


GOOD_ROW = {
    "path": "corpus/doc00000.txt",
    "use": ["baseline"],
    "ai_status": "pre_ai_human",
}


def _corpus(
    root: Path,
    count: int,
    *,
    body: str = "tiny synthetic document",
    use: list[str] | None = None,
    ai_status: str = "pre_ai_human",
    register: str | None = None,
    split: str | None = None,
    persona: str | None = None,
    corpus_name: str = "corpus",
    manifest_name: str = "manifest.jsonl",
) -> Path:
    """Write ``count`` tiny synthetic documents plus their manifest."""
    corpus = root / corpus_name
    corpus.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index in range(count):
        name = f"doc{index:05d}.txt"
        (corpus / name).write_text(f"{body} {index}\n", encoding="utf-8")
        row: dict[str, Any] = {
            "path": f"{corpus_name}/{name}",
            "use": list(use) if use is not None else ["baseline"],
            "ai_status": ai_status,
        }
        if register is not None:
            row["register"] = register
        if split is not None:
            row["split"] = split
        if persona is not None:
            row["persona"] = persona
        rows.append(row)
    manifest = root / manifest_name
    manifest.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return manifest


def _args(manifest: Path, out_dir: Path, *extra: str) -> list[str]:
    """CLI arguments for a run whose outputs live under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return [
        "--manifest", str(manifest),
        "--report-out", str(out_dir / "report.json"),
        "--checkpoint-dir", str(out_dir / "state"),
        *extra,
    ]


def _run(argv: list[str]) -> tuple[int, bytes, bytes]:
    """Drive ``main()`` with recording binary streams."""
    out = _Sink()
    err = _Sink()
    code = rs.main(argv, stdout=out, stderr=err)
    return code, out.buffer.getvalue(), err.buffer.getvalue()


def _report(out_dir: Path) -> dict[str, Any]:
    return json.loads((out_dir / "report.json").read_text(encoding="utf-8"))


def _shard_names(out_dir: Path) -> list[str]:
    return sorted(p.name for p in (out_dir / "state").iterdir())


def _cells(inventory: Any) -> list[dict[str, int]]:
    cells: list[dict[str, int]] = []
    for value in inventory.values():
        if set(value) == {"documents", "words"}:
            cells.append(value)
        else:
            cells.extend(value.values())
    return cells


def _interrupt_after_reads(
    monkeypatch: pytest.MonkeyPatch, argv: list[str], reads: int
) -> None:
    """Run until ``reads`` documents have been read, then interrupt."""
    real_read = rs.read_planned_document
    calls = {"n": 0}

    def interrupting_read(path: Any, expected_fingerprint: Any) -> bytes:
        calls["n"] += 1
        if calls["n"] > reads:
            raise KeyboardInterrupt("synthetic interruption")
        return real_read(path, expected_fingerprint)

    monkeypatch.setattr(rs, "read_planned_document", interrupting_read)
    try:
        with pytest.raises(KeyboardInterrupt):
            _run(argv)
    finally:
        monkeypatch.setattr(rs, "read_planned_document", real_read)


def _counting_reads(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every planned-document read without changing it."""
    reads: list[Any] = []
    real_read = rs.read_planned_document

    def counting_read(path: Any, expected_fingerprint: Any) -> bytes:
        reads.append(path)
        return real_read(path, expected_fingerprint)

    monkeypatch.setattr(rs, "read_planned_document", counting_read)
    return reads


@pytest.fixture(scope="module")
def two_shard_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """A 251-document corpus (two shards) and its uninterrupted fresh run.

    Shared by the resume tests: each one writes its own outputs under its own
    ``tmp_path`` but reuses this corpus, so every H2 digest matches the fresh
    control.
    """
    root = tmp_path_factory.mktemp("two-shard")
    manifest = _corpus(root, rs.SHARD_ROWS + 1)
    fresh = root / "fresh"
    code, out, err = _run(_args(manifest, fresh))
    assert code == 0
    assert _shard_names(fresh) == [
        "register-00000000.sqlite", "register-00000001.sqlite",
    ]
    return {
        "manifest": manifest,
        "report": (fresh / "report.json").read_bytes(),
        "out": out,
        "err": err,
    }


# --------------------------------------------------------------------------
# H1 identity
# --------------------------------------------------------------------------


def test_landed_receipt_and_classifier_bind_to_the_pinned_identities(
    binding: rs.H1Binding,
) -> None:
    receipt_path, _ = rs.default_h1_paths()
    receipt, data = rs.read_h1_receipt(receipt_path)
    assert rs.raw_sha256(data) == rs.H1_RECEIPT_SHA256
    assert binding.receipt_sha256 == rs.H1_RECEIPT_SHA256
    assert binding.classifier_sha256 == receipt["classifier_sha256"]
    assert binding.mapping_sha256 == receipt["mapping_sha256"]
    assert binding.refusal_contract_sha256 == receipt["refusal_contract_sha256"]


@pytest.mark.parametrize(
    "key,constant",
    [
        ("classifier_sha256", "H1_FINAL_CLASSIFIER_SHA256"),
        ("mapping_sha256", "H1_MAPPING_SHA256"),
        ("refusal_contract_sha256", "H1_REFUSAL_CONTRACT_SHA256"),
    ],
)
def test_receipt_identity_digests_are_pinned_to_the_ci_gate_constants(
    key: str, constant: str
) -> None:
    # Each of the three H1 identity digests is equality-pinned on its own, so
    # dropping any one pin is caught here rather than masked by a sibling pin.
    receipt_path, _ = rs.default_h1_paths()
    receipt, _ = rs.read_h1_receipt(receipt_path)
    assert receipt[key] == getattr(rs, constant)
    with pytest.raises(rs.PolicyRefused):
        rs.validate_h1_receipt({**receipt, key: "0" * 64})


@pytest.mark.parametrize("target", ["receipt", "classifier"])
def test_tampered_h1_artifact_refuses_before_any_document_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """A tampered receipt or classifier refuses with ``policy_refused`` before
    the first document body is read and before either output is created."""
    receipt_path, classifier_path = rs.default_h1_paths()
    references = tmp_path / "h1" / "references"
    scripts = tmp_path / "h1" / "scripts"
    references.mkdir(parents=True)
    scripts.mkdir(parents=True)
    receipt_copy = references / receipt_path.name
    classifier_copy = scripts / classifier_path.name
    shutil.copyfile(receipt_path, receipt_copy)
    shutil.copyfile(classifier_path, classifier_copy)
    if target == "receipt":
        receipt_copy.write_bytes(receipt_copy.read_bytes().replace(b"{", b"{ ", 1))
    else:
        classifier_copy.write_bytes(
            classifier_copy.read_bytes() + b"\n# tampered\n"
        )
    monkeypatch.setattr(
        rs, "default_h1_paths", lambda: (receipt_copy, classifier_copy)
    )
    reads = _counting_reads(monkeypatch)
    manifest = _corpus(tmp_path, 2)
    out_dir = tmp_path / "out"
    code, out, err = _run(_args(manifest, out_dir))
    assert (code, out, err) == (3, GOLDEN_POLICY_REFUSED, b"")
    assert reads == []
    assert not (out_dir / "report.json").exists()
    assert not (out_dir / "state").exists()


# --------------------------------------------------------------------------
# Manifest projection and scope
# --------------------------------------------------------------------------


_SOURCE_FIELD_VARIANTS: list[dict[str, Any]] = [
    {"source": ""},
    {"source_id": "abc-123"},
    {"source_family": "unclassified"},
    {"source": "café"},                 # NFC composed
    {"source": "café"},                # NFD decomposed, same grapheme
    {"source": "‮reversed‬"},      # bidi-looking valid Unicode
    {"source": "a" * 20000},                 # far past any owned-field bound
    {"source": 12345},
    {"source_id": None},
    {"source_family": ["a", "b"]},
    {"source": {"nested": True}},
    {"source_id": True},
    {"source": "x", "source_id": "y", "source_family": "unclassified"},
]


def test_source_fields_never_change_the_report(tmp_path: Path) -> None:
    """Metamorphic: the sweep is source-blind. Adding any value of the three
    unowned source fields to a row leaves the report and the envelope
    byte-identical, because every H2 digest is built from the owned fields
    only."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc00000.txt").write_text("tiny synthetic document\n", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    row = {**GOOD_ROW, "register": "personal", "split": "baseline", "persona": "alias-a"}

    def sweep(index: int, extra: dict[str, Any]) -> tuple[bytes, bytes]:
        manifest.write_text(json.dumps({**row, **extra}) + "\n", encoding="utf-8")
        out_dir = tmp_path / f"run{index}"
        code, out, _err = _run(_args(manifest, out_dir))
        assert code == 0, extra
        return (out_dir / "report.json").read_bytes(), out

    baseline = sweep(0, {})
    for index, variant in enumerate(_SOURCE_FIELD_VARIANTS, start=1):
        assert sweep(index, variant) == baseline, variant


def test_filters_select_the_expected_subset(tmp_path: Path) -> None:
    """``use`` is list membership; ``split``, ``ai_status``, and ``persona`` are
    exact equality; combined filters intersect."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    rows = []
    spec = [
        (["baseline", "idiolect"], "holdout", "ai_edited", "alpha"),
        (["baseline"], "train", "ai_edited", "alpha"),
        (["idiolect"], "holdout", "pre_ai_human", "beta"),
        (["idiolect"], "holdout", "ai_edited", None),
    ]
    for index, (use, split, ai_status, persona) in enumerate(spec):
        (corpus / f"doc{index}.txt").write_text(f"body {index}\n", encoding="utf-8")
        row: dict[str, Any] = {
            "path": f"corpus/doc{index}.txt", "use": use,
            "ai_status": ai_status, "split": split,
        }
        if persona is not None:
            row["persona"] = persona
        rows.append(row)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )

    cases = [
        (["--use", "idiolect"], 3),
        (["--use", "baseline"], 2),
        (["--split", "holdout"], 3),
        (["--ai-status", "ai_edited"], 3),
        (["--persona", "alpha"], 2),
        (["--use", "idiolect", "--split", "holdout", "--ai-status", "ai_edited"], 2),
        (["--persona", "gamma"], 0),
    ]
    for index, (extra, expected) in enumerate(cases):
        out_dir = tmp_path / f"run{index}"
        code, _out, _err = _run(_args(manifest, out_dir, *extra))
        assert code == 0, extra
        report = _report(out_dir)
        assert report["counts"]["input_rows"] == 4
        assert report["counts"]["scoped_documents"] == expected, extra


def test_omitted_filters_include_every_admissible_row(tmp_path: Path) -> None:
    """Omitted filters include every H2-admissible row; there are no implicit
    corpus-role defaults."""
    manifest = _corpus(
        tmp_path, 3, use=["baseline", "idiolect"], split="holdout",
        register="personal", persona="alias-a", ai_status="ai_edited",
    )
    out_dir = tmp_path / "out"
    code, _out, _err = _run(_args(manifest, out_dir))
    assert code == 0
    report = _report(out_dir)
    assert report["counts"]["input_rows"] == 3
    assert report["counts"]["scoped_documents"] == 3
    assert report["scope"] == {
        "ai_status": None,
        "min_words": 100,
        "persona_selected": False,
        "scope_sha256": report["scope"]["scope_sha256"],
        "split": None,
        "use": None,
    }


def test_crlf_manifest_reports_identically_to_lf(tmp_path: Path) -> None:
    """Native-Windows text-mode writers terminate every manifest line with
    CRLF. One trailing CR per line is a terminator, so the CRLF spelling
    commits the same report as LF, and the legacy validator reads it too (the
    row-boundary hardening once broke it on Windows CI)."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "doc00000.txt").write_text("word " * 150, encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    row = json.dumps({**GOOD_ROW, "register": "personal"}).encode("utf-8")

    results = []
    for index, terminator in enumerate((b"\n", b"\r\n")):
        manifest.write_bytes(row + terminator)
        out_dir = tmp_path / f"run{index}"
        code, out, _err = _run(_args(manifest, out_dir))
        assert code == 0
        assert _report(out_dir)["counts"]["input_rows"] == 1
        results.append(((out_dir / "report.json").read_bytes(), out))
    assert results[0] == results[1]

    legacy = mv.validate_manifest(str(manifest))
    assert legacy["n_entries"] == 1
    assert not any(
        "forbidden line-break character" in issue["message"]
        for issue in legacy["issues"]
    )


_ROW = json.dumps(GOOD_ROW).encode("utf-8")


@pytest.mark.parametrize(
    "data",
    [
        # Two objects on ONE physical LF-delimited line. str.splitlines() breaks
        # on each of these, so the row would have been counted as two documents.
        *[
            pytest.param(_ROW + breaker.encode("utf-8") + _ROW + b"\n", id=repr(breaker))
            for breaker in (
                "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e",
                "\x85", " ", " ",
            )
        ],
        # Carriage returns that are not the one trailing line terminator.
        pytest.param(_ROW[:-1] + b"\r}\n", id="interior-cr-whitespace"),
        pytest.param(_ROW + b"\r\r\n", id="double-cr"),
        # An unknown owned enum value must not silently become null or drop out,
        # which would move the row between declared families or filter sets.
        *[
            pytest.param(json.dumps({**GOOD_ROW, **extra}).encode("utf-8") + b"\n", id=name)
            for name, extra in (
                ("retired-register", {"register": "social_media_facebook"}),
                ("unknown-register", {"register": "not_a_register"}),
                ("unknown-split", {"split": "not_a_split"}),
                ("unknown-use-member", {"use": ["baseline", "not_a_use"]}),
            )
        ],
        # json.loads keeps the last duplicate silently; the parser must not.
        pytest.param(
            b'{"path":"corpus/other.txt","path":"corpus/doc00000.txt",'
            b'"use":["baseline"],"ai_status":"pre_ai_human"}\n',
            id="duplicate-key",
        ),
    ],
)
def test_a_row_that_would_silently_misproject_refuses(
    tmp_path: Path, data: bytes
) -> None:
    """Each manifest here parses under a lenient reader, but as the wrong rows,
    the wrong document, or the wrong family or filter value. The strict parser refuses them as ``bad_input``
    before any output exists. Both named documents exist, so the refusal is the
    parser's and not a missing file's."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("doc00000.txt", "other.txt"):
        (corpus / name).write_text("tiny synthetic document\n", encoding="utf-8")
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_bytes(data)
    out_dir = tmp_path / "out"
    code, out, err = _run(_args(manifest, out_dir))
    assert (code, out, err) == (2, GOLDEN_BAD_INPUT, b"")
    assert not (out_dir / "report.json").exists()
    assert not (out_dir / "state").exists()


def test_a_relative_path_resolves_to_the_manifest_parent_first(
    tmp_path: Path,
) -> None:
    """Candidate order is manifest parent, then manifest grandparent, then the
    working directory. A row must bind the nearest file, never a same-named
    file further out."""
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    manifest = inner / "m.jsonl"
    manifest.write_text(json.dumps({**GOOD_ROW, "path": "doc.txt"}) + "\n", encoding="utf-8")
    (outer / "doc.txt").write_text("grandparent", encoding="utf-8")

    plan = mv.project_register_sweep_manifest_bytes(
        manifest.read_bytes(), manifest_path=manifest,
    ).document_plan
    assert (plan[0].candidate_index, plan[0].absolute_path) == (1, outer / "doc.txt")

    (inner / "doc.txt").write_text("parent", encoding="utf-8")
    plan = mv.project_register_sweep_manifest_bytes(
        manifest.read_bytes(), manifest_path=manifest,
    ).document_plan
    assert (plan[0].candidate_index, plan[0].absolute_path) == (0, inner / "doc.txt")


@POSIX_ONLY
def test_an_unsafe_first_candidate_never_falls_through(tmp_path: Path) -> None:
    """A present but unsafe nearest candidate (a symlink) refuses; it does not
    silently fall through to a safe same-named file further out."""
    inner = tmp_path / "inner"
    inner.mkdir()
    manifest = inner / "m.jsonl"
    manifest.write_text(json.dumps({**GOOD_ROW, "path": "doc.txt"}) + "\n", encoding="utf-8")
    (tmp_path / "real_target.txt").write_text("real", encoding="utf-8")
    (inner / "doc.txt").symlink_to(tmp_path / "real_target.txt")
    (tmp_path / "doc.txt").write_text("fallback", encoding="utf-8")
    with pytest.raises(rs.BadInput):
        mv.project_register_sweep_manifest_bytes(
            manifest.read_bytes(), manifest_path=manifest,
        )


@POSIX_ONLY
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permissions",
)
def test_a_permission_blocked_candidate_ships_the_bad_input_golden(
    tmp_path: Path,
) -> None:
    """Bug regression: a nearest candidate under an unsearchable directory was
    treated as absent, so the run bound the next candidate out and exited 0. An
    unanswerable probe is an input failure, not an absence."""
    manifest = _corpus(tmp_path, 1)
    inner = tmp_path / "inner"
    shadow = inner / "corpus"
    shadow.mkdir(parents=True)
    (shadow / "doc00000.txt").write_text("shadowed document 0\n", encoding="utf-8")
    inner_manifest = inner / "manifest.jsonl"
    inner_manifest.write_bytes(manifest.read_bytes())
    out_dir = tmp_path / "out"
    os.chmod(shadow, 0o000)
    try:
        code, out, err = _run(_args(inner_manifest, out_dir))
    finally:
        os.chmod(shadow, 0o700)
    assert (code, out, err) == (2, GOLDEN_BAD_INPUT, b"")
    assert not (out_dir / "report.json").exists()
    assert not (out_dir / "state").exists()


@pytest.mark.parametrize("change", ["non_utf8", "edited_after_planning"])
def test_a_document_that_does_not_match_its_plan_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """A document must decode as UTF-8 and still be the file the plan bound.
    Otherwise mojibake, or contents written after planning (a sync client
    mid-sweep), would be counted under the old ``document_plan_sha256``."""
    manifest = _corpus(tmp_path, 2)
    target = tmp_path / "corpus" / "doc00001.txt"
    if change == "non_utf8":
        target.write_bytes(b"caf\xe9 synthetic document 1\n")
    else:
        real_read = rs.read_planned_document

        def editing_read(path: Any, expected_fingerprint: Any) -> bytes:
            # Same size, new contents: only the fingerprint can tell.
            target.write_text("TINY synthetic document 1\n", encoding="utf-8")
            return real_read(path, expected_fingerprint)

        monkeypatch.setattr(rs, "read_planned_document", editing_read)
    out_dir = tmp_path / "out"
    code, out, err = _run(_args(manifest, out_dir))
    assert (code, out, err) == (2, GOLDEN_BAD_INPUT, b"")
    assert not (out_dir / "report.json").exists()


@pytest.mark.parametrize("alias", ["same_path", "hardlink"])
def test_a_document_listed_twice_refuses_before_any_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias: str
) -> None:
    """A repeated row, or two paths naming one inode, would count one document
    twice. The scoped plan refuses before the first body read."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.txt").write_text("only document\n", encoding="utf-8")
    second = "corpus/a.txt"
    if alias == "hardlink":
        os.link(corpus / "a.txt", corpus / "b.txt")
        second = "corpus/b.txt"
    rows = [{**GOOD_ROW, "path": "corpus/a.txt"}, {**GOOD_ROW, "path": second}]
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    reads = _counting_reads(monkeypatch)
    out_dir = tmp_path / "out"
    code, out, err = _run(_args(manifest, out_dir))
    assert (code, out, err) == (2, GOLDEN_BAD_INPUT, b"")
    assert reads == []
    assert not (out_dir / "state").exists()


# --------------------------------------------------------------------------
# Aggregation: conservation and marginal equations
# --------------------------------------------------------------------------


def _aggregate(domains: rs.RegisterDomains, documents: list) -> dict:
    """Accumulate ``(declared, primary, refusal_reason, words, bytes)`` rows."""
    accumulator = rs.RegisterAggregate(domains)
    for declared, primary, reason, words, size in documents:
        accumulator.add_document(
            declared_family=declared,
            primary=primary,
            refusal_reason=reason,
            n_words=words,
            document_bytes=size,
        )
    return accumulator.snapshot()


def _mixed(domains: rs.RegisterDomains) -> dict:
    """Every bucket populated: same, different, unresolved-declared, and two
    refusals."""
    first, second = domains.classified[0], domains.classified[1]
    return _aggregate(
        domains,
        [
            (first, first, None, 100, 600),
            (second, first, None, 200, 700),
            ("unknown", second, None, 300, 800),
            (first, "unknown", domains.refusals[0], 4, 20),
            ("unknown", "unknown", domains.refusals[1], 7, 30),
        ],
    )


def test_mixed_run_satisfies_every_marginal_and_conservation_equation(
    domains: rs.RegisterDomains,
) -> None:
    aggregate = _mixed(domains)
    rs.validate_aggregate(aggregate, domains=domains)
    counts = aggregate["counts"]
    declared = aggregate["declared_family_inventory"]
    classified = aggregate["classified_family_inventory"]
    crosstab = aggregate["declared_by_classified_family"]
    refusals = aggregate["refusal_inventory"]
    matches = aggregate["match_inventory"]

    for measure in ("documents", "words"):
        total = counts[f"scoped_{measure}"]
        classified_total = counts[f"classified_{measure}"]
        refused_total = counts[f"refused_{measure}"]
        assert sum(cell[measure] for cell in declared.values()) == total
        assert sum(cell[measure] for cell in matches.values()) == total
        assert sum(cell[measure] for cell in classified.values()) == classified_total
        assert (
            sum(cell[measure] for row in crosstab.values() for cell in row.values())
            == classified_total
        )
        assert sum(cell[measure] for cell in refusals.values()) == refused_total
        assert classified_total + refused_total == total
        # Every column marginal.
        for family in domains.classified:
            assert (
                sum(crosstab[d][family][measure] for d in domains.declared)
                == classified[family][measure]
            )
        # Every row marginal: the crosstab row is at most the declared cell; the
        # rest of the declared cell is that family's refused share.
        for family in domains.declared:
            assert (
                sum(crosstab[family][p][measure] for p in domains.classified)
                <= declared[family][measure]
            )
        assert matches["same"][measure] == sum(
            crosstab[f][f][measure] for f in domains.classified
        )
        assert matches["different"][measure] == sum(
            crosstab[d][p][measure]
            for d in domains.classified
            for p in domains.classified
            if d != p
        )
        assert matches["unresolved"][measure] == (
            sum(crosstab["unknown"][p][measure] for p in domains.classified)
            + sum(cell[measure] for cell in refusals.values())
        )
        assert counts[f"unresolved_declared_{measure}"] == declared["unknown"][measure]
        assert (
            counts[f"resolved_declared_{measure}"]
            == total - declared["unknown"][measure]
        )


def test_refusal_never_in_a_family_cell(domains: rs.RegisterDomains) -> None:
    """Each refusal reason is recorded once, in the refusal inventory and the
    unresolved match bucket, and never in a classified-family cell or the
    crosstab."""
    declared = domains.classified[0]
    cell = {"documents": 1, "words": 3}
    for reason in domains.refusals:
        aggregate = _aggregate(domains, [(declared, "unknown", reason, 3, 15)])
        rs.validate_aggregate(aggregate, domains=domains)
        assert aggregate["refusal_inventory"][reason] == cell
        assert sum(
            c["documents"] for c in aggregate["classified_family_inventory"].values()
        ) == 0
        assert sum(
            c["documents"]
            for row in aggregate["declared_by_classified_family"].values()
            for c in row.values()
        ) == 0
        assert aggregate["declared_family_inventory"][declared] == cell
        assert aggregate["match_inventory"]["unresolved"] == cell
        assert aggregate["counts"]["refused_documents"] == 1
        assert aggregate["counts"]["resolved_declared_documents"] == 1


def test_word_counts_come_only_from_the_validated_h1_evidence(
    binding: rs.H1Binding, domains: rs.RegisterDomains
) -> None:
    """H2 has no competing tokenizer: the word count is read from the validated
    H1 evidence, even when it disagrees with every text-derived count."""
    text = "\n\n".join(
        "This is a synthetic paragraph written to exercise the classifier "
        "seam with ordinary sentences. It carries no private prose."
        for _ in range(4)
    )
    result = binding.classify(text, min_words=10)
    accumulator = rs.RegisterAggregate(domains)
    accumulator.add_h1_result(
        declared_family="unknown",
        result=result,
        document_bytes=len(text.encode("utf-8")),
    )
    aggregate = accumulator.snapshot()
    assert aggregate["counts"]["scoped_words"] == result["evidence"]["n_words"]
    rs.validate_aggregate(aggregate, domains=domains)

    hostile = copy.deepcopy(result)
    hostile["evidence"]["n_words"] = 4242
    second = rs.RegisterAggregate(domains)
    second.add_h1_result(declared_family="unknown", result=hostile, document_bytes=1)
    assert second.snapshot()["counts"]["scoped_words"] == 4242


def test_every_inventory_cell_mutation_breaks_a_marginal(
    domains: rs.RegisterDomains,
) -> None:
    """The pre-publication check catches any single-cell drift, a refusal
    recorded in a family cell, and swapped same/different buckets."""
    first = domains.classified[0]
    mutations = (
        ("counts", ("scoped_documents",)),
        ("counts", ("classified_words",)),
        ("counts", ("refused_documents",)),
        ("declared_family_inventory", (first,)),
        ("declared_family_inventory", ("unknown",)),
        ("classified_family_inventory", (first,)),
        ("declared_by_classified_family", (first, first)),
        ("declared_by_classified_family", ("unknown", first)),
        ("refusal_inventory", (domains.refusals[0],)),
        ("match_inventory", ("same",)),
        ("match_inventory", ("different",)),
        ("match_inventory", ("unresolved",)),
    )
    for name, path in mutations:
        for measure in ("documents", "words"):
            aggregate = _mixed(domains)
            node = aggregate[name]
            if name == "counts":
                node[path[0]] += 1
            else:
                for step in path:
                    node = node[step]
                node[measure] += 1
            with pytest.raises(rs.InternalError):
                rs.validate_aggregate(aggregate, domains=domains)

    refused = _aggregate(domains, [(first, "unknown", domains.refusals[0], 9, 40)])
    refused["classified_family_inventory"][first] = {"documents": 1, "words": 9}
    refused["declared_by_classified_family"][first][first] = {"documents": 1, "words": 9}
    with pytest.raises(rs.InternalError):
        rs.validate_aggregate(refused, domains=domains)

    swapped = _mixed(domains)
    matches = swapped["match_inventory"]
    matches["same"], matches["different"] = matches["different"], matches["same"]
    with pytest.raises(rs.InternalError):
        rs.validate_aggregate(swapped, domains=domains)


def test_report_counts_conserve_the_scoped_totals(tmp_path: Path) -> None:
    manifest = _corpus(tmp_path, 6, register="personal")
    out_dir = tmp_path / "out"
    assert _run(_args(manifest, out_dir, "--min-words", "1"))[0] == 0
    report = _report(out_dir)
    counts = report["counts"]
    # ``register: personal`` resolves to a declared family for every row.
    assert counts["resolved_declared_documents"] == 6
    for measure in ("documents", "words"):
        scoped = counts[f"scoped_{measure}"]
        assert (
            counts[f"resolved_declared_{measure}"]
            + counts[f"unresolved_declared_{measure}"]
        ) == scoped
        assert counts[f"classified_{measure}"] + counts[f"refused_{measure}"] == scoped
        assert sum(
            cell[measure] for cell in report["declared_family_inventory"].values()
        ) == scoped
        assert sum(
            cell[measure] for cell in report["match_inventory"].values()
        ) == scoped


def test_min_words_reaches_the_classifier_and_is_not_a_row_filter(
    tmp_path: Path,
) -> None:
    """``--min-words`` is passed straight to the classifier: at the default
    every tiny document refuses ``short_text``, and at 1 every one classifies.
    The scoped row count is identical either way."""
    counts = []
    for index, extra in enumerate(([], ["--min-words", "1"])):
        root = tmp_path / f"m{index}"
        manifest = _corpus(root, 3, register="personal")
        assert _run(_args(manifest, root / "out", *extra))[0] == 0
        report = _report(root / "out")
        counts.append(report["counts"])
        assert report["counts"]["scoped_documents"] == 3
    assert counts[0]["refused_documents"] == 3
    assert counts[0]["classified_documents"] == 0
    assert counts[1]["refused_documents"] == 0
    assert counts[1]["classified_documents"] == 3


def test_empty_scope_commits_a_zero_inventory_and_creates_no_shard(
    tmp_path: Path,
) -> None:
    manifest = _corpus(tmp_path, 3)
    out_dir = tmp_path / "out"
    code, out, err = _run(_args(manifest, out_dir, "--use", "exclude"))
    assert code == 0
    assert err == _complete_line(0)
    report = _report(out_dir)
    assert report["counts"]["input_rows"] == 3
    assert report["counts"]["scoped_documents"] == 0
    assert report["counts"]["scoped_words"] == 0
    assert _shard_names(out_dir) == []
    for name in rs.INVENTORY_KEYS:
        for cell in _cells(report[name]):
            assert cell == {"documents": 0, "words": 0}
    assert json.loads(out.decode("utf-8"))["results"]["counts"] == report["counts"]


# --------------------------------------------------------------------------
# Output: goldens, determinism, privacy, and the commit point
# --------------------------------------------------------------------------


def test_success_envelope_is_the_canonical_golden(tmp_path: Path) -> None:
    """The full canonical success envelope, with explicit null and empty
    defaults and the complete frozen ClaimLicense rendering, plus the report's
    closed shape."""
    manifest = _corpus(tmp_path, 2, register="personal")
    out_dir = tmp_path / "out"
    code, out, err = _run(_args(manifest, out_dir, "--persona", "unused"))
    assert code == 0
    assert err == _complete_line(0)  # no row carries the persona
    report_bytes = (out_dir / "report.json").read_bytes()
    report = json.loads(report_bytes.decode("utf-8"))
    expected = {
        "ai_status": None,
        "available": True,
        "baseline": None,
        "claim_license": GOLDEN_CLAIM_LICENSE,
        "claim_license_rendered": (
            "## What this result licenses\n\n"
            "**Task surface:** validation / labeled-corpus harness\n\n"
            "**Reports:** " + GOLDEN_CLAIM_LICENSE["licenses"] + "\n\n"
            "**Does NOT report:** " + GOLDEN_CLAIM_LICENSE["does_not_license"]
            + "\n\n### Caveats\n\n- "
            + GOLDEN_CLAIM_LICENSE["additional_caveats"][0]
        ),
        "results": {
            "counts": report["counts"],
            "report_schema_version": "setec-register-sweep-report/2",
            "report_sha256": "sha256:" + hashlib.sha256(report_bytes).hexdigest(),
            "taxonomy": "register_families/v2",
        },
        "schema_version": "1.0",
        "target": {"path": None, "words": report["counts"]["scoped_words"]},
        "task_surface": "validation",
        "tool": "register_sweep",
        "version": "2.0.0",
        "warnings": [],
    }
    assert out == _canonical(expected) + b"\n"
    # The envelope carries no family cell, filter value, path, or identifier.
    assert b"persona" not in out
    assert b"unused" not in out
    assert b"declared_family_inventory" not in out

    # The report is canonical (sorted-key) bytes with one terminal newline and
    # the closed spec key set.
    assert report_bytes == _canonical(report) + b"\n"
    assert set(report) == set(SPEC_REPORT_KEYS)
    assert report["schema_version"] == "setec-register-sweep-report/2"
    assert report["claim_license"] == GOLDEN_CLAIM_LICENSE
    assert report["warnings"] == []


def test_controlled_error_envelopes_are_the_three_goldens() -> None:
    for failure, golden, exit_code in (
        (rs.BadInput, GOLDEN_BAD_INPUT, 2),
        (rs.PolicyRefused, GOLDEN_POLICY_REFUSED, 3),
        (rs.InternalError, GOLDEN_INTERNAL_ERROR, 4),
    ):
        assert rs.freeze_controlled_error(failure) == (golden, exit_code)
        # An instance carrying private text maps to the same golden: no caught
        # exception text reaches stdout.
        instance = failure("/private/path/secret.jsonl")
        assert rs.freeze_controlled_error(instance) == (golden, exit_code)


SENTINEL_PERSONA = "zqsentinelpersonazq"
SENTINEL_PATH = "zqsentineldirzq"
SENTINEL_PROSE = "zqsentinelprosezq"


def test_sentinel_values_never_reach_any_output_stream(tmp_path: Path) -> None:
    """Sentinel prose, path components, and the raw persona filter never reach
    stdout, stderr, the report, or the checkpoint. The validated categorical
    filters appear only in the report's closed ``scope`` object."""
    root = tmp_path / SENTINEL_PATH
    manifest = _corpus(
        root, 3,
        body=SENTINEL_PROSE,
        corpus_name=SENTINEL_PATH + "-corpus",
        manifest_name=SENTINEL_PATH + "-manifest.jsonl",
        use=["idiolect"], split="holdout", ai_status="ai_edited",
        persona=SENTINEL_PERSONA,
    )
    code, out, err = _run(
        _args(manifest, root,
              "--persona", SENTINEL_PERSONA,
              "--use", "idiolect", "--split", "holdout",
              "--ai-status", "ai_edited")
    )
    assert code == 0
    report_bytes = (root / "report.json").read_bytes()
    checkpoint_bytes = b"".join(
        p.read_bytes() for p in sorted((root / "state").iterdir())
    )
    for sentinel in (SENTINEL_PERSONA, SENTINEL_PATH, SENTINEL_PROSE):
        encoded = sentinel.encode("utf-8")
        for stream in (out, err, report_bytes, checkpoint_bytes):
            assert encoded not in stream
    report = json.loads(report_bytes.decode("utf-8"))
    assert report["scope"]["use"] == "idiolect"
    assert report["scope"]["split"] == "holdout"
    assert report["scope"]["ai_status"] == "ai_edited"
    assert report["scope"]["persona_selected"] is True
    for selector in (b"idiolect", b"holdout", b"ai_edited"):
        for stream in (out, err, checkpoint_bytes):
            assert selector not in stream
        assert report_bytes.count(selector) == 1


def test_scope_digest_commits_the_raw_persona(tmp_path: Path) -> None:
    """The report never shows the persona, but its ``scope_sha256`` commits to
    it: two runs differing only in persona report different scope digests."""
    digests = []
    for index, persona in enumerate(("alpha", "beta")):
        root = tmp_path / f"s{index}"
        manifest = _corpus(root, 1, persona="alpha")
        assert _run(_args(manifest, root / "out", "--persona", persona))[0] == 0
        report = _report(root / "out")
        assert report["scope"]["persona_selected"] is True
        digests.append(report["scope"]["scope_sha256"])
    assert digests[0] != digests[1]


@pytest.mark.parametrize(
    "extra",
    [
        # A repeated option must not silently resolve to the last value.
        ["--use", "baseline", "--use", "train"],
        ["--min-words", "100", "--min-words", "200"],
        # No source-grouping option exists; it must not be silently ignored.
        ["--group-by-source"],
        ["--source", "x"],
        ["extra-positional"],
        ["--use", "Baseline"],
    ],
)
def test_bad_options_refuse_before_any_output_creation(
    tmp_path: Path, extra: list[str]
) -> None:
    manifest = _corpus(tmp_path, 1)
    out_dir = tmp_path / "out"
    code, out, err = _run(_args(manifest, out_dir, *extra))
    assert (code, out, err) == (2, GOLDEN_BAD_INPUT, b"")
    assert not (out_dir / "report.json").exists()
    assert not (out_dir / "state").exists()


def test_an_existing_report_is_never_overwritten(tmp_path: Path) -> None:
    manifest = _corpus(tmp_path, 1)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "report.json").write_text("{}", encoding="utf-8")
    code, out, _err = _run(_args(manifest, out_dir))
    assert (code, out) == (3, GOLDEN_POLICY_REFUSED)
    assert (out_dir / "report.json").read_text(encoding="utf-8") == "{}"
    assert not (out_dir / "state").exists()


def test_a_failure_after_processing_complete_publishes_no_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publication is the terminal commit point: a failure after
    ``processing-complete`` emits the one controlled envelope and leaves no
    report, while the sealed shard stays for a resume."""
    manifest = _corpus(tmp_path, 2)

    def refusing_freeze(**_kwargs: Any) -> tuple[bytes, str, dict, bytes]:
        raise rs.InternalError()

    monkeypatch.setattr(rs, "freeze_publication", refusing_freeze)
    out_dir = tmp_path / "out"
    code, out, err = _run(_args(manifest, out_dir))
    assert (code, out) == (4, GOLDEN_INTERNAL_ERROR)
    assert err == _complete_line(2)
    assert not (out_dir / "report.json").exists()
    assert _shard_names(out_dir) == ["register-00000000.sqlite"]


@POSIX_ONLY
def test_the_checkpoint_is_owner_private(tmp_path: Path) -> None:
    manifest = _corpus(tmp_path, 2)
    out_dir = tmp_path / "out"
    assert _run(_args(manifest, out_dir))[0] == 0
    state = out_dir / "state"
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
    for shard in state.iterdir():
        assert stat.S_IMODE(os.stat(shard).st_mode) == 0o600


def test_runtime_import_graph_reaches_no_network_module(tmp_path: Path) -> None:
    """A full run, including the exec'd receipt-bound classifier, imports no
    network module. It runs in a child interpreter so this session's own
    imports cannot mask the answer."""
    root = tmp_path / "probe"
    manifest = _corpus(root, 2)
    program = (
        "import sys\n"
        f"sys.path.insert(0, {str(SCRIPTS)!r})\n"
        "import io\n"
        "import register_sweep as rs\n"
        "out = io.BytesIO(); err = io.BytesIO()\n"
        "code = rs.main(["
        f"'--manifest', {str(manifest)!r},"
        f"'--report-out', {str(root / 'report.json')!r},"
        f"'--checkpoint-dir', {str(root / 'state')!r}"
        "], stdout=out, stderr=err)\n"
        # Transport-capable modules by top-level name, plus the transport
        # submodules of urllib/http by exact name. Not the bare urllib/http
        # top-levels: urllib.parse is pure string parsing, and Python 3.12's
        # pathlib imports it at module scope.
        "network = sorted(\n"
        "    name for name in sys.modules\n"
        "    if name.split('.')[0] in {\n"
        "        'socket', '_socket', 'ssl', '_ssl',\n"
        "        'ftplib', 'smtplib', 'telnetlib', 'asyncio', 'selectors',\n"
        "        'requests', 'httpx', 'subprocess',\n"
        "    }\n"
        "    or name in {'urllib.request', 'urllib.error', 'http.client',\n"
        "                'http.server', 'xmlrpc'}\n"
        ")\n"
        "print(code, network)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, check=True,
    )
    assert completed.stdout.strip() == "0 []", completed.stdout + completed.stderr


# --------------------------------------------------------------------------
# The real entry point: ``python3 register_sweep.py ...``
#
# Run as a script, the module loads as ``__main__``; manifest_validator then
# imports ``register_sweep`` at import time. Without the launcher's alias that
# loads a SECOND copy with its own BadInput/PolicyRefused/InternalError
# classes, so a refusal raised from the manifest_validator seam misses every
# isinstance check and ships as exit 4 / internal_error instead of exit 2 /
# bad_input. These cases therefore run through ``subprocess``.
# --------------------------------------------------------------------------


def _cli_subprocess(argv: list[str]) -> tuple[int, bytes, bytes]:
    completed = subprocess.run(
        [sys.executable, str(REGISTER_SWEEP_SCRIPT), *argv],
        capture_output=True, cwd=str(REPO_ROOT),
    )
    return completed.returncode, completed.stdout, completed.stderr


def test_real_entry_point_commits_the_success_envelope(tmp_path: Path) -> None:
    manifest = _corpus(tmp_path, 3)
    out_dir = tmp_path / "out"
    code, out, err = _cli_subprocess(_args(manifest, out_dir))
    assert code == 0, err
    assert err == _complete_line(3)
    report_bytes = (out_dir / "report.json").read_bytes()
    envelope = json.loads(out.decode("utf-8"))
    assert envelope["available"] is True
    assert envelope["results"]["report_sha256"] == (
        "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    )
    assert json.loads(report_bytes)["counts"]["scoped_documents"] == 3


def test_real_entry_point_ships_seam_refusals_as_bad_input(tmp_path: Path) -> None:
    """Seam-raised failure-map rows are exit 2 / ``bad_input`` at the real
    entry point, as is an argument-less invocation."""
    assert _cli_subprocess([]) == (2, GOLDEN_BAD_INPUT, b"")
    good = json.dumps(GOOD_ROW).encode("utf-8")
    cases = {
        "bad_enum_value": json.dumps(
            {**GOOD_ROW, "ai_status": "not_a_real_ai_status"}
        ).encode("utf-8") + b"\n",
        "duplicate_row": good + b"\n" + good + b"\n",
        "missing_document": good + b"\n",
        "malformed_json": b"{bad-json\n",
    }
    for case, data in cases.items():
        root = tmp_path / case
        corpus = root / "corpus"
        corpus.mkdir(parents=True)
        if case != "missing_document":
            (corpus / "doc00000.txt").write_text("tiny synthetic document 0\n", encoding="utf-8")
        manifest = root / "manifest.jsonl"
        manifest.write_bytes(data)
        out_dir = root / "out"
        assert _cli_subprocess(_args(manifest, out_dir)) == (
            2, GOLDEN_BAD_INPUT, b"",
        ), case
        assert not (out_dir / "report.json").exists()
        assert not (out_dir / "state").exists()


# --------------------------------------------------------------------------
# Interrupt and resume
# --------------------------------------------------------------------------


def test_fresh_and_resumed_reports_are_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_shard_run: dict[str, Any]
) -> None:
    """Interruption after a sealed shard loses only the unpublished rows. The
    resumed report and stdout envelope are byte-identical to a fresh run's, and
    no earlier cadence line is replayed."""
    manifest = two_shard_run["manifest"]
    out_dir = tmp_path / "run"
    _interrupt_after_reads(monkeypatch, _args(manifest, out_dir), rs.SHARD_ROWS)
    assert not (out_dir / "report.json").exists()
    assert _shard_names(out_dir) == ["register-00000000.sqlite"]

    code, out, err = _run(_args(manifest, out_dir, "--resume"))
    assert code == 0
    assert (out_dir / "report.json").read_bytes() == two_shard_run["report"]
    assert out == two_shard_run["out"]
    # The fresh run emitted cadence lines at 100 and 200; the resumed run
    # starts at 250 and emits none.
    assert two_shard_run["err"].count(b"register sweep progress:") == 2
    assert err == _complete_line(rs.SHARD_ROWS + 1)


def test_resume_of_a_complete_sealed_chain_reprocesses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_shard_run: dict[str, Any]
) -> None:
    """A sealed chain that already covers the whole plan resumes with zero
    document reads and commits the identical report."""
    manifest = two_shard_run["manifest"]
    out_dir = tmp_path / "run"
    real_publish = rs.TopologyPreflight.publish_report

    def refusing_publish(self: Any, payload: bytes) -> None:
        raise KeyboardInterrupt("synthetic interruption at the commit point")

    monkeypatch.setattr(rs.TopologyPreflight, "publish_report", refusing_publish)
    with pytest.raises(KeyboardInterrupt):
        _run(_args(manifest, out_dir))
    monkeypatch.setattr(rs.TopologyPreflight, "publish_report", real_publish)
    assert not (out_dir / "report.json").exists()

    reads = _counting_reads(monkeypatch)
    code, _out, err = _run(_args(manifest, out_dir, "--resume"))
    assert code == 0
    assert reads == []
    assert (out_dir / "report.json").read_bytes() == two_shard_run["report"]
    assert err == _complete_line(rs.SHARD_ROWS + 1)


@POSIX_ONLY
def test_crash_debris_in_a_reserved_temp_is_not_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_shard_run: dict[str, Any]
) -> None:
    """A crash can leave a fully written shard under a reserved temporary name.
    Resume must not count it as sealed progress, and the resumed report still
    matches the fresh run."""
    manifest = two_shard_run["manifest"]
    out_dir = tmp_path / "run"
    _interrupt_after_reads(monkeypatch, _args(manifest, out_dir), rs.SHARD_ROWS)
    state = out_dir / "state"
    debris = state / (".tmp-" + "0" * 32)
    debris.write_bytes((state / "register-00000000.sqlite").read_bytes())
    os.chmod(debris, 0o600)

    code, _out, _err = _run(_args(manifest, out_dir, "--resume"))
    assert code == 0
    assert (out_dir / "report.json").read_bytes() == two_shard_run["report"]


@POSIX_ONLY
def test_a_hole_in_the_shard_chain_refuses_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With shards 0 and 1 sealed, losing shard 0 must not resume from shard 1
    and silently drop its 250 rows."""
    manifest = _corpus(tmp_path, 2 * rs.SHARD_ROWS + 1)
    out_dir = tmp_path / "run"
    _interrupt_after_reads(monkeypatch, _args(manifest, out_dir), 2 * rs.SHARD_ROWS)
    assert _shard_names(out_dir) == [
        "register-00000000.sqlite", "register-00000001.sqlite",
    ]
    (out_dir / "state" / "register-00000000.sqlite").unlink()
    code, out, err = _run(_args(manifest, out_dir, "--resume"))
    assert (code, out, err) == (3, GOLDEN_POLICY_REFUSED, b"")
    assert not (out_dir / "report.json").exists()


@POSIX_ONLY
def test_a_corrupted_shard_refuses_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_shard_run: dict[str, Any]
) -> None:
    """A sealed row whose stored digest no longer matches its bytes refuses
    rather than feeding the resumed counts."""
    manifest = two_shard_run["manifest"]
    out_dir = tmp_path / "run"
    _interrupt_after_reads(monkeypatch, _args(manifest, out_dir), rs.SHARD_ROWS)
    shard = out_dir / "state" / "register-00000000.sqlite"
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(shard.read_bytes())
        connection.execute(
            "UPDATE rows SET row_sha256=zeroblob(32) WHERE scoped_ordinal=0"
        )
        connection.commit()
        corrupt = connection.serialize()
    finally:
        connection.close()
    shard.unlink()
    shard.write_bytes(corrupt)
    os.chmod(shard, 0o600)

    code, out, err = _run(_args(manifest, out_dir, "--resume"))
    assert (code, out, err) == (3, GOLDEN_POLICY_REFUSED, b"")
    assert not (out_dir / "report.json").exists()


@pytest.mark.parametrize("change", ["min_words", "manifest_row", "document_edit"])
def test_resume_under_a_changed_run_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """A checkpoint is bound to its scope, manifest, and document plan.
    Resuming after any of them changed must refuse, not mix sealed rows from
    one run into another's report."""
    manifest = _corpus(tmp_path, rs.SHARD_ROWS + 1)
    out_dir = tmp_path / "run"
    _interrupt_after_reads(monkeypatch, _args(manifest, out_dir), rs.SHARD_ROWS)
    extra: list[str] = []
    if change == "min_words":
        extra = ["--min-words", "1"]
    elif change == "manifest_row":
        (tmp_path / "corpus" / "extra.txt").write_text("extra\n", encoding="utf-8")
        with manifest.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**GOOD_ROW, "path": "corpus/extra.txt"}) + "\n")
    else:
        # Same size, so only the plan's fingerprint binding can refuse it.
        (tmp_path / "corpus" / "doc00000.txt").write_text(
            "TINY synthetic document 0\n", encoding="utf-8"
        )
    code, out, err = _run(_args(manifest, out_dir, "--resume", *extra))
    assert (code, out, err) == (3, GOLDEN_POLICY_REFUSED, b"")
    assert not (out_dir / "report.json").exists()
