#!/usr/bin/env python3
"""Check every registered text primitive against its pinned primary and mutant cases."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/setec-voiceprint/scripts"
FIELDS = {"case_id", "callable", "args", "kwargs", "result_path", "comparator", "expected", "mutant"}
# Callables that take bytes: their args are written as {"hex": "..."}.
BYTES_ARGS = {"_analysis"}
COMPARATORS = {"json_exact", "sequence_exact", "set_exact", "bytes_hex_exact", "float_hex_exact", "exception_exact"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def encode(value, comparator):
    if comparator == "json_exact":
        return canonical(value)
    if comparator == "sequence_exact":
        if not isinstance(value, (list, tuple)):
            raise ValueError("sequence required")
        return tuple(canonical(v) for v in value)
    if comparator == "set_exact":
        if not isinstance(value, (list, set, frozenset)):
            raise ValueError("set required")
        if any(type(v) not in (str, int, float, bool, type(None)) for v in value):
            raise ValueError("set scalars required")
        encoded = [canonical(v) for v in value]
        if len(set(encoded)) != len(encoded):
            raise ValueError("duplicate set scalar")
        return tuple(sorted(encoded))
    if comparator == "bytes_hex_exact":
        if isinstance(value, bytes):
            return {"hex": value.hex()}
        if type(value) is not dict or set(value) != {"hex"}:
            raise ValueError("closed hex object required")
        h = value["hex"]
        if type(h) is not str or len(h) % 2 or any(c not in "0123456789abcdef" for c in h):
            raise ValueError("lowercase even hex required")
        return value
    if comparator == "float_hex_exact":
        if type(value) is float:
            return value.hex()
        if type(value) is not str or float.fromhex(value).hex() != value:
            raise ValueError("canonical float hex required")
        return value
    if comparator == "exception_exact":
        if type(value) is not dict or set(value) != {"type", "message"} or any(type(v) is not str for v in value.values()):
            raise ValueError("closed exception object required")
        return value
    raise ValueError("unknown comparator")


def invoke(fn, case, comparator):
    try:
        result = fn(*copy.deepcopy(case["args"]), **copy.deepcopy(case["kwargs"]))
    except Exception as exc:
        if comparator != "exception_exact":
            raise
        return encode({"type": type(exc).__module__ + "." + type(exc).__qualname__, "message": str(exc)}, comparator)
    if comparator == "exception_exact":
        raise ValueError("expected exception was not raised")
    for selector in case["result_path"]:
        result = result[selector]
    return encode(result, comparator)


def configure_punkt(resource_root):
    import hashlib
    import nltk
    if nltk.__version__ != "3.9.4":
        raise ValueError("native characterization requires NLTK 3.9.4")
    root = Path(resource_root).resolve(strict=True)
    expected = {
        "collocations.tab": "8e2da1225e4dd2cc9dba261ee231ccb134859e21b46006e7f472c5ee269af0cf",
        "sent_starters.txt": "f3f8535483e1dba487241b764945168123bca3209a9645e59acd1225dc76edac",
        "abbrev_types.txt": "92a3e070f43d9b4c5534758ca40ad7343b04e7e29bfe0c2eb658a39445a4f779",
        "ortho_context.tab": "4bbcca25ed3d3f06c02402abf8419b9f033b8adc06e7b482eca4e45f81a5dc4c",
    }
    directory = root / "tokenizers/punkt_tab/english"
    if {p.name for p in directory.iterdir()} != set(expected):
        raise ValueError("unexpected native resource files")
    for name, digest in expected.items():
        p = directory / name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise ValueError("native resource integrity failure")
    nltk.data.path[:] = [str(root)]
    if Path(str(nltk.data.find("tokenizers/punkt_tab/english/"))).resolve() != directory.resolve():
        raise ValueError("native resource resolved outside isolated root")
    nltk.tokenize._get_punkt_tokenizer.cache_clear()


def run(fixture, resource_root):
    # Restore NLTK's search path afterwards: a pytest worker must not keep
    # Punkt visible to later tests, which would flip variance_audit's
    # split_sentences backend for them.
    import nltk
    saved_path = list(nltk.data.path)
    try:
        configure_punkt(resource_root)
        return _run(fixture)
    finally:
        nltk.data.path[:] = saved_path
        nltk.tokenize._get_punkt_tokenizer.cache_clear()


def _bytes_args(case):
    args = []
    for value in case["args"]:
        if type(value) is not dict or set(value) != {"hex"}:
            raise ValueError("bytes argument must be a closed hex object")
        args.append(bytes.fromhex(encode(value, "bytes_hex_exact")["hex"]))
    return {**case, "args": args}


def _run(fixture):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from setec.core import textprims
    document = json.loads(Path(fixture).read_text(encoding="utf-8"))
    if set(document) != {"schema", "license", "rows"} or document["schema"] != "textprims-characterization/2" or not document["license"]:
        raise ValueError("invalid fixture header")
    seen, covered = set(), set()
    for row in document["rows"]:
        if set(row) != FIELDS or row["case_id"] in seen or row["comparator"] not in COMPARATORS:
            raise ValueError("invalid or duplicate fixture row")
        seen.add(row["case_id"])
        name = row["callable"].removesuffix(".__contains__")
        if name not in textprims.PRIMITIVES:
            raise ValueError("fixture names an unregistered callable: " + row["callable"])
        target = getattr(textprims, name)   # resolves through the single import point
        membership = row["callable"].endswith(".__contains__")
        if membership:
            if not isinstance(target, (set, frozenset)) or row["comparator"] != "json_exact" or type(row["expected"]) is not bool:
                raise ValueError("word-set rows are boolean membership queries")
            fn = target.__contains__
        else:
            fn = target
        mutant = row["mutant"]
        if set(mutant) != {"args", "kwargs", "result_path", "expected"}:
            raise ValueError("invalid mutant")
        cases = [row, mutant]
        for case in cases:
            if type(case["args"]) is not list or type(case["kwargs"]) is not dict or type(case["result_path"]) is not list or any(type(v) not in (str, int) for v in case["result_path"]):
                raise ValueError("invalid call arguments or selectors")
        if name in BYTES_ARGS:
            cases = [_bytes_args(case) for case in cases]
        primary = encode(row["expected"], row["comparator"])
        secondary = encode(mutant["expected"], row["comparator"])
        if primary == secondary:
            raise ValueError("mutant has no teeth")
        if invoke(fn, cases[0], row["comparator"]) != primary or invoke(fn, cases[1], row["comparator"]) != secondary:
            raise ValueError("characterization mismatch: " + row["case_id"])
        covered.add(name)
    if covered != set(textprims.PRIMITIVES):
        raise ValueError("registered primitive lacks characterization: " + ", ".join(sorted(set(textprims.PRIMITIVES) - covered)))
    return len(seen)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=ROOT / "references/textprims/characterization.json")
    parser.add_argument("--punkt-data", required=True, type=Path)
    args = parser.parse_args()
    print(f"{run(args.fixture, args.punkt_data)} primary/mutant rows passed")
