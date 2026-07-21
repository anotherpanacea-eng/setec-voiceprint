"""Synthetic contract tests for the internal v3 mirror gate."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
SCRIPT = HERE / "_mirror_gate.py"
SPEC = importlib.util.spec_from_file_location("mirror_gate", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate
SPEC.loader.exec_module(gate)


def run(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, check=False)


def pair(tmp_path: Path, source: bytes, mirror: bytes) -> tuple[Path, Path]:
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(source); b.write_bytes(mirror)
    return a, b


def sidecar(source: bytes, regions: list[list[tuple[int, int]]], complete: bool = True) -> bytes:
    return json.dumps({"schema_version": "setec-mirror-quote-regions/1", "source_sha256": hashlib.sha256(source).hexdigest(), "complete": complete, "regions": [{"spans": [{"start_byte": a, "end_byte": b} for a, b in row]} for row in regions]}).encode()


def test_legacy_order_and_binary_lf_cli(tmp_path: Path) -> None:
    source, mirror = pair(tmp_path, b"alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon", b"red blue green yellow orange purple black white gray cyan magenta gold silver bronze ivory teal amber coral navy violet")
    out = run("--register", "unknown", str(source), str(mirror))
    assert out.returncode == 0 and out.stderr == b"" and out.stdout.endswith(b"\n") and not out.stdout.endswith(b"\r\n")
    value = json.loads(out.stdout)
    legacy_types = {
        "source_words": int,
        "mirror_words": int,
        "ratio": float,
        "paragraphs_source": int,
        "paragraphs_mirror": int,
        "similarity": float,
        "entity_retention": float,
        "entities_source": int,
        "ok_len": bool,
        "ok_par": bool,
        "ok_sim": bool,
        "ok_ent": bool,
        "all_pass": bool,
    }
    assert list(value)[:13] == list(legacy_types)
    assert all(type(value[key]) is expected for key, expected in legacy_types.items())
    assert list(value)[13:] == ["gate_v3"]


@pytest.mark.parametrize("args", [(), ("x", "y", "z"), ("--register", "bad", "x", "y"), ("--nope", "x", "y")])
def test_usage_is_closed(args: tuple[str, ...]) -> None:
    out = run(*args)
    assert out.returncode == 2 and out.stdout == b"" and out.stderr == b"mirror_gate_error:usage_error\n"


def test_sidecar_errors_are_closed(tmp_path: Path) -> None:
    a, b = pair(tmp_path, b"ok", b"ok")
    bad = tmp_path / "bad.json"; bad.write_bytes(b"{")
    out = run(str(a), str(b), "--quote-spans", str(bad))
    assert out.returncode == 3 and out.stdout == b"" and out.stderr == b"mirror_gate_error:sidecar_invalid_json\n"
    raw = "xéy".encode(); a, b = pair(tmp_path, raw, raw)
    bad.write_bytes(sidecar(raw, [[(1, 2)]]))
    assert run(str(a), str(b), "--quote-spans", str(bad)).stderr == b"mirror_gate_error:sidecar_invalid_schema\n"


def test_register_matrix_and_grouped_region(tmp_path: Path) -> None:
    raw = b"Lead:\n> alpha\n> beta\n\n" + b"one two three four five six seven eight nine ten eleven twelve thirteen fourteen"
    a, b = pair(tmp_path, raw, raw.replace(b"one", b"zero"))
    published = json.loads(run(str(a), str(b), "--register", "published").stdout)
    assert published["gate_v3"]["quotes"]["reason"] == "complete_annotation_required"
    sc = tmp_path / "s.json"; sc.write_bytes(sidecar(raw, [[(8, 14), (16, 21)]]))
    value = json.loads(run(str(a), str(b), "--register", "published", "--quote-spans", str(sc)).stdout)
    quotes = value["gate_v3"]["quotes"]
    assert quotes["source_regions"] == quotes["preserved_regions"] == 1 and quotes["ok"] is True


def test_complete_omission_is_completed_quote_failure(tmp_path: Path) -> None:
    raw = b'"alpha" one two three four five six seven eight nine ten eleven twelve thirteen fourteen'
    a, b = pair(tmp_path, raw, raw.replace(b"one", b"zero"))
    sc = tmp_path / "s.json"; sc.write_bytes(sidecar(raw, []))
    value = json.loads(run(str(a), str(b), "--register", "published", "--quote-spans", str(sc)).stdout)
    assert value["gate_v3"]["quotes"]["reason"] == "complete_annotation_omits_detected_source_quote"


def test_entities_and_exact_copy_threshold() -> None:
    _, _, ok, ext = gate._entity_result("Now GPT4 arrived. Alice Smith met Alice Smith.", "Now GPT4 arrived. Alice met Alice.", "published")
    assert not ok and ext["hard_missing"] >= 1 and ext["published_retention_below_0_90"] is True
    source = b" ".join(f"s{i}".encode() for i in range(50))
    mirror15 = b" ".join([f"s{i}".encode() for i in range(15)] + [f"m{i}".encode() for i in range(35)])
    mirror16 = b" ".join([f"s{i}".encode() for i in range(16)] + [f"m{i}".encode() for i in range(34)])
    assert gate._exact_copy(source, mirror15, bytearray(len(source)), bytearray(len(mirror15)))["ok"] is True
    assert gate._exact_copy(source, mirror16, bytearray(len(source)), bytearray(len(mirror16)))["ok"] is False


def sc_value(source: bytes, regions: list[list[tuple[int, int]]], complete: bool = True) -> dict[str, object]:
    return json.loads(sidecar(source, regions, complete))


def test_closed_aggregate_schema_mappings_and_hard_pass() -> None:
    source = b"alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon"
    mirror = b"red blue green yellow orange purple black white gray cyan magenta gold silver bronze ivory teal amber coral navy violet"
    value = gate.evaluate(source, mirror)
    assert value["all_pass"] is True
    assert value["gate_v3"]["all_hard_pass"] is value["all_pass"]
    assert set(value["gate_v3"]) == {"schema_version", "register", "quotes", "entities", "advisories", "exact_copy", "all_hard_pass"}
    assert set(value["gate_v3"]["quotes"]) == {"annotation_complete", "source_regions", "preserved_regions", "ok", "reason", "automatic_scope"}
    assert set(value["gate_v3"]["entities"]) == {"hard_source", "hard_missing", "advisory_source", "published_retention_below_0_90"}
    assert set(value["gate_v3"]["advisories"]) == {"similarity_below_0_15"}
    assert set(value["gate_v3"]["exact_copy"]) == {"eligible_mirror_tokens", "covered_mirror_tokens", "coverage", "ok", "reason"}
    failed = gate.evaluate(source, source)
    assert failed["ok_sim"] is False and failed["gate_v3"]["exact_copy"]["ok"] is False and failed["all_pass"] is False


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("utf8", "input_invalid_utf8"),
        ("bytes", "input_too_large"),
        ("tokens", "input_token_limit"),
    ],
)
def test_input_error_codes(tmp_path: Path, kind: str, code: str) -> None:
    raw = {"utf8": b"\xff", "bytes": b"x" * (gate.MAX_BYTES + 1), "tokens": (b"x " * gate.MAX_TOKENS) + b"x"}[kind]
    source, mirror = pair(tmp_path, raw, b"safe")
    out = run(str(source), str(mirror))
    assert out.returncode == 3 and out.stdout == b"" and out.stderr == f"mirror_gate_error:{code}\n".encode()


def test_input_exact_ceilings_and_mocked_permission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    byte_limit = tmp_path / "bytes.txt"; byte_limit.write_bytes(b"x" * gate.MAX_BYTES)
    assert len(gate._read_input(str(byte_limit))) == gate.MAX_BYTES
    token_limit = tmp_path / "tokens.txt"; token_limit.write_bytes(b" ".join([b"x"] * gate.MAX_TOKENS))
    assert len(gate._read_input(str(token_limit)).split()) == gate.MAX_TOKENS
    monkeypatch.setattr(gate.Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(gate.MirrorGateError, match="input_unreadable"):
        gate._read_input("private-sentinel")


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"\xff", "sidecar_invalid_utf8"),
        (b"{", "sidecar_invalid_json"),
        (b"[]", "sidecar_invalid_schema"),
    ],
)
def test_sidecar_decode_error_codes(tmp_path: Path, payload: bytes, code: str) -> None:
    source, mirror = pair(tmp_path, b"safe", b"safe")
    sc = tmp_path / "sidecar"; sc.write_bytes(payload)
    out = run(str(source), str(mirror), "--quote-spans", str(sc))
    assert out.returncode == 3 and out.stdout == b"" and out.stderr == f"mirror_gate_error:{code}\n".encode()


def test_sidecar_unreadable_too_large_stale_and_exact_byte_limit(tmp_path: Path) -> None:
    source, mirror = pair(tmp_path, b"safe", b"safe")
    assert run(str(source), str(mirror), "--quote-spans", str(tmp_path / "missing")).stderr == b"mirror_gate_error:sidecar_unreadable\n"
    sc = tmp_path / "sidecar"
    sc.write_bytes(b" " * (gate.MAX_BYTES + 1))
    assert run(str(source), str(mirror), "--quote-spans", str(sc)).stderr == b"mirror_gate_error:sidecar_too_large\n"
    stale = sc_value(b"other", [])
    sc.write_text(json.dumps(stale))
    assert run(str(source), str(mirror), "--quote-spans", str(sc)).stderr == b"mirror_gate_error:sidecar_stale\n"
    compact = sidecar(b"safe", [])
    sc.write_bytes(compact + b" " * (gate.MAX_BYTES - len(compact)))
    assert len(sc.read_bytes()) == gate.MAX_BYTES
    assert run(str(source), str(mirror), "--quote-spans", str(sc)).returncode == 0


@pytest.mark.parametrize(
    "regions",
    [
        [[]],
        [[(0, 0)]],
        [[(True, 1)]],
        [[(-1, 1)]],
        [[(0, 5)]],
        [[(1, 2), (0, 1)]],
        [[(0, 2), (1, 3)]],
        [[(0, 1)], [(0, 1)]],
        [[(0, 2)], [(1, 3)]],
    ],
)
def test_sidecar_coordinate_order_rejections(regions: list[list[tuple[int, int]]]) -> None:
    source = b"abcd"
    with pytest.raises(gate.MirrorGateError) as exc:
        gate._validate_sidecar(sc_value(source, regions), source)
    assert exc.value.code == "sidecar_invalid_schema"


def test_sidecar_eof_utf8_and_span_limits() -> None:
    source = "éx".encode()
    complete, regions = gate._validate_sidecar(sc_value(source, [[(0, len(source))]]), source)
    assert complete and regions[0].spans == (gate.Span(0, len(source)),)
    with pytest.raises(gate.MirrorGateError, match="sidecar_invalid_schema"):
        gate._validate_sidecar(sc_value(source, [[(1, 2)]]), source)
    large_source = b"x " * 4097
    rows_4096 = [[(i * 2, i * 2 + 1)] for i in range(4096)]
    assert len(gate._validate_sidecar(sc_value(large_source, rows_4096), large_source)[1]) == 4096
    with pytest.raises(gate.MirrorGateError) as exc:
        gate._validate_sidecar(sc_value(large_source, rows_4096 + [[(8192, 8193)]]), large_source)
    assert exc.value.code == "sidecar_span_limit"


def test_sidecar_grouping_and_reconciliation_rules() -> None:
    source = b"> alpha\n> beta\nplain"
    automatic = gate._automatic_regions(source)
    assert len(automatic) == 1
    matching = gate.Region((gate.Span(2, 8), gate.Span(10, 15)), gate.Span(2, 15))
    merged, verified = gate._reconcile(source, automatic, [matching], True)
    assert len(merged) == 1 and verified
    partial = gate.Region((gate.Span(2, 8),), gate.Span(2, 8))
    with pytest.raises(gate.MirrorGateError, match="sidecar_invalid_schema"):
        gate._reconcile(source, automatic, [partial], True)
    bare_multi = gate.Region((gate.Span(16, 17), gate.Span(18, 19)), gate.Span(16, 19))
    with pytest.raises(gate.MirrorGateError, match="sidecar_invalid_schema"):
        gate._reconcile(source, automatic, [bare_multi], True)


@pytest.mark.parametrize(
    ("raw", "payloads"),
    [
        (b"> x\n", [b"x\n"]),
        (b"   > x\r\n", [b"x\r\n"]),
        (b"    > x\n", []),
        (b">  x\n", [b" x\n"]),
        (b">\tx\r", [b"\tx\r"]),
        (b">\n", [b"\n"]),
        (b">", []),
        (b'""', []),
    ],
)
def test_markdown_and_empty_payload_grammar(raw: bytes, payloads: list[bytes]) -> None:
    assert [r.payload(raw) for r in gate._automatic_regions(raw)] == payloads


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"Intro:\n  alpha\n  beta\n", b"alpha\nbeta\n"),
        (b"Intro:\n\talpha\n\t\tbeta\r", b"alpha\n\tbeta\r"),
        (b"Intro:\n alpha\n", None),
        (b"Intro:\n\n  alpha\n", None),
        (b"Intro: \t\n   alpha\n  beta\n", b"alpha\n"),
        (b"Intro:\n\talpha\n  beta\n", b"alpha\n"),
    ],
)
def test_colon_block_grammar(raw: bytes, expected: bytes | None) -> None:
    regions = [r for r in gate._automatic_regions(raw) if r.kind == "colon"]
    assert ([r.payload(raw) for r in regions] if regions else None) == ([expected] if expected is not None else None)


def test_automatic_precedence_discards_whole_lower_candidates() -> None:
    markdown_over_colon = b"Intro:\n  > X\n"
    regions = gate._automatic_regions(markdown_over_colon)
    assert len(regions) == 1 and regions[0].kind == "markdown" and regions[0].payload(markdown_over_colon) == b"X\n"
    colon_over_inline = b'Intro:\n  "X"\n'
    regions = gate._automatic_regions(colon_over_inline)
    assert len(regions) == 1 and regions[0].kind == "colon" and regions[0].payload(colon_over_inline) == b'"X"\n'
    nested = b'> "X"\n'
    assert [r.kind for r in gate._automatic_regions(nested)] == ["markdown"]


def test_inline_styles_paragraph_boundary_unmatched_and_curly_apostrophe() -> None:
    raw = '"one\ntwo" “three” ‘don’t alpha’'.encode()
    assert [r.payload(raw).decode() for r in gate._automatic_regions(raw)] == ["one\ntwo", "three", "don’t alpha"]
    boundary = b'"open\n\nnext paragraph'
    started = time.monotonic()
    assert gate._inline_regions(boundary) == []
    assert time.monotonic() - started < 1.0
    source = '‘don’t alpha’'.encode(); mirror = '‘don’t beta’'.encode()
    assert gate.evaluate(source, mirror)["gate_v3"]["quotes"]["reason"] == "quote_fidelity_failed"


def quote_result(source: bytes, mirror: bytes, register: str = "unknown", annotations: tuple[bool, list[gate.Region]] | None = None) -> dict[str, object]:
    return gate.evaluate(source, mirror, register, annotations)["gate_v3"]["quotes"]


@pytest.mark.parametrize(
    ("source", "mirror", "reason", "preserved"),
    [
        (b'"a" then "a"', b'"a" then "a"', "ok", 2),
        (b'"a" then "a"', b'"a"', "quote_fidelity_failed", 1),
        (b'"a" then "b"', b'"b" then "a"', "quote_fidelity_failed", 1),
        (b'"a\nb"', b'"a\r\nb"', "quote_fidelity_failed", 0),
        (b'"a"', b'"b"', "quote_fidelity_failed", 0),
    ],
)
def test_ordered_quote_fidelity(source: bytes, mirror: bytes, reason: str, preserved: int) -> None:
    result = quote_result(source, mirror)
    assert result["reason"] == reason and result["preserved_regions"] == preserved


def test_raw_quote_matches_multiplicity_added_and_scope() -> None:
    # Source annotations can match unmarked raw mirror occurrences in order.
    source = b"a-a"
    annotations = (False, [gate.Region((gate.Span(0, 1),), gate.Span(0, 1)), gate.Region((gate.Span(2, 3),), gate.Span(2, 3))])
    assert quote_result(source, b"a x a", annotations=annotations)["preserved_regions"] == 2
    assert quote_result(source, b"a", annotations=annotations)["reason"] == "quote_fidelity_failed"
    added = quote_result(b"plain", b'plain "added"')
    assert added["reason"] == "added_mirror_quote_region" and added["automatic_scope"] == gate.SCOPE
    # A raw substring cannot consume or mask a larger detected region.
    bare = (False, [gate.Region((gate.Span(0, 5),), gate.Span(0, 5))])
    conflict = quote_result(b"alpha", b"> alpha beta\n", annotations=bare)
    assert conflict["reason"] == "quote_fidelity_failed"
    arbitrary = quote_result(b"plain", b"plain bare restatement")
    assert arbitrary["reason"] == "ok" and arbitrary["automatic_scope"] == gate.SCOPE


def test_quote_reason_precedence_and_register_matrix() -> None:
    source, mirror = b'"lost"', b'"added"'
    for register in ("published", "informal"):
        result = quote_result(source, mirror, register)
        assert result["reason"] == "complete_annotation_required" and result["ok"] is False
        assert result["source_regions"] == 1 and result["preserved_regions"] == 0
    source_auto = gate._automatic_regions(source)
    mirror_auto = gate._automatic_regions(mirror)
    direct, source_mask, mirror_mask = gate._quote_fidelity(
        source, mirror, source_auto, mirror_auto, True, False
    )
    assert direct["reason"] == "complete_annotation_required"
    assert direct["source_regions"] == 1 and direct["preserved_regions"] == 0
    assert bytes(source_mask) == b"\x00" + b"\x01" * 4 + b"\x00"
    assert bytes(mirror_mask) == b"\x00" + b"\x01" * 5 + b"\x00"
    exact = gate._exact_copy(source, mirror, source_mask, mirror_mask)
    assert exact == {
        "eligible_mirror_tokens": 0,
        "covered_mirror_tokens": 0,
        "coverage": None,
        "ok": False,
        "reason": "insufficient_nonquote_evidence",
    }
    assert quote_result(source, mirror, "unknown")["reason"] == "quote_fidelity_failed"
    incomplete = (False, [])
    assert quote_result(b"plain", b"plain", "published", incomplete)["reason"] == "complete_annotation_required"
    complete_empty = (True, [])
    result = quote_result(b"plain", b"plain", "published", complete_empty)
    assert result["annotation_complete"] is True and result["reason"] == "ok"


def test_bounded_fidelity_4096_repetitive_regions() -> None:
    source = b"a " * 4096
    regions = [gate.Region((gate.Span(i * 2, i * 2 + 1),), gate.Span(i * 2, i * 2 + 1)) for i in range(4096)]
    started = time.monotonic()
    result, _, _ = gate._quote_fidelity(source, source, regions, [], False, False)
    assert result["preserved_regions"] == 4096 and result["reason"] == "ok"
    automatic, _, _ = gate._quote_fidelity(source, source, regions, regions, False, False)
    assert automatic["preserved_regions"] == 4096 and automatic["reason"] == "ok"
    assert time.monotonic() - started < 5.0


@pytest.mark.parametrize("token", ["GPT4", "NASA", "McDonald", "O’Neil", "Jean-Luc"])
def test_entity_strong_token_classes(token: str) -> None:
    source = f"{token} arrived." if token in {"GPT4", "NASA", "McDonald"} else f"{token} arrived. we met {token}."
    retention, count, ok, ext = gate._entity_result(source, "nothing remained.", "unknown")
    assert count == 1 and retention == 0 and not ok and ext["hard_source"] == ext["hard_missing"] == 1


def test_entity_recurrence_requires_two_occurrences_and_noninitial() -> None:
    assert gate._entity_result("Alice Smith", "none", "unknown")[3]["hard_source"] == 0
    assert gate._entity_result("we met Alice", "none", "unknown")[3]["hard_source"] == 0
    assert gate._entity_result("Alice rested. we met Alice", "none", "unknown")[3]["hard_source"] == 1
    # A hard multi-token phrase is atomic.
    _, _, ok, ext = gate._entity_result("Alice Smith rested. we met Alice Smith", "Alice remained", "unknown")
    assert not ok and ext["hard_missing"] == 1
    assert gate._entity_result("Alice Smith rested. we met Alice Smith", "Alice Smith remained", "unknown")[2] is True


@pytest.mark.parametrize("connective", sorted(gate._CONNECTIVES))
def test_initial_connectives_are_suppressed_but_following_strong_token_survives(connective: str) -> None:
    assert gate._phrases(connective) == []
    if connective == "Now":
        phrases = gate._phrases("Now GPT4")
        assert [(p, hard) for p, hard, _ in phrases] == [("GPT4", True)]


def test_connective_suppression_is_exact_positional_and_mirror_retention_is_direct() -> None:
    phrases = {p for p, _, _ in gate._phrases("we said Now. now ThereforeX. therefore Yet, Again.")}
    assert {"Now", "ThereforeX", "Yet", "Again"} <= phrases
    # Two noninitial source occurrences make Now hard; an initial mirror Now retains it.
    retention, _, ok, ext = gate._entity_result("we said Now. they repeated Now.", "Now we begin.", "unknown")
    assert retention == 1 and ok and ext["hard_missing"] == 0


def test_entity_unique_phrase_denominator_and_register_advisory() -> None:
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet"]
    source = ". ".join(names) + "."
    mirror_nine = ". ".join(names[:9]) + "."
    retention, count, ok, published = gate._entity_result(source, mirror_nine, "published")
    assert count == 10 and retention == 0.9 and ok and published["published_retention_below_0_90"] is False
    below_source = source + " Kilo."
    _, _, _, below = gate._entity_result(below_source, mirror_nine, "published")
    assert below["published_retention_below_0_90"] is True
    assert gate._entity_result(below_source, mirror_nine, "informal")[3]["published_retention_below_0_90"] is None
    assert gate._entity_result(below_source, mirror_nine, "unknown")[3]["published_retention_below_0_90"] is None
    repeated = gate._entity_result("GPT4 arrived. GPT4 returned.", "GPT4 remains.", "unknown")
    assert repeated[1] == 1 and repeated[3]["hard_source"] == 1


@pytest.mark.parametrize(
    ("similarity", "lower_advisory", "ok_sim"),
    [(0.149999, True, True), (0.15, False, True), (0.75, False, True), (0.750001, False, False)],
)
def test_similarity_unrounded_edges(monkeypatch: pytest.MonkeyPatch, similarity: float, lower_advisory: bool, ok_sim: bool) -> None:
    monkeypatch.setattr(gate, "_similarity", lambda a, b: similarity)
    source = b"one two three four five six seven eight nine ten eleven twelve thirteen"
    mirror = b"red blue green yellow orange purple black white gray cyan magenta gold silver"
    result = gate.evaluate(source, mirror)
    assert result["gate_v3"]["advisories"]["similarity_below_0_15"] is lower_advisory
    assert result["ok_sim"] is ok_sim


def test_similarity_keeps_default_sequence_matcher_autojunk(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    class FakeMatcher:
        def __init__(self, *args: object, **kwargs: object):
            captured["args"], captured["kwargs"] = args, kwargs
        def ratio(self) -> float:
            return 0.5
    monkeypatch.setattr(gate.difflib, "SequenceMatcher", FakeMatcher)
    assert gate._similarity("a", "b") == 0.5
    assert captured == {"args": (None, "a", "b"), "kwargs": {}}


def exact(source_tokens: list[bytes], mirror_tokens: list[bytes], source_mask: bytearray | None = None, mirror_mask: bytearray | None = None) -> dict[str, object]:
    source, mirror = b" ".join(source_tokens), b" ".join(mirror_tokens)
    return gate._exact_copy(source, mirror, source_mask or bytearray(len(source)), mirror_mask or bytearray(len(mirror)))


def test_exact_run_12_13_overlap_duplicate_and_insufficient() -> None:
    source = [f"s{i}".encode() for i in range(50)]
    run12 = exact(source, source[:12] + [f"m{i}".encode() for i in range(38)])
    assert run12["covered_mirror_tokens"] == 0
    run13 = exact(source, source[:13] + [f"m{i}".encode() for i in range(37)])
    assert run13["covered_mirror_tokens"] == 13
    overlap = exact(source, source[:14] + [f"m{i}".encode() for i in range(36)])
    assert overlap["covered_mirror_tokens"] == 14
    duplicate_source = source[:13] + [b"gap"] + source[:13]
    duplicate_mirror = source[:13] + [b"other"] + source[:13] + [b"tail"] * 30
    assert exact(duplicate_source, duplicate_mirror)["covered_mirror_tokens"] == 26
    for count in (0, 12):
        result = exact([], [f"m{i}".encode() for i in range(count)])
        assert result["coverage"] is None and result["ok"] is False and result["reason"] == "insufficient_nonquote_evidence"


def test_exact_run_quote_mask_and_paragraph_boundary_do_not_bridge() -> None:
    tokens = [f"s{i}".encode() for i in range(13)]
    source = b" ".join(tokens)
    mirror = source
    source_mask = bytearray(len(source)); source_mask[source.find(b"s6"):source.find(b"s6") + 2] = b"\x01\x01"
    assert gate._exact_copy(source, mirror, source_mask, bytearray(len(mirror)))["covered_mirror_tokens"] == 0
    broken = b" ".join(tokens[:6]) + b"\n\n" + b" ".join(tokens[6:])
    assert gate._exact_copy(broken, broken, bytearray(len(broken)), bytearray(len(broken)))["covered_mirror_tokens"] == 0
    mirror_mask = bytearray(len(mirror)); mirror_mask[:] = b"\x01" * len(mirror)
    result = gate._exact_copy(source, mirror, bytearray(len(source)), mirror_mask)
    assert result["eligible_mirror_tokens"] == 0 and result["coverage"] is None


def test_newline_canonicalization_preserves_legacy_metrics_but_sha_is_raw() -> None:
    lf = b"one two\n\nthree four"
    crlf = b"one two\r\n\r\nthree four"
    cr = b"one two\r\rthree four"
    values = [gate.evaluate(raw, b"red blue\n\ngreen yellow") for raw in (lf, crlf, cr)]
    fields = ("source_words", "paragraphs_source", "ratio", "entities_source")
    assert [{key: value[key] for key in fields} for value in values].count({key: values[0][key] for key in fields}) == 3
    assert hashlib.sha256(lf).hexdigest() != hashlib.sha256(crlf).hexdigest() != hashlib.sha256(cr).hexdigest()


def test_ceilings_return_before_similarity_and_exact_stress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfdbinary: pytest.CaptureFixture[bytes]) -> None:
    safe = tmp_path / "safe"; safe.write_bytes(b"safe")
    too_many = tmp_path / "too-many"; too_many.write_bytes((b"x " * gate.MAX_TOKENS) + b"x")
    monkeypatch.setattr(gate, "_similarity", lambda *args: (_ for _ in ()).throw(AssertionError("called")))
    assert gate.main([str(too_many), str(safe)]) == 3
    captured = capfdbinary.readouterr()
    assert captured.out == b"" and captured.err == b"mirror_gate_error:input_token_limit\n"
    tokens = [f"t{i}".encode() for i in range(gate.MAX_TOKENS)]
    result = exact(tokens, tokens)
    assert result["eligible_mirror_tokens"] == gate.MAX_TOKENS
    assert result["covered_mirror_tokens"] == gate.MAX_TOKENS and result["reason"] == "over_0_30"


def test_privacy_sentinels_absent_from_outputs_and_errors(tmp_path: Path) -> None:
    source_sentinel = "SrcQ7vM2zK9"
    mirror_sentinel = "MirP8xN4cL6"
    source = (source_sentinel + " " + " ".join(f"a{i}" for i in range(20))).encode()
    mirror = (mirror_sentinel + " " + " ".join(f"b{i}" for i in range(20))).encode()
    a, b = pair(tmp_path, source, mirror)
    passed = run(str(a), str(b))
    failed = run(str(a), str(a))
    missing_path = str(tmp_path / "PathR5jT3wH8")
    errored = run(missing_path, str(b))
    for output in (passed.stdout, passed.stderr, failed.stdout, failed.stderr, errored.stdout, errored.stderr):
        for sentinel in (source_sentinel, mirror_sentinel, "PathR5jT3wH8"):
            assert sentinel.encode() not in output and json.dumps(sentinel).encode() not in output


def test_sidecar_closed_keys_and_types() -> None:
    source = b"abcd"
    base = sc_value(source, [[(0, 1)]])
    variants: list[dict[str, object]] = []
    extra_top = dict(base); extra_top["extra"] = 1; variants.append(extra_top)
    missing_top = dict(base); missing_top.pop("complete"); variants.append(missing_top)
    bad_complete = dict(base); bad_complete["complete"] = 1; variants.append(bad_complete)
    bad_schema = dict(base); bad_schema["schema_version"] = "other"; variants.append(bad_schema)
    bad_sha = dict(base); bad_sha["source_sha256"] = str(base["source_sha256"]).upper(); variants.append(bad_sha)
    extra_region = json.loads(json.dumps(base)); extra_region["regions"][0]["extra"] = 1; variants.append(extra_region)
    extra_span = json.loads(json.dumps(base)); extra_span["regions"][0]["spans"][0]["extra"] = 1; variants.append(extra_span)
    for value in variants:
        with pytest.raises(gate.MirrorGateError) as exc:
            gate._validate_sidecar(value, source)
        assert exc.value.code == "sidecar_invalid_schema"


def test_all_one_over_ceilings_precede_similarity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfdbinary: pytest.CaptureFixture[bytes]) -> None:
    monkeypatch.setattr(gate, "_similarity", lambda *args: (_ for _ in ()).throw(AssertionError("similarity called")))
    safe = tmp_path / "safe"; safe.write_bytes(b"x")
    cases: list[tuple[list[str], bytes]] = []
    too_large = tmp_path / "too-large"; too_large.write_bytes(b"x" * (gate.MAX_BYTES + 1))
    cases.append(([str(too_large), str(safe)], b"mirror_gate_error:input_too_large\n"))
    too_many = tmp_path / "too-many"; too_many.write_bytes((b"x " * gate.MAX_TOKENS) + b"x")
    cases.append(([str(too_many), str(safe)], b"mirror_gate_error:input_token_limit\n"))
    sidecar_large = tmp_path / "sidecar-large"; sidecar_large.write_bytes(b" " * (gate.MAX_BYTES + 1))
    cases.append(([str(safe), str(safe), "--quote-spans", str(sidecar_large)], b"mirror_gate_error:sidecar_too_large\n"))
    span_source = b"x " * 4097
    span_file = tmp_path / "span-source"; span_file.write_bytes(span_source)
    span_sidecar = tmp_path / "span-sidecar"; span_sidecar.write_bytes(sidecar(span_source, [[(i * 2, i * 2 + 1)] for i in range(4097)]))
    cases.append(([str(span_file), str(span_file), "--quote-spans", str(span_sidecar)], b"mirror_gate_error:sidecar_span_limit\n"))
    for argv, expected in cases:
        assert gate.main(argv) == 3
        captured = capfdbinary.readouterr()
        assert captured.out == b"" and captured.err == expected


def test_unexpected_internal_error_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfdbinary: pytest.CaptureFixture[bytes]) -> None:
    source, mirror = pair(tmp_path, b"safe", b"safe")
    monkeypatch.setattr(gate, "evaluate", lambda *args: (_ for _ in ()).throw(RuntimeError("UserLeakQ9x7")))
    assert gate.main([str(source), str(mirror)]) == 3
    captured = capfdbinary.readouterr()
    assert captured.out == b"" and captured.err == b"mirror_gate_error:internal_error\n"
    assert b"UserLeakQ9x7" not in captured.err


def test_privacy_quote_entity_sha_id_offset_and_position_sentinels(tmp_path: Path) -> None:
    quote = "QuoteV8qJ3m"
    entity = "EntityQ7Z9"
    identifier = "IdentifierR6wK2p"
    position = "PositionT5nH4c"
    source_raw = (f'“{quote}” {entity} {identifier} {position} ' + " ".join(f"s{i}" for i in range(20))).encode()
    mirror_raw = (f'“{quote}” {entity} {identifier} {position} ' + " ".join(f"m{i}" for i in range(20))).encode()
    source, mirror = pair(tmp_path, source_raw, mirror_raw)
    sc = tmp_path / "OffsetU4bL8d.json"
    auto = gate._automatic_regions(source_raw)[0]
    sc.write_bytes(sidecar(source_raw, [[(span.start, span.end) for span in auto.spans]]))
    output = run(str(source), str(mirror), "--quote-spans", str(sc)).stdout
    sha = hashlib.sha256(source_raw).hexdigest()
    for sentinel in (quote, entity, identifier, position, "OffsetU4bL8d", sha):
        assert sentinel.encode() not in output and json.dumps(sentinel).encode() not in output


def test_forbidden_extent_jump_and_failed_search_memoization_at_input_ceiling() -> None:
    class CountingBytes(bytes):
        find_calls = 0
        def find(self, *args: object) -> int:
            type(self).find_calls += 1
            return super().find(*args)

    source = b"x " * gate.MAX_QUOTE_SPANS
    source_regions = [
        gate.Region((gate.Span(i * 2, i * 2 + 1),), gate.Span(i * 2, i * 2 + 1))
        for i in range(gate.MAX_QUOTE_SPANS)
    ]
    mirror = CountingBytes(b"x" * gate.MAX_BYTES)
    # One effective marked region owns the entire repetitive mirror. Its
    # payload is not equal to a one-byte source region, and no raw occurrence
    # inside its structural extent is eligible.
    marked = gate.Region((gate.Span(0, len(mirror)),), gate.Span(0, len(mirror)), "markdown")
    result, _, _ = gate._quote_fidelity(source, mirror, source_regions, [marked], False, False)
    assert result["preserved_regions"] == 0 and result["reason"] == "quote_fidelity_failed"
    # One probe finds the forbidden occurrence; one probe at extent.end proves
    # exhaustion. The other 4,095 identical misses reuse that result.
    assert CountingBytes.find_calls == 2


def test_earliest_raw_candidate_still_precedes_later_automatic_match() -> None:
    source_region = [gate.Region((gate.Span(0, 1),), gate.Span(0, 1))]
    mirror = b"x\n> x"
    automatic = gate._automatic_regions(mirror)
    result, _, _ = gate._quote_fidelity(b"x", mirror, source_region, automatic, False, False)
    assert result["preserved_regions"] == 1
    assert result["reason"] == "added_mirror_quote_region"  # earlier raw x consumed
    auto_first = b"> x"
    result, _, _ = gate._quote_fidelity(b"x", auto_first, source_region, gate._automatic_regions(auto_first), False, False)
    assert result["preserved_regions"] == 1 and result["reason"] == "ok"


def test_mirror_retention_requires_same_maximal_phrase_both_directions() -> None:
    forward = gate._entity_result("Alice rested. we met Alice.", "Alice Smith arrived.", "unknown")
    assert forward[3]["hard_source"] == 1 and forward[3]["hard_missing"] == 1 and not forward[2]
    reverse = gate._entity_result("Alice Smith rested. we met Alice Smith.", "Alice arrived.", "unknown")
    assert reverse[3]["hard_source"] == 1 and reverse[3]["hard_missing"] == 1 and not reverse[2]
    now = gate._entity_result("we said Now. they repeated Now.", "Now we begin.", "unknown")
    assert now[3]["hard_source"] == 1 and now[3]["hard_missing"] == 0 and now[2]
    now_gpt4 = gate._entity_result("Now GPT4", "Now GPT4", "unknown")
    assert now_gpt4[3]["hard_source"] == 1 and now_gpt4[3]["hard_missing"] == 0 and now_gpt4[2]


def test_automatic_precedence_probe_count_and_small_bruteforce_oracle(monkeypatch: pytest.MonkeyPatch) -> None:
    original = gate._overlaps_sorted
    probes = 0
    def counted(*args: object) -> bool:
        nonlocal probes
        probes += 1
        return original(*args)
    monkeypatch.setattr(gate, "_overlaps_sorted", counted)
    raw = b'"a" ' * 8000
    regions = gate._automatic_regions(raw)
    assert len(regions) == 8000 and all(region.kind == "inline" for region in regions)
    assert probes == 8000

    candidates = [
        gate.Region((gate.Span(0, 8),), gate.Span(0, 8), "inline"),
        gate.Region((gate.Span(0, 3),), gate.Span(0, 3), "inline"),
        gate.Region((gate.Span(4, 6),), gate.Span(4, 6), "inline"),
        gate.Region((gate.Span(8, 10),), gate.Span(8, 10), "inline"),
    ]
    higher = [gate.Region((gate.Span(5, 7),), gate.Span(5, 7), "markdown")]
    expected: list[gate.Region] = []
    for candidate in sorted(candidates, key=lambda r: (r.extent.start, -(r.extent.end - r.extent.start))):
        if not any(gate._overlap(candidate.extent, prior.extent) for prior in higher + expected):
            expected.append(candidate)
    assert gate._accept_priority_class(candidates, higher) == expected


def test_colon_endpoint_probe_count_and_markdown_reveals_inner_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    original = gate._colon_candidate_end
    probes = 0
    def counted(*args: object) -> int:
        nonlocal probes
        probes += 1
        return original(*args)
    monkeypatch.setattr(gate, "_colon_candidate_end", counted)
    raw = ("Root:\n" + "  item:\n" * 1999 + "  tail\n").encode()
    regions = gate._automatic_regions(raw)
    assert len(regions) == 1 and regions[0].kind == "colon"
    assert probes == 2000

    interleaved = b"Root:\n  > marked\n  Inner:\n    payload\n"
    effective = gate._automatic_regions(interleaved)
    assert [region.kind for region in effective] == ["markdown", "colon"]
    assert [region.payload(interleaved) for region in effective] == [b"marked\n", b"payload\n"]


def test_inline_unmatched_opener_closer_lookup_count(monkeypatch: pytest.MonkeyPatch) -> None:
    original = gate._next_inline_closer
    lookups = 0
    def counted(*args: object) -> int | None:
        nonlocal lookups
        lookups += 1
        return original(*args)
    monkeypatch.setattr(gate, "_next_inline_closer", counted)
    raw = ("“ " * gate.MAX_TOKENS).encode()
    assert gate._inline_regions(raw) == []
    assert lookups == gate.MAX_TOKENS


def test_entity_initial_classification_one_scan_at_token_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    original = gate._initial_flags
    original_initial = gate._initial
    scans = 0
    def counted(*args: object) -> dict[int, bool]:
        nonlocal scans
        scans += 1
        return original(*args)
    monkeypatch.setattr(gate, "_initial_flags", counted)
    monkeypatch.setattr(gate, "_initial", lambda *args: (_ for _ in ()).throw(AssertionError("quadratic prefix scan")))
    text = " ".join(["Alice."] * gate.MAX_TOKENS)
    phrases = gate._phrases(text)
    assert len(phrases) == gate.MAX_TOKENS and scans == 1

    sample = "  Alice met Bob.\n  Carol asked? Then Dave.\n\nEcho"
    matches = list(gate._TOKEN_RE.finditer(sample))
    flags = original(sample, matches)
    assert flags == {match.start(): original_initial(sample, match.start()) for match in matches}


def test_reconcile_merge_walk_overlap_probe_count(monkeypatch: pytest.MonkeyPatch) -> None:
    count = gate.MAX_QUOTE_SPANS
    automatic = [gate.Region((gate.Span(i * 2, i * 2 + 1),), gate.Span(i * 2, i * 2 + 1), "inline") for i in range(count)]
    annotations = [gate.Region(region.spans, gate.Span(region.spans[0].start, region.spans[-1].end)) for region in automatic]
    original = gate._overlap
    probes = 0
    def counted(*args: object) -> bool:
        nonlocal probes
        probes += 1
        return original(*args)
    monkeypatch.setattr(gate, "_overlap", counted)
    merged, complete = gate._reconcile(b"x " * count, automatic, annotations, True)
    assert complete and merged == automatic and probes == count
