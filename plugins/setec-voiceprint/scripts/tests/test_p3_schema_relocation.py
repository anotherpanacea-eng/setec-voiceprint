"""Behavioral compatibility for the four P3 schema relocations.

Each scenario runs against a copied plugin in an isolated, stdlib-only child.
The embedded pickles are external compatibility fixtures, not source snapshots.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_MODULES = (
    "argument_feature_schema",
    "narrative_feature_schema",
    "cross_doc_consistency_schema",
    "argument_certainty_calibration_schema",
)
_ORDERS = ("legacy-first", "package-first", "package-only")

# Produced by Python 3.12 pickle.dumps from unchanged Git objects at producer
# base 7537b9f64af8181e1b59250ce66bdfc150d5ac06, with ordinary legacy module
# names. Protocols 0-5 each contain the synthetic graph built in _GRAPH below:
# class objects, records, distinct SchemaErrors, shared references and a cycle.
# Do not regenerate these bytes from a relocated candidate.
_BASELINE_PICKLES = (
    (
        "KGRwMApWY2xhc3NlcwpwMQooY2FyZ3VtZW50X2ZlYXR1cmVfc2NoZW1hCkRlcml2ZWRTaWduYWwKcDIKY25hcnJh"
        "dGl2ZV9mZWF0dXJlX3NjaGVtYQpGZWF0dXJlU2lnbmFsCnAzCmNuYXJyYXRpdmVfZmVhdHVyZV9zY2hlbWEKQ29y"
        "ZUZlYXR1cmUKcDQKY2Nyb3NzX2RvY19jb25zaXN0ZW5jeV9zY2hlbWEKU2NoZW1hRXJyb3IKcDUKY2FyZ3VtZW50"
        "X2NlcnRhaW50eV9jYWxpYnJhdGlvbl9zY2hlbWEKU2NoZW1hRXJyb3IKcDYKdHA3CnNWcmVjb3JkcwpwOAooY2Nv"
        "cHlfcmVnCl9yZWNvbnN0cnVjdG9yCnA5CihnMgpjX19idWlsdGluX18Kb2JqZWN0CnAxMApOdHAxMQpScDEyCihk"
        "cDEzClZrZXkKcDE0ClZzeW50aGV0aWMKcDE1CnNWbGFiZWwKcDE2CmcxNQpzVmJ1bmRsZQpwMTcKVkIxX3N0cnVj"
        "dHVyYWxfYXJjCnAxOApzVmtpbmQKcDE5ClZ0cmFuc2l0aW9uX3JhdGUKcDIwCnNWbGVhbmluZwpwMjEKVmh1bWFu"
        "CnAyMgpzVmFuY2hvcmVkCnAyMwpJMDEKc1ZodW1hbl9tZWFuCnAyNApGMC43NQpzVmFpX21lYW4KcDI1CkYwLjI1"
        "CnNWYW5jaG9yX3JlZ2lzdGVyCnAyNgpnMTUKc1Zub3RlcwpwMjcKVgpwMjgKc1ZjYWxpYnJhdGlvbl9zdGF0dXMK"
        "cDI5ClZsaXRlcmF0dXJlX2FuY2hvcmVkCnAzMApzYmc5CihnMwpnMTAKTnRwMzEKUnAzMgooZHAzMwpWb3B0aW9u"
        "CnAzNApOc2cyMQpnMjIKc2cyNApGMC43NQpzZzI1CkYwLjI1CnNnMTcKZzE1CnNiZzkKKGc0CmcxMApOdHAzNQpS"
        "cDM2CihkcDM3CmcxNApnMTUKc2cxNgpnMTUKc1ZkaW1lbnNpb24KcDM4ClZTSVQKcDM5CnNWZmVhdHVyZV90eXBl"
        "CnA0MApWY2F0ZWdvcmljYWwKcDQxCnNWcXVlc3Rpb24KcDQyCmcxNQpzVmRlc2NyaXB0aW9uCnA0MwpnMjgKc1Zy"
        "ZXNwb25zZV9vcHRpb25zCnA0NAooZzE1CnRwNDUKc1ZzaWduYWxzCnA0NgooZzMyCmc5CihnMwpnMTAKTnRwNDcK"
        "UnA0OAooZHA0OQpnMzQKZzE1CnNnMjEKVmFpCnA1MApzZzI0CkYwLjI1CnNnMjUKRjAuNzUKc2cxNwpnMTUKc2J0"
        "cDUxCnNWcGFwZXJfdGFibGVfcm93CnA1MgpJMQpzYnRwNTMKc1ZlcnJvcnMKcDU0CihnNQooZzE1CnRwNTUKUnA1"
        "NgooZHA1NwpWcmVjb3JkCnA1OApnMzYKc2JnNgooZzE1CnRwNTkKUnA2MAooZHA2MQpnNTgKZzEyCnNidHA2Mgpz"
        "Vm5lc3RlZApwNjMKKGxwNjQKZzM2CmFnMzIKYShkcDY1CmczNgpnMTUKc2FzVnNlbGYKcDY2CmcwCnMu"
    ),
    (
        "fXEAKFgHAAAAY2xhc3Nlc3EBKGNhcmd1bWVudF9mZWF0dXJlX3NjaGVtYQpEZXJpdmVkU2lnbmFsCnECY25hcnJh"
        "dGl2ZV9mZWF0dXJlX3NjaGVtYQpGZWF0dXJlU2lnbmFsCnEDY25hcnJhdGl2ZV9mZWF0dXJlX3NjaGVtYQpDb3Jl"
        "RmVhdHVyZQpxBGNjcm9zc19kb2NfY29uc2lzdGVuY3lfc2NoZW1hClNjaGVtYUVycm9yCnEFY2FyZ3VtZW50X2Nl"
        "cnRhaW50eV9jYWxpYnJhdGlvbl9zY2hlbWEKU2NoZW1hRXJyb3IKcQZ0cQdYBwAAAHJlY29yZHNxCChjY29weV9y"
        "ZWcKX3JlY29uc3RydWN0b3IKcQkoaAJjX19idWlsdGluX18Kb2JqZWN0CnEKTnRxC1JxDH1xDShYAwAAAGtleXEO"
        "WAkAAABzeW50aGV0aWNxD1gFAAAAbGFiZWxxEGgPWAYAAABidW5kbGVxEVgRAAAAQjFfc3RydWN0dXJhbF9hcmNx"
        "ElgEAAAAa2luZHETWA8AAAB0cmFuc2l0aW9uX3JhdGVxFFgHAAAAbGVhbmluZ3EVWAUAAABodW1hbnEWWAgAAABh"
        "bmNob3JlZHEXSTAxClgKAAAAaHVtYW5fbWVhbnEYRz/oAAAAAAAAWAcAAABhaV9tZWFucRlHP9AAAAAAAABYDwAA"
        "AGFuY2hvcl9yZWdpc3RlcnEaaA9YBQAAAG5vdGVzcRtYAAAAAHEcWBIAAABjYWxpYnJhdGlvbl9zdGF0dXNxHVgT"
        "AAAAbGl0ZXJhdHVyZV9hbmNob3JlZHEedWJoCShoA2gKTnRxH1JxIH1xIShYBgAAAG9wdGlvbnEiTmgVaBZoGEc/"
        "6AAAAAAAAGgZRz/QAAAAAAAAaBFoD3ViaAkoaARoCk50cSNScSR9cSUoaA5oD2gQaA9YCQAAAGRpbWVuc2lvbnEm"
        "WAMAAABTSVRxJ1gMAAAAZmVhdHVyZV90eXBlcShYCwAAAGNhdGVnb3JpY2FscSlYCAAAAHF1ZXN0aW9ucSpoD1gL"
        "AAAAZGVzY3JpcHRpb25xK2gcWBAAAAByZXNwb25zZV9vcHRpb25zcSwoaA90cS1YBwAAAHNpZ25hbHNxLihoIGgJ"
        "KGgDaApOdHEvUnEwfXExKGgiaA9oFVgCAAAAYWlxMmgYRz/QAAAAAAAAaBlHP+gAAAAAAABoEWgPdWJ0cTNYDwAA"
        "AHBhcGVyX3RhYmxlX3Jvd3E0SwF1YnRxNVgGAAAAZXJyb3JzcTYoaAUoaA90cTdScTh9cTlYBgAAAHJlY29yZHE6"
        "aCRzYmgGKGgPdHE7UnE8fXE9aDpoDHNidHE+WAYAAABuZXN0ZWRxP11xQChoJGggfXFBaCRoD3NlWAQAAABzZWxm"
        "cUJoAHUu"
    ),
    (
        "gAJ9cQAoWAcAAABjbGFzc2VzcQEoY2FyZ3VtZW50X2ZlYXR1cmVfc2NoZW1hCkRlcml2ZWRTaWduYWwKcQJjbmFy"
        "cmF0aXZlX2ZlYXR1cmVfc2NoZW1hCkZlYXR1cmVTaWduYWwKcQNjbmFycmF0aXZlX2ZlYXR1cmVfc2NoZW1hCkNv"
        "cmVGZWF0dXJlCnEEY2Nyb3NzX2RvY19jb25zaXN0ZW5jeV9zY2hlbWEKU2NoZW1hRXJyb3IKcQVjYXJndW1lbnRf"
        "Y2VydGFpbnR5X2NhbGlicmF0aW9uX3NjaGVtYQpTY2hlbWFFcnJvcgpxBnRxB1gHAAAAcmVjb3Jkc3EIaAIpgXEJ"
        "fXEKKFgDAAAAa2V5cQtYCQAAAHN5bnRoZXRpY3EMWAUAAABsYWJlbHENaAxYBgAAAGJ1bmRsZXEOWBEAAABCMV9z"
        "dHJ1Y3R1cmFsX2FyY3EPWAQAAABraW5kcRBYDwAAAHRyYW5zaXRpb25fcmF0ZXERWAcAAABsZWFuaW5ncRJYBQAA"
        "AGh1bWFucRNYCAAAAGFuY2hvcmVkcRSIWAoAAABodW1hbl9tZWFucRVHP+gAAAAAAABYBwAAAGFpX21lYW5xFkc/"
        "0AAAAAAAAFgPAAAAYW5jaG9yX3JlZ2lzdGVycRdoDFgFAAAAbm90ZXNxGFgAAAAAcRlYEgAAAGNhbGlicmF0aW9u"
        "X3N0YXR1c3EaWBMAAABsaXRlcmF0dXJlX2FuY2hvcmVkcRt1YmgDKYFxHH1xHShYBgAAAG9wdGlvbnEeTmgSaBNo"
        "FUc/6AAAAAAAAGgWRz/QAAAAAAAAaA5oDHViaAQpgXEffXEgKGgLaAxoDWgMWAkAAABkaW1lbnNpb25xIVgDAAAA"
        "U0lUcSJYDAAAAGZlYXR1cmVfdHlwZXEjWAsAAABjYXRlZ29yaWNhbHEkWAgAAABxdWVzdGlvbnElaAxYCwAAAGRl"
        "c2NyaXB0aW9ucSZoGVgQAAAAcmVzcG9uc2Vfb3B0aW9uc3EnaAyFcShYBwAAAHNpZ25hbHNxKWgcaAMpgXEqfXEr"
        "KGgeaAxoElgCAAAAYWlxLGgVRz/QAAAAAAAAaBZHP+gAAAAAAABoDmgMdWKGcS1YDwAAAHBhcGVyX3RhYmxlX3Jv"
        "d3EuSwF1YodxL1gGAAAAZXJyb3JzcTBoBWgMhXExUnEyfXEzWAYAAAByZWNvcmRxNGgfc2JoBmgMhXE1UnE2fXE3"
        "aDRoCXNihnE4WAYAAABuZXN0ZWRxOV1xOihoH2gcfXE7aB9oDHNlWAQAAABzZWxmcTxoAHUu"
    ),
    (
        "gAN9cQAoWAcAAABjbGFzc2VzcQEoY2FyZ3VtZW50X2ZlYXR1cmVfc2NoZW1hCkRlcml2ZWRTaWduYWwKcQJjbmFy"
        "cmF0aXZlX2ZlYXR1cmVfc2NoZW1hCkZlYXR1cmVTaWduYWwKcQNjbmFycmF0aXZlX2ZlYXR1cmVfc2NoZW1hCkNv"
        "cmVGZWF0dXJlCnEEY2Nyb3NzX2RvY19jb25zaXN0ZW5jeV9zY2hlbWEKU2NoZW1hRXJyb3IKcQVjYXJndW1lbnRf"
        "Y2VydGFpbnR5X2NhbGlicmF0aW9uX3NjaGVtYQpTY2hlbWFFcnJvcgpxBnRxB1gHAAAAcmVjb3Jkc3EIaAIpgXEJ"
        "fXEKKFgDAAAAa2V5cQtYCQAAAHN5bnRoZXRpY3EMWAUAAABsYWJlbHENaAxYBgAAAGJ1bmRsZXEOWBEAAABCMV9z"
        "dHJ1Y3R1cmFsX2FyY3EPWAQAAABraW5kcRBYDwAAAHRyYW5zaXRpb25fcmF0ZXERWAcAAABsZWFuaW5ncRJYBQAA"
        "AGh1bWFucRNYCAAAAGFuY2hvcmVkcRSIWAoAAABodW1hbl9tZWFucRVHP+gAAAAAAABYBwAAAGFpX21lYW5xFkc/"
        "0AAAAAAAAFgPAAAAYW5jaG9yX3JlZ2lzdGVycRdoDFgFAAAAbm90ZXNxGFgAAAAAcRlYEgAAAGNhbGlicmF0aW9u"
        "X3N0YXR1c3EaWBMAAABsaXRlcmF0dXJlX2FuY2hvcmVkcRt1YmgDKYFxHH1xHShYBgAAAG9wdGlvbnEeTmgSaBNo"
        "FUc/6AAAAAAAAGgWRz/QAAAAAAAAaA5oDHViaAQpgXEffXEgKGgLaAxoDWgMWAkAAABkaW1lbnNpb25xIVgDAAAA"
        "U0lUcSJYDAAAAGZlYXR1cmVfdHlwZXEjWAsAAABjYXRlZ29yaWNhbHEkWAgAAABxdWVzdGlvbnElaAxYCwAAAGRl"
        "c2NyaXB0aW9ucSZoGVgQAAAAcmVzcG9uc2Vfb3B0aW9uc3EnaAyFcShYBwAAAHNpZ25hbHNxKWgcaAMpgXEqfXEr"
        "KGgeaAxoElgCAAAAYWlxLGgVRz/QAAAAAAAAaBZHP+gAAAAAAABoDmgMdWKGcS1YDwAAAHBhcGVyX3RhYmxlX3Jv"
        "d3EuSwF1YodxL1gGAAAAZXJyb3JzcTBoBWgMhXExUnEyfXEzWAYAAAByZWNvcmRxNGgfc2JoBmgMhXE1UnE2fXE3"
        "aDRoCXNihnE4WAYAAABuZXN0ZWRxOV1xOihoH2gcfXE7aB9oDHNlWAQAAABzZWxmcTxoAHUu"
    ),
    (
        "gASVHAMAAAAAAAB9lCiMB2NsYXNzZXOUKIwXYXJndW1lbnRfZmVhdHVyZV9zY2hlbWGUjA1EZXJpdmVkU2lnbmFs"
        "lJOUjBhuYXJyYXRpdmVfZmVhdHVyZV9zY2hlbWGUjA1GZWF0dXJlU2lnbmFslJOUaAWMC0NvcmVGZWF0dXJllJOU"
        "jBxjcm9zc19kb2NfY29uc2lzdGVuY3lfc2NoZW1hlIwLU2NoZW1hRXJyb3KUk5SMJWFyZ3VtZW50X2NlcnRhaW50"
        "eV9jYWxpYnJhdGlvbl9zY2hlbWGUaAuTlHSUjAdyZWNvcmRzlGgEKYGUfZQojANrZXmUjAlzeW50aGV0aWOUjAVs"
        "YWJlbJRoFIwGYnVuZGxllIwRQjFfc3RydWN0dXJhbF9hcmOUjARraW5klIwPdHJhbnNpdGlvbl9yYXRllIwHbGVh"
        "bmluZ5SMBWh1bWFulIwIYW5jaG9yZWSUiIwKaHVtYW5fbWVhbpRHP+gAAAAAAACMB2FpX21lYW6URz/QAAAAAAAA"
        "jA9hbmNob3JfcmVnaXN0ZXKUaBSMBW5vdGVzlIwAlIwSY2FsaWJyYXRpb25fc3RhdHVzlIwTbGl0ZXJhdHVyZV9h"
        "bmNob3JlZJR1YmgHKYGUfZQojAZvcHRpb26UTmgaaBtoHUc/6AAAAAAAAGgeRz/QAAAAAAAAaBZoFHViaAkpgZR9"
        "lChoE2gUaBVoFIwJZGltZW5zaW9ulIwDU0lUlIwMZmVhdHVyZV90eXBllIwLY2F0ZWdvcmljYWyUjAhxdWVzdGlv"
        "bpRoFIwLZGVzY3JpcHRpb26UaCGMEHJlc3BvbnNlX29wdGlvbnOUaBSFlIwHc2lnbmFsc5RoJGgHKYGUfZQoaCZo"
        "FGgajAJhaZRoHUc/0AAAAAAAAGgeRz/oAAAAAAAAaBZoFHVihpSMD3BhcGVyX3RhYmxlX3Jvd5RLAXVih5SMBmVy"
        "cm9yc5RoDGgUhZRSlH2UjAZyZWNvcmSUaCdzYmgOaBSFlFKUfZRoPGgRc2KGlIwGbmVzdGVklF2UKGgnaCR9lGgn"
        "aBRzZYwEc2VsZpRoAHUu"
    ),
    (
        "gAWVHAMAAAAAAAB9lCiMB2NsYXNzZXOUKIwXYXJndW1lbnRfZmVhdHVyZV9zY2hlbWGUjA1EZXJpdmVkU2lnbmFs"
        "lJOUjBhuYXJyYXRpdmVfZmVhdHVyZV9zY2hlbWGUjA1GZWF0dXJlU2lnbmFslJOUaAWMC0NvcmVGZWF0dXJllJOU"
        "jBxjcm9zc19kb2NfY29uc2lzdGVuY3lfc2NoZW1hlIwLU2NoZW1hRXJyb3KUk5SMJWFyZ3VtZW50X2NlcnRhaW50"
        "eV9jYWxpYnJhdGlvbl9zY2hlbWGUaAuTlHSUjAdyZWNvcmRzlGgEKYGUfZQojANrZXmUjAlzeW50aGV0aWOUjAVs"
        "YWJlbJRoFIwGYnVuZGxllIwRQjFfc3RydWN0dXJhbF9hcmOUjARraW5klIwPdHJhbnNpdGlvbl9yYXRllIwHbGVh"
        "bmluZ5SMBWh1bWFulIwIYW5jaG9yZWSUiIwKaHVtYW5fbWVhbpRHP+gAAAAAAACMB2FpX21lYW6URz/QAAAAAAAA"
        "jA9hbmNob3JfcmVnaXN0ZXKUaBSMBW5vdGVzlIwAlIwSY2FsaWJyYXRpb25fc3RhdHVzlIwTbGl0ZXJhdHVyZV9h"
        "bmNob3JlZJR1YmgHKYGUfZQojAZvcHRpb26UTmgaaBtoHUc/6AAAAAAAAGgeRz/QAAAAAAAAaBZoFHViaAkpgZR9"
        "lChoE2gUaBVoFIwJZGltZW5zaW9ulIwDU0lUlIwMZmVhdHVyZV90eXBllIwLY2F0ZWdvcmljYWyUjAhxdWVzdGlv"
        "bpRoFIwLZGVzY3JpcHRpb26UaCGMEHJlc3BvbnNlX29wdGlvbnOUaBSFlIwHc2lnbmFsc5RoJGgHKYGUfZQoaCZo"
        "FGgajAJhaZRoHUc/0AAAAAAAAGgeRz/oAAAAAAAAaBZoFHVihpSMD3BhcGVyX3RhYmxlX3Jvd5RLAXVih5SMBmVy"
        "cm9yc5RoDGgUhZRSlH2UjAZyZWNvcmSUaCdzYmgOaBSFlFKUfZRoPGgRc2KGlIwGbmVzdGVklF2UKGgnaCR9lGgn"
        "aBRzZYwEc2VsZpRoAHUu"
    ),
)


@pytest.fixture(scope="module")
def copied_plugin(tmp_path_factory):
    parent = tmp_path_factory.mktemp("schema-relocation")
    root = parent / "plugin"
    shutil.copytree(
        _PLUGIN_ROOT, root, ignore=shutil.ignore_patterns("__pycache__", "tests")
    )
    (parent / "foreign-cwd").mkdir()
    return root


def _run(root, *args):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", *map(str, args)],
        cwd=root.parent / "foreign-cwd",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), (
        result.stdout + result.stderr
    )


def _imports(root, order):
    return f"""
