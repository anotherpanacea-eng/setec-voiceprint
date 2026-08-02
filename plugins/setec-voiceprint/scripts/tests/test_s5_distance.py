from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import s5_distance as s5  # type: ignore  # noqa: E402
import stylometry_core as sc  # type: ignore  # noqa: E402


class _Token:
    def __init__(self, text: str, index: int):
        self.is_space = False
        self.pos_ = (
            "PUNCT" if re.fullmatch(r"[^A-Za-z']+", text)
            else ("PROPN" if text[:1].isupper() else ("NOUN" if len(text) > 5 else "VERB"))
        )
        self.dep_ = ("ROOT", "nsubj", "dobj", "advmod")[index % 4]


class _Doc:
    def __init__(self, text: str):
        self.sents = []
        for sentence in s5.regex_split_sentences(text):
            pieces = re.findall(r"[A-Za-z']+|[^\w\s]", sentence)
            self.sents.append([_Token(piece, i) for i, piece in enumerate(pieces)])


class FakeNLP:
    pipe_names = ["tagger", "parser"]

    def __call__(self, text: str) -> _Doc:
        return _Doc(text)


BASELINE_TEXTS = (
    (
        "First baseline paragraph opens plainly, then turns toward evidence. "
        "Readers consider the ordinary claim; they compare it with another example!\n"
    ),
    (
        "A second reference asks a sharper question? Its syntax circles slowly around "
        "institutional choices, public reasons, and practical consequences.\n"
    ),
    (
        "Finally, the third sample uses clipped clauses: one fact; another fact; a "
        "qualified conclusion. Nevertheless the argument remains deliberately clear.\n"
    ),
)
TARGET_TEXT = (
    "This target begins with a concrete observation. It then qualifies the public "
    "claim, connects evidence to consequences, and closes with one difficult question?\n"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_request(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path.resolve()
    target = root / "target.txt"
    target.write_text(TARGET_TEXT, encoding="utf-8")
    baseline_dir = root / "baseline"
    entries_dir = baseline_dir / "entries"
    entries_dir.mkdir(parents=True)
    rows = []
    for index, text in enumerate(BASELINE_TEXTS):
        data = text.encode("utf-8")
        digest = _sha(data)
        (entries_dir / f"{digest}.txt").write_bytes(data)
        rows.append({
            "id": digest,
            "path": f"entries/{digest}.txt",
            "content_sha256": digest,
            "source_seed_sha256": _sha(f"seed-{index}".encode()),
            "source_paragraph_index": index,
            "seed_family": f"seed_{index}",
            "use": "baseline",
            "split": "train",
            "register": None,
            "persona": "fixture-persona",
            "ai_status": "pre_ai_human",
        })
    manifest = baseline_dir / "manifest.jsonl"
    manifest_bytes = b"".join(s5.canonical_json(row) for row in rows)
    manifest.write_bytes(manifest_bytes)
    inventory = _sha("\n".join(sorted(row["content_sha256"] for row in rows)).encode())
    request = {
        "schema": s5.REQUEST_SCHEMA,
        "private_root": str(root),
        "target_relpath": "target.txt",
        "target_sha256": _sha(target.read_bytes()),
        "baseline_manifest_relpath": "baseline/manifest.jsonl",
        "baseline_manifest_sha256": _sha(manifest_bytes),
        "baseline_content_inventory_sha256": inventory,
        "use": "baseline",
        "split": "train",
        "register": None,
        "persona": "fixture-persona",
        "ai_status": "pre_ai_human",
        "sentence_splitter_id": s5.SENTENCE_SPLITTER_ID,
        "parser_inventory_sha256": "a" * 64,
    }
    request_path = root / "request.json"
    request_path.write_bytes(s5.canonical_json(request))
    return request_path, request


def test_exact_six_family_fixture_and_hash_echoes(tmp_path: Path, monkeypatch):
    request_path, request = _write_request(tmp_path)
    observed_names = {}
    real_family_distance = s5.family_distance

    def recording_family_distance(target, baseline, family, names, **kwargs):
        observed_names[family] = list(names)
        return real_family_distance(target, baseline, family, names, **kwargs)

    monkeypatch.setattr(s5, "family_distance", recording_family_distance)
    results, target_words, baseline_files, baseline_words = s5.score_request(
        request_path,
        nlp=FakeNLP(),
        parser_version="fixture-parser-1",
    )

    assert list(results["family_scores"]) == list(s5.S5_FAMILIES)
    assert results["target_sha256"] == request["target_sha256"]
    assert results["baseline_manifest_sha256"] == request["baseline_manifest_sha256"]
    assert (
        results["baseline_content_inventory_sha256"]
        == request["baseline_content_inventory_sha256"]
    )
    assert results["parser_inventory_sha256"] == "a" * 64
    assert results["sentence_splitter_id"] == s5.SENTENCE_SPLITTER_ID
    assert results["s5_score"] == pytest.approx(
        sum(results["family_scores"].values()) / 6,
        rel=0,
        abs=1e-15,
    )
    assert results["family_scores"] == pytest.approx({
        "char_ngrams_3": 0.7685640174233851,
        "char_ngrams_4": 1.0044851936786767,
        "char_ngrams_5": 0.6535687882965725,
        "pos_trigrams": 1.3292546070740199,
        "dependency_ngrams": 0.6034521845761506,
        "punctuation": 0.8392607421034265,
    }, rel=0, abs=1e-15)
    assert set(observed_names) == set(s5.S5_FAMILIES)
    for family, limit in s5.S5_LIMITS.items():
        assert len(observed_names[family]) <= limit
    assert observed_names["punctuation"] == sorted(observed_names["punctuation"])
    assert target_words > 0 and baseline_files == 3 and baseline_words > target_words

    envelope = s5.build_envelope(
        results,
        target_words=target_words,
        baseline_files=baseline_files,
        baseline_words=baseline_words,
    )
    assert envelope["schema_version"] == "1.0"
    assert envelope["tool"] == "s5_distance"
    assert envelope["warnings"] == []
    forbidden = {"verdict", "label", "is_ai", "is_human", "probability"}

    def walk(value):
        if isinstance(value, dict):
            assert not (set(value) & forbidden)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(envelope["results"])

    extra = dict(results)
    extra["unexpected"] = True
    with pytest.raises(s5.S5DistanceError, match="wrong keys"):
        s5.build_envelope(
            extra,
            target_words=target_words,
            baseline_files=baseline_files,
            baseline_words=baseline_words,
        )


def test_manifest_extra_key_refuses(tmp_path: Path):
    request_path, request = _write_request(tmp_path)
    manifest = tmp_path / request["baseline_manifest_relpath"]
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    rows[0]["extra"] = True
    raw = b"".join(s5.canonical_json(row) for row in rows)
    manifest.write_bytes(raw)
    request["baseline_manifest_sha256"] = _sha(raw)
    request_path.write_bytes(s5.canonical_json(request))
    with pytest.raises(s5.S5DistanceError, match="wrong keys"):
        s5.score_request(request_path, nlp=FakeNLP(), parser_version="fixture")


def test_request_path_traversal_refuses(tmp_path: Path):
    request_path, request = _write_request(tmp_path)
    request["target_relpath"] = "../target.txt"
    request_path.write_bytes(s5.canonical_json(request))
    with pytest.raises(s5.S5DistanceError, match="contained POSIX relpath"):
        s5.score_request(request_path, nlp=FakeNLP(), parser_version="fixture")


def test_manifest_is_not_reopened_after_validated_snapshot(tmp_path: Path, monkeypatch):
    request_path, request = _write_request(tmp_path)
    real_validate = s5._validate_manifest
    observed = {}

    def validate_then_swap(*args, **kwargs):
        rows, entries = real_validate(*args, **kwargs)
        manifest = tmp_path / request["baseline_manifest_relpath"]
        swapped = [dict(row) for row in rows]
        for row in swapped:
            row["path"] = str(tmp_path.parent / "outside.txt")
        manifest.write_bytes(b"".join(s5.canonical_json(row) for row in swapped))
        return rows, entries

    real_extract = s5.extract_entry_features

    def record_entries(entries, **kwargs):
        observed["paths"] = [entry["path"] for entry in entries]
        observed["texts"] = [entry["text"] for entry in entries]
        return real_extract(entries, **kwargs)

    monkeypatch.setattr(s5, "_validate_manifest", validate_then_swap)
    monkeypatch.setattr(s5, "extract_entry_features", record_entries)
    results, *_ = s5.score_request(
        request_path, nlp=FakeNLP(), parser_version="fixture")
    assert results["baseline_manifest_sha256"] == request["baseline_manifest_sha256"]
    assert observed["texts"] == list(BASELINE_TEXTS)
    assert all(Path(path).is_relative_to(tmp_path) for path in observed["paths"])


def test_nonfinite_request_is_a_closed_error_envelope(tmp_path: Path, capsys):
    request_path, request = _write_request(tmp_path)
    request["use"] = math.nan
    request_path.write_text(
        json.dumps(
            request,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert s5.main(["--request", str(request_path), "--json"]) == 0
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["available"] is False
    assert envelope["reason_category"] == "bad_input"
    assert "non-finite" in envelope["reason"]
    assert "Traceback" not in captured.out + captured.err


def test_nonfinite_manifest_is_a_closed_error_envelope(tmp_path: Path, capsys):
    request_path, request = _write_request(tmp_path)
    manifest = tmp_path / request["baseline_manifest_relpath"]
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    rows[0]["source_paragraph_index"] = math.nan
    raw = b"".join(
        (
            json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=True,
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )
    manifest.write_bytes(raw)
    request["baseline_manifest_sha256"] = _sha(raw)
    request_path.write_bytes(s5.canonical_json(request))

    assert s5.main(["--request", str(request_path), "--json"]) == 0
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["available"] is False
    assert envelope["reason_category"] == "bad_input"
    assert "non-finite" in envelope["reason"]
    assert "Traceback" not in captured.out + captured.err


def test_overflowing_family_mean_is_a_closed_error_envelope(
    tmp_path: Path, capsys, monkeypatch,
):
    request_path, _request = _write_request(tmp_path)
    monkeypatch.setattr(
        s5,
        "_score",
        lambda *_args, **_kwargs: {family: 1e308 for family in s5.S5_FAMILIES},
    )
    assert s5.main(["--request", str(request_path), "--json"]) == 0
    captured = capsys.readouterr()
    envelope = json.loads(captured.out)
    assert envelope["available"] is False
    assert envelope["reason_category"] == "bad_input"
    assert "non-finite" in envelope["reason"]
    assert "Traceback" not in captured.out + captured.err


def test_injected_splitter_is_independent_of_ambient_nltk(monkeypatch):
    monkeypatch.setattr(sc, "split_sentences", lambda _text: ["ambient", "drift"] * 20)
    a = sc.extract_features(
        TARGET_TEXT,
        include_spacy=False,
        sentence_splitter=s5.regex_split_sentences,
        families={"punctuation"},
        allow_non_prose=True,
    )
    monkeypatch.setattr(sc, "split_sentences", lambda _text: [])
    b = sc.extract_features(
        TARGET_TEXT,
        include_spacy=False,
        sentence_splitter=s5.regex_split_sentences,
        families={"punctuation"},
        allow_non_prose=True,
    )
    assert a == b


def test_degenerate_family_refuses_instead_of_emitting_zero():
    entries = [
        {
            "id": f"same-{index}",
            "path": f"same-{index}.txt",
            "text": "Identical syntax, identical punctuation. Another sentence!\n",
            "metadata": {"register": None},
        }
        for index in range(3)
    ]
    with pytest.raises(s5.S5DistanceError, match="no nonzero-SD feature"):
        s5._score(TARGET_TEXT, entries, nlp=FakeNLP())


def test_import_never_attempts_punkt_download(tmp_path: Path):
    fake = tmp_path / "nltk"
    fake.mkdir()
    (fake / "__init__.py").write_text(
        "class Data:\n"
        "    def find(self, _name): raise LookupError('missing')\n"
        "data = Data()\n"
        "def download(*_a, **_kw): raise AssertionError('network download attempted')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(tmp_path), str(SCRIPTS)))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", "import s5_distance; print(s5_distance.SENTENCE_SPLITTER_ID)"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == s5.SENTENCE_SPLITTER_ID
