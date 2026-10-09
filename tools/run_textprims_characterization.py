#!/usr/bin/env python3
"""Run the closed synthetic primitive oracle without compatibility side effects."""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/setec-voiceprint/scripts"
TOKENIZER_OWNER = "plugins/setec-voiceprint/scripts/setec/core/passage_tokenizer_v1.py"
# Rows minted in place at an existing owner: owner path -> (module, registered symbols, label).
OWNED = {
    "plugins/setec-voiceprint/scripts/setec/core/verbatim_cover.py": ("setec.core.verbatim_cover", {"_tokens", "_content_fingerprint"}, "verbatim-cover"),
    "plugins/setec-voiceprint/scripts/setec/core/paragraph_parser.py": ("setec.core.paragraph_parser", {"split_paragraphs", "split_sentences"}, "paragraph-parser"),
    "plugins/setec-voiceprint/scripts/setec/preflight/common.py": ("setec.preflight.common", {"_analysis"}, "preflight-analysis"),
}
# Bytes-taking callables: each args element is a bytes_hex_exact object decoded before the call.
HEX_ARGS = {"plugins/setec-voiceprint/scripts/setec/preflight/common.py:_analysis"}
OWNER = "plugins/setec-voiceprint/scripts/setec/core/textprims.py"
FIELDS = {"case_id", "family", "registry_id", "legacy_callable", "registered_callable", "args", "kwargs", "result_path", "comparator", "expected", "mutant"}
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


def _run(fixture):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from setec.core import textprims
    document = json.loads(Path(fixture).read_text(encoding="utf-8"))
    if set(document) != {"schema", "license", "rows"} or document["schema"] != "textprims-characterization/1" or not document["license"]:
        raise ValueError("invalid fixture header")
    registry = {row["id"]: row for name in ("TOKENIZERS", "SENTENCE_SPLITTERS", "PARAGRAPH_SPLITTERS", "FUNCTION_WORD_SETS", "QUANTILES", "FINGERPRINTS", "PREPROCESSORS") for row in getattr(textprims, name).values()}
    seen, covered = set(), set()
    for row in document["rows"]:
        if set(row) != FIELDS or row["case_id"] in seen or row["comparator"] not in COMPARATORS:
            raise ValueError("invalid or duplicate fixture row")
        seen.add(row["case_id"])
        entry = registry[row["registry_id"]]
        table_row = entry["family"] == "function_words"
        frozen_tokenizer = entry["implementation_ref"] == TOKENIZER_OWNER + ":tokenize"
        if frozen_tokenizer:
            from setec.core import passage_tokenizer_v1
            expected_data = SCRIPTS / "passage_tokenizer_data_v1.json"
            if passage_tokenizer_v1.DATA_FILE.resolve() != expected_data.resolve() or expected_data.is_symlink() or hashlib.sha256(expected_data.read_bytes()).hexdigest() != entry["pattern_sha256"]:
                raise ValueError("frozen tokenizer default table mismatch")
            default_path = passage_tokenizer_v1.tokenize.__kwdefaults__.get("data_path")
            if not isinstance(default_path, Path) or default_path.resolve() != expected_data.resolve():
                raise ValueError("frozen tokenizer callable default table mismatch")
            if textprims.tokenize is not passage_tokenizer_v1.tokenize:
                raise ValueError("frozen tokenizer registry identity changed")
        owner_path, _, owned_symbol = entry["implementation_ref"].partition(":")
        owned = owner_path in OWNED and owned_symbol in OWNED[owner_path][1]
        if owned:
            owner_module = importlib.import_module(OWNED[owner_path][0])
            if getattr(textprims, owned_symbol) is not getattr(owner_module, owned_symbol):
                raise ValueError(OWNED[owner_path][2] + " registry identity changed")
        expected_ref = entry["implementation_ref"] + (".__contains__" if table_row else "")
        if row["family"] != entry["family"] or row["registered_callable"] != expected_ref:
            raise ValueError("fixture registry binding mismatch")
        # R1 had already relocated these callables. The legacy oracle names
        # that pre-R2 owner, not a side-effectful compatibility-module import.
        functions = []
        for ref in (row["legacy_callable"], row["registered_callable"]):
            module, symbol = ref.split(":")
            if frozen_tokenizer:
                if module != TOKENIZER_OWNER or symbol != "tokenize":
                    raise ValueError("frozen tokenizer must name its native final owner")
                functions.append(passage_tokenizer_v1.tokenize)
                continue
            if owned:
                if module != owner_path or symbol != owned_symbol:
                    raise ValueError(OWNED[owner_path][2] + " row must name its final owner")
                functions.append(getattr(owner_module, symbol))
                continue
            if module != OWNER:
                raise ValueError("non-owner callable outside this cohort")
            if table_row:
                if not symbol.endswith(".__contains__") or row["comparator"] != "json_exact" or type(row["expected"]) is not bool or type(row["mutant"]["expected"]) is not bool:
                    raise ValueError("closed boolean table membership required")
                table = getattr(textprims, symbol.removesuffix(".__contains__"))
                fn = table.__contains__
                if fn.__self__ is not table:
                    raise ValueError("membership method lost its table identity")
                functions.append(fn)
            else:
                functions.append(getattr(textprims, symbol))
        same_object = functions[0].__self__ is functions[1].__self__ if table_row else functions[0] is functions[1]
        if not same_object:
            raise ValueError("ownership identity changed")
        mutant = row["mutant"]
        if set(mutant) != {"args", "kwargs", "result_path", "expected"}:
            raise ValueError("invalid mutant")
        cases = []
        for case in (row, mutant):
            if type(case["args"]) is not list or type(case["kwargs"]) is not dict or type(case["result_path"]) is not list or any(type(v) not in (str, int) for v in case["result_path"]):
                raise ValueError("invalid call arguments or selectors")
            if frozen_tokenizer and "data_path" in case["kwargs"]:
                raise ValueError("custom frozen tokenizer table is forbidden")
            if table_row and (len(case["args"]) != 1 or type(case["args"][0]) is not str or case["kwargs"] or case["result_path"]):
                raise ValueError("table membership requires one word query")
            if entry["implementation_ref"] in HEX_ARGS:
                case = {**case, "args": [bytes.fromhex(encode(arg, "bytes_hex_exact")["hex"]) for arg in case["args"]]}
            cases.append(case)
        primary = encode(row["expected"], row["comparator"])
        secondary = encode(mutant["expected"], row["comparator"])
        if primary == secondary:
            raise ValueError("mutant has no teeth")
        for fn in functions:
            if invoke(fn, cases[0], row["comparator"]) != primary or invoke(fn, cases[1], row["comparator"]) != secondary:
                raise ValueError("characterization mismatch: " + row["case_id"])
        covered.add(row["registry_id"])
    if covered != set(registry):
        raise ValueError("registry callable lacks characterization")
    return len(seen)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=ROOT / "references/textprims/characterization.json")
    parser.add_argument("--punkt-data", required=True, type=Path)
    args = parser.parse_args()
    print(f"{run(args.fixture, args.punkt_data)} primary/mutant rows passed")