import importlib
import sys
sys.path.insert(0, {str(root / 'scripts')!r})
modules = []
for name in {_MODULES!r}:
    paths = {{
        'legacy-first': (name, 'setec.core.' + name),
        'package-first': ('setec.core.' + name, name),
        'package-only': ('setec.core.' + name,),
    }}[{order!r}]
    for path in paths:
        importlib.import_module(path)
    modules.append(sys.modules['setec.core.' + name])
a, n, x, c = modules
"""


_GRAPH = """
from dataclasses import FrozenInstanceError, asdict, astuple, fields, is_dataclass, replace

def make_graph():
    derived = a.DerivedSignal(
        'synthetic', 'synthetic', 'B1_structural_arc', 'transition_rate',
        'human', True, .75, .25, 'synthetic', '',
    )
    signal = n.FeatureSignal(None, 'human', .75, .25, 'synthetic')
    other = n.FeatureSignal('synthetic', 'ai', .25, .75, 'synthetic')
    feature = n.CoreFeature(
        'synthetic', 'synthetic', 'SIT', 'categorical', 'synthetic', '',
        ('synthetic',), (signal, other), 1,
    )
    errors = (x.SchemaError('synthetic'), c.SchemaError('synthetic'))
    errors[0].record = feature
    errors[1].record = derived
    graph = {
        'classes': (a.DerivedSignal, n.FeatureSignal, n.CoreFeature,
                    x.SchemaError, c.SchemaError),
        'records': (derived, signal, feature),
        'errors': errors,
        'nested': [feature, signal, {feature: 'synthetic'}],
    }
    graph['self'] = graph
    return graph

def check_graph(graph):
    expected = make_graph()
    assert graph['classes'] == expected['classes']
    assert graph['self'] is graph
    derived, signal, feature = graph['records']
    assert derived.calibration_status == 'literature_anchored'
    calibration = next(f for f in fields(a.DerivedSignal)
                       if f.name == 'calibration_status')
    assert calibration.default == 'literature_anchored'
    assert derived.gap == signal.gap == .5
    assert replace(derived, human_mean=None, ai_mean=None, anchored=False).gap is None
    assert feature.is_dual_leaning
    assert not replace(feature, signals=(signal,)).is_dual_leaning
    assert feature.signals[0] is signal
    assert graph['nested'][0] is feature
    assert graph['nested'][1] is signal
    assert next(iter(graph['nested'][2])) is feature
    assert graph['nested'][2][expected['records'][2]] == 'synthetic'
    assert astuple(feature) == (
        'synthetic', 'synthetic', 'SIT', 'categorical', 'synthetic', '',
        ('synthetic',),
        ((None, 'human', .75, .25, 'synthetic'),
         ('synthetic', 'ai', .25, .75, 'synthetic')), 1,
    )
    assert asdict(feature)['signals'][0] == {
        'option': None, 'leaning': 'human', 'human_mean': .75,
        'ai_mean': .25, 'bundle': 'synthetic',
    }
    for value, equivalent, cls in zip(
        graph['records'], expected['records'], graph['classes'][:3]
    ):
        assert type(value) is cls
        assert is_dataclass(cls) and is_dataclass(value)
        assert value == equivalent and hash(value) == hash(equivalent)
        assert {value: 'stored'}[equivalent] == 'stored'
        field = fields(cls)[0].name
        changed = replace(value, **{field: 'changed'})
        assert type(changed) is cls and changed != value
        assert getattr(value, field) == getattr(equivalent, field)
        assert asdict(value) == asdict(equivalent)
        for operation in (
            lambda: setattr(value, field, 'changed'),
            lambda: delattr(value, field),
        ):
            try:
                operation()
            except FrozenInstanceError:
                pass
            else:
                raise AssertionError('record lost frozen behavior')
    assert x.SchemaError is not c.SchemaError
    for error, cls in zip(graph['errors'], graph['classes'][3:]):
        assert type(error) is cls and isinstance(error, ValueError)
        assert error.args == ('synthetic',) and str(error) == 'synthetic'
        try:
            raise error
        except cls as caught:
            assert caught is error
    assert graph['errors'][0].record is feature
    assert graph['errors'][1].record is derived
"""


@pytest.mark.parametrize("order", _ORDERS[:2])
def test_ordinary_imports_share_classes_registries_and_function_globals(copied_plugin, order):
    code = _imports(copied_plugin, order) + _GRAPH + """
registries = ('DERIVED_SIGNALS', 'CORE_FEATURES',
              'RELATION_DESCRIPTIONS', 'CERTAINTY_OPTIONS')
class_names = (('DerivedSignal',), ('FeatureSignal', 'CoreFeature'),
               ('SchemaError',), ('SchemaError',))
for name, package, registry, names in zip(
    """ + repr(_MODULES) + """, modules, registries, class_names
):
    legacy = sys.modules[name]
    assert legacy is package
    assert getattr(legacy, registry) is getattr(package, registry)
    for cls_name in names:
        assert getattr(legacy, cls_name) is getattr(package, cls_name)
        assert getattr(package, cls_name).__module__ == name
check_graph(make_graph())

# Rebinding through either path must affect functions invoked through the
# other path, including iterator globals and validator whitelist globals.
la, ln, lx, lc = (sys.modules[name] for name in """ + repr(_MODULES) + """)
derived, signal, feature = make_graph()['records']
saved = a.DERIVED_SIGNALS
la.DERIVED_SIGNALS = (derived,)
assert list(a.iter_anchored_signals()) == [derived]
assert next(a.iter_anchored_signals()) is derived
a.DERIVED_SIGNALS = (replace(derived, anchored=False),)
assert list(la.iter_anchored_signals()) == []
a.DERIVED_SIGNALS = saved
assert la.DERIVED_SIGNALS is saved

saved = n.CORE_FEATURES
ln.CORE_FEATURES = (feature,)
assert list(n.iter_signals()) == [
    (feature, 0, signal), (feature, 1, feature.signals[1]),
]
assert next(n.iter_signals())[0] is feature
n.CORE_FEATURES = ()
assert list(ln.iter_signals()) == []
n.CORE_FEATURES = saved
assert ln.CORE_FEATURES is saved

tension = {
    'topic_ref': 'synthetic',
    'relation': 'tension', 'legitimate_variation': 'genuine',
    'severity': 'patched-legacy', 'rationale': 'synthetic',
    'resolution_class': 'synthetic',
    'loci': [
        {'doc': 'one', 'start_char': 0, 'end_char': 1, 'quote': 'x'},
        {'doc': 'two', 'start_char': 0, 'end_char': 1, 'quote': 'x'},
    ],
}
saved = x.SEVERITY_OPTIONS
lx.SEVERITY_OPTIONS = ('patched-legacy',)
assert x.validate_tension_row(tension) is None
x.SEVERITY_OPTIONS = ('patched-package',)
tension['severity'] = 'patched-package'
assert lx.validate_tension_row(tension) is None
x.SEVERITY_OPTIONS = saved
try:
    lx.validate_tension_row(tension)
except x.SchemaError:
    pass
else:
    raise AssertionError('validator did not observe restored whitelist')

claim = {
    'loci': {'start_char': 0, 'end_char': 1, 'quote': 'x'},
    'certainty': 'patched-legacy', 'support': 'none', 'alignment': 'aligned',
    'defense': 'none', 'rationale': '', 'resolution_class': 'none',
}
saved = c.CERTAINTY_OPTIONS
lc.CERTAINTY_OPTIONS = ('patched-legacy',)
assert c.validate_claim_row(claim) is None
c.CERTAINTY_OPTIONS = ('patched-package',)
claim['certainty'] = 'patched-package'
assert lc.validate_claim_row(claim) is None
c.CERTAINTY_OPTIONS = saved
try:
    lc.validate_claim_row(claim)
except c.SchemaError:
    pass
else:
    raise AssertionError('validator did not observe restored whitelist')
"""
    _run(copied_plugin, "-c", code)


def test_package_only_class_constructor_property_and_nested_type_hints(copied_plugin):
    code = _imports(copied_plugin, "package-only") + """
from typing import get_args, get_type_hints
for cls in (a.DerivedSignal, n.FeatureSignal, n.CoreFeature):
    hints = get_type_hints(cls)
    constructor = get_type_hints(cls.__init__)
    assert constructor.pop('return') is type(None)
    assert constructor == hints
assert get_args(get_type_hints(a.DerivedSignal)['leaning']) == ('ai', 'human')
assert get_args(get_type_hints(n.FeatureSignal)['leaning']) == ('ai', 'human')
assert get_type_hints(n.CoreFeature)['signals'] == tuple[n.FeatureSignal, ...]
assert get_type_hints(n.CoreFeature.__init__)['signals'] == tuple[n.FeatureSignal, ...]
assert get_type_hints(a.DerivedSignal.gap.fget) == {'return': float | None}
assert get_type_hints(n.FeatureSignal.gap.fget) == {'return': float}
assert get_type_hints(n.CoreFeature.is_dual_leaning.fget) == {'return': bool}
"""
    _run(copied_plugin, "-c", code)


@pytest.mark.parametrize("protocol", range(6))
@pytest.mark.parametrize("order", _ORDERS)
def test_baseline_synthetic_graph_loads_in_fresh_process(copied_plugin, protocol, order):
    code = _imports(copied_plugin, order) + _GRAPH + f"""
import base64
import pickle
check_graph(pickle.loads(base64.b64decode({_BASELINE_PICKLES[protocol]!r})))
"""
    _run(copied_plugin, "-c", code)


@pytest.mark.parametrize("protocol", range(6))
def test_candidate_pickles_keep_legacy_globals_from_package_only_import(copied_plugin, protocol):
    code = _imports(copied_plugin, "package-only") + _GRAPH + f"""
import io
import pickle

references = []
class RecordingUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        references.append((module, name))
        return super().find_class(module, name)

original = make_graph()
payload = pickle.dumps(original, protocol={protocol})
restored = RecordingUnpickler(io.BytesIO(payload)).load()
check_graph(restored)
expected_globals = {{
    ('argument_feature_schema', 'DerivedSignal'),
    ('narrative_feature_schema', 'FeatureSignal'),
    ('narrative_feature_schema', 'CoreFeature'),
    ('cross_doc_consistency_schema', 'SchemaError'),
    ('argument_certainty_calibration_schema', 'SchemaError'),
}}
assert expected_globals.issubset(references), references
assert not any(module.startswith('setec.') for module, name in references), references
for name, package in zip({_MODULES!r}, modules):
    assert sys.modules[name] is package
"""
    _run(copied_plugin, "-c", code)


@pytest.mark.parametrize("name", _MODULES)
@pytest.mark.parametrize("execution", ("direct", "runpy"))
def test_copied_launchers_are_silent_and_keep_main(copied_plugin, name, execution):
    launcher = copied_plugin / "scripts" / (name + ".py")
    if execution == "direct":
        _run(copied_plugin, launcher)
    else:
        # No scripts-root insertion: the detached launcher's bootstrap is
        # responsible for resolving the copied package from a foreign cwd.
        _run(copied_plugin, "-c", f"""
import runpy
import sys
main = sys.modules['__main__']
observed = False
def check_main(frame, event, arg):
    global observed
    if event == 'return' and frame.f_code.co_filename == {str(launcher)!r}:
        # Observe the launcher before runpy restores its temporary __main__.
        assert sys.modules['__main__'].__dict__ is frame.f_globals
        observed = True
sys.setprofile(check_main)
try:
    runpy.run_path({str(launcher)!r}, run_name='__main__')
finally:
    sys.setprofile(None)
assert observed
assert sys.modules['__main__'] is main
""")


@pytest.mark.parametrize(
    "name,taxonomy",
    (
        ("argument_feature_schema", "ROLE_DESCRIPTIONS"),
        ("narrative_feature_schema", "DIMENSION_LABELS"),
    ),
)
@pytest.mark.parametrize("entry", ("legacy", "package"))
def test_failed_selfcheck_publishes_no_alias_and_clean_retry_works(
    copied_plugin, name, taxonomy, entry
):
    # Trace execution only to corrupt an existing public mutable taxonomy as
    # it becomes available. No source rewriting, line-number assumptions,
    # patched self-check, or production-only testing seam is needed.
    code = f"""
import importlib
import sys
sys.path.insert(0, {str(copied_plugin / 'scripts')!r})
legacy_name = {name!r}
package_name = 'setec.core.' + legacy_name
entry = legacy_name if {entry!r} == 'legacy' else package_name
injected = False

def corrupt_taxonomy(frame, event, arg):
    global injected
    if event == 'line' and frame.f_globals.get('__name__') == package_name:
        taxonomy = frame.f_globals.get({taxonomy!r})
        if isinstance(taxonomy, dict) and taxonomy and not injected:
            taxonomy.clear()
            injected = True
    return corrupt_taxonomy

sys.settrace(corrupt_taxonomy)
try:
    importlib.import_module(entry)
except RuntimeError as error:
    assert injected
    assert {'ROLE_DESCRIPTIONS' if name == 'argument_feature_schema' else 'unknown dimension'!r} in str(error)
else:
    raise AssertionError('invalid taxonomy did not fail import-time self-check')
finally:
    sys.settrace(None)
assert legacy_name not in sys.modules
assert package_name not in sys.modules

retry = importlib.import_module(entry)
assert getattr(retry, {taxonomy!r})
assert importlib.import_module(package_name) is retry
assert importlib.import_module(legacy_name) is retry
from typing import get_type_hints
cls = retry.{'DerivedSignal' if name == 'argument_feature_schema' else 'CoreFeature'}
assert get_type_hints(cls)
"""
    _run(copied_plugin, "-c", code)
