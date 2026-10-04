# P3 subprocess probe: prospective A1/A4 preparation

Settled base: `ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe`. Fleet issue76. This preparation changes only this document; no helper, scenario, fixture, runtime or workflow edit has begun. It grants no implementation clearance.

Fresh pre-change collection and execution:63 cases passed with zero skips/errors/failures on Python3.13 on Windows, HEAD fixed at the settled base throughout. Local collection/JUnit records retained privately. The three module totals are baseline13, judge25, support25. Windows success does not establish POSIX branch execution or hosted clearance.

## Proposed boundary

The three top-level `_probe(code, scripts, cwd, *args)` bodies are AST-identical (seven source lines). Only that runner body is eligible to be shared in the existing conftest plain-helper module. Keep local wrappers and signatures; supply each module's current subprocess.run and sys.executable at call time. Preserve argument order `[executable, "-I", "-S", "-B", "-c", code, str(scripts), *map(str, args)]`, cwd, text=True, capture_output=True, timeout=30, implicit None return, stderr as the nonzero-return assertion message, and rejection of either nonempty stdout or stderr. No definition-time binding or common package runner is authorized.

The separate packaging-argument probes remain local: they return CompletedProcess, omit -I, and provide their own environment policy. The proposed cohort's module tables, copied-plugin fixtures, parameter matrices, aliases/pickle contracts, private API patches, no-model/no-provider controls and Windows/POSIX direct-execution branches are all local variants and remain byte-for-byte scenario material. No normalized surface/envelope/claim-license assertion is collapsed, and no new contract expectation is invented. Existing normalized fixture bytes remain authoritative and untouched.

## Selection and mapping

Existing Linux job selects the full scripts/tests directory; the six focused-platform selectors do not name these modules. Keep all workflows and selectors unchanged. This observation is not hosted clearance or A3 completion.

Every original node listed below must map to exactly the same node ID and preserve the complete linked function, decorators, fixture inputs and embedded subprocess code. Any later helper-only build must compare collection and outcomes, retain every divergent scenario, and separately list new behavioral cases. Counts alone cannot prove preservation. There is no test rename, source-shape enforcement test, fixture generator, new gate or hashing framework.

## baseline family

Exact collected cases:

- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_import_identity_and_shared_patches[False-concreteness]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_import_identity_and_shared_patches[False-argument_register_baselines]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_import_identity_and_shared_patches[False-register_typical_baselines]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_import_identity_and_shared_patches[False-register_taxonomy]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_import_identity_and_shared_patches[True-concreteness]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_import_identity_and_shared_patches[True-argument_register_baselines]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_import_identity_and_shared_patches[True-register_typical_baselines]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_import_identity_and_shared_patches[True-register_taxonomy]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_existing_class_pickle_contract[False]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_existing_class_pickle_contract[True]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_copied_plugin_and_stay_put_assets[direct]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_copied_plugin_and_stay_put_assets[runpy]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py::test_copied_plugin_and_stay_put_assets[package]`

### `test_import_identity_and_shared_patches`

[Complete base function, decorators and scenarios](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py#L27)

Outer assertions (complete scenario link also preserves helper calls, branch conditions and inputs):

```python
# Observable assertions are in the subprocess payloads below.
```

Complete embedded Python scenario 1 (preserve its assertions, control flow and expected values):

```python
import importlib, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
name = sys.argv[2]
paths = [name, 'setec.core.' + name]
if sys.argv[3] == 'True': paths.reverse()
a, b = [importlib.import_module(p) for p in paths]
assert a is b
if name == 'concreteness':
    original = a.CONC_SCALE_MAX
    a.CONC_SCALE_MAX = 2
    assert not b.is_valid_rating(3)
    b.CONC_SCALE_MAX = original
    assert a.is_valid_rating(3)
elif name == 'argument_register_baselines':
    original = a.ENV_VAR
    root = Path.cwd()
    (root / a.YAML_NAME).touch()
    os.environ['SYNTHETIC_BASELINES'] = str(root)
    a.ENV_VAR = 'SYNTHETIC_BASELINES'
    assert b.resolve_yaml_path() == root / a.YAML_NAME
    b.ENV_VAR = original
    assert a.ENV_VAR == original
elif name == 'register_typical_baselines':
    original = a.get_baseline_mean
    a.get_baseline_mean = lambda *args, **kwargs: 0.4
    assert b.resolve_baseline('synthetic', 'signal')['value'] == 0.4
    b.get_baseline_mean = original
    assert a.get_baseline_mean is original
else:
    original = a.REGISTER_TO_TIER
    a.REGISTER_TO_TIER = {'synthetic': 'private_dyadic'}
    assert b.resolve_register_tier('synthetic') == 'private_dyadic'
    b.REGISTER_TO_TIER = original
    assert a.resolve_register_tier('message.imessage') == 'private_dyadic'
```

### `test_existing_class_pickle_contract`

[Complete base function, decorators and scenarios](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py#L68)

Outer assertions (complete scenario link also preserves helper calls, branch conditions and inputs):

```python
# Observable assertions are in the subprocess payloads below.
```

Complete embedded Python scenario 1 (preserve its assertions, control flow and expected values):

```python
import importlib, pickle, sys
sys.path.insert(0, sys.argv[1])
prefix = 'setec.core.' if sys.argv[2] == 'True' else ''
a = importlib.import_module(prefix + 'argument_register_baselines')
r = importlib.import_module(prefix + 'register_typical_baselines')
values = [a.SignalBaseline('s', 0.2, None, 'heuristic', None, True),
          a.RegisterBaseline('synthetic', {}, None, 'synthetic.yaml'),
          a.RegisterBaselineError('synthetic'), r.RegisterTypicalBaselineError('synthetic')]
for value in values:
    restored = pickle.loads(pickle.dumps(value))
    assert type(restored) is type(value)
    assert restored.__dict__ == value.__dict__
    assert str(restored) == str(value)
    assert b'setec.core.' not in pickle.dumps(value)
```

### `test_copied_plugin_and_stay_put_assets`

[Complete base function, decorators and scenarios](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_p3_baseline_family_relocation.py#L88)

Outer assertions (complete scenario link also preserves helper calls, branch conditions and inputs):

```python
assert result.returncode == 0, result.stderr
assert result.stdout == result.stderr == ''
```

Complete embedded Python scenario 1 (preserve its assertions, control flow and expected values):

```python
import runpy,sys; old=sys.modules['__main__']; runpy.run_path(sys.argv[2],run_name='__main__'); assert sys.modules['__main__'] is old
```

Complete embedded Python scenario 2 (preserve its assertions, control flow and expected values):

```python
import importlib, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
name = sys.argv[2]
m = importlib.import_module('setec.core.' + name)
assert m is importlib.import_module(name)
root = Path(sys.argv[1]).parent
if name == 'concreteness':
    assert m._DEFAULT_DATA_PATH == root / 'data' / 'brysbaert_concreteness.csv'
elif name == 'register_taxonomy':
    assert m.REGISTRY_PATH == root / 'register_tiers.d'
    assert m.resolve_register_tier('message.imessage') == 'private_dyadic'
else:
    assert m._DEFAULT_YAML_PATH == root.parents[1] / 'baselines' / (
        'argument_register_baselines.yaml' if name == 'argument_register_baselines'
        else 'register_typical.yaml')
```

## judge family

Exact collected cases:

- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-agd_move_scan_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-argquality_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-argument_certainty_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-argument_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-cross_doc_consistency_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-fallacy_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-judge_backends]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-narrative_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-position_pair_register_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-warrant_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-agd_move_scan_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-argquality_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-argument_certainty_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-argument_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-cross_doc_consistency_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-fallacy_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-judge_backends]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-narrative_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-position_pair_register_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-warrant_judge]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_existing_class_pickle_contract[False]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_existing_class_pickle_contract[True]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_copied_plugin_from_foreign_cwd[direct]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_copied_plugin_from_foreign_cwd[runpy]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py::test_copied_plugin_from_foreign_cwd[package]`

### `test_import_identity_private_api_and_shared_patches`

[Complete base function, decorators and scenarios](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py#L31)

Outer assertions (complete scenario link also preserves helper calls, branch conditions and inputs):

```python
# Observable assertions are in the subprocess payloads below.
```

Complete embedded Python scenario 1 (preserve its assertions, control flow and expected values):

```python
import importlib, sys
sys.path.insert(0, sys.argv[1])
name = sys.argv[2]
paths = [name, 'setec.core.' + name]
if sys.argv[3] == 'True': paths.reverse()
a, b = [importlib.import_module(path) for path in paths]
assert a is b
key = 'assert_judge_generator_disjoint' if name == 'judge_backends' else 'build_judge'
original = getattr(a, key)
sentinel = object()
setattr(a, key, lambda *args, **kwargs: sentinel)
assert getattr(b, key)('synthetic') is sentinel
setattr(b, key, original)
assert getattr(a, key) is original
if name != 'judge_backends':
    assert a._mock_judge is b._mock_judge
assert not any(n in sys.modules for n in ('torch', 'transformers', 'openai', 'anthropic'))
```

### `test_existing_class_pickle_contract`

[Complete base function, decorators and scenarios](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py#L54)

Outer assertions (complete scenario link also preserves helper calls, branch conditions and inputs):

```python
# Observable assertions are in the subprocess payloads below.
```

Complete embedded Python scenario 1 (preserve its assertions, control flow and expected values):

```python
import importlib, pickle, sys
sys.path.insert(0, sys.argv[1])
prefix = 'setec.core.' if sys.argv[2] == 'True' else ''
names = ('agd_move_scan_judge', 'argquality_judge', 'argument_certainty_judge',
         'argument_judge', 'cross_doc_consistency_judge', 'fallacy_judge',
         'judge_backends', 'narrative_judge', 'position_pair_register_judge',
         'warrant_judge')
mods = {name: importlib.import_module(prefix + name) for name in names}
identity = {'kind': 'manifest', 'model': 'synthetic'}
values = []
for name, mod in mods.items():
    if name == 'judge_backends':
        values.append(mod.JudgeDisjointnessError('synthetic'))
        continue
    values.append(mod.JudgeError('synthetic'))
    if name == 'argument_certainty_judge':
        item = mod.Claim('topic', 'statement', 0, 4, 'text', 'none')
        values.extend([item, mod.JudgeResult([item], identity)])
    elif name == 'cross_doc_consistency_judge':
        item = mod.Commitment('doc', 'topic', 'claim', 'statement', 0, 4, 'text')
        values.extend([item, mod.JudgeResult([item], identity)])
    elif name == 'position_pair_register_judge':
        item = mod.PositionPair('What?', 0, 4, 'text', 5, 9, 'more')
        values.extend([item, mod.JudgeResult([item], identity)])
    elif name == 'agd_move_scan_judge':
        values.append(mod.JudgeResult({'observations': []}, identity, []))
    else:
        values.append(mod.JudgeResult({}, identity))
for value in values:
    cls = type(value)
    legacy_class = pickle.loads(('c' + cls.__module__ + '\n' + cls.__name__ + '\n.').encode())
    assert legacy_class is cls
    payload = pickle.dumps(value)
    restored = pickle.loads(payload)
    assert type(restored) is cls
    assert restored.__dict__ == value.__dict__
    assert str(restored) == str(value)
    assert b'setec.core.' not in payload
```

### `test_copied_plugin_from_foreign_cwd`

[Complete base function, decorators and scenarios](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_p3_judge_family_relocation.py#L98)

Outer assertions (complete scenario link also preserves helper calls, branch conditions and inputs):

```python
assert result.returncode == 0, result.stderr
assert result.stdout == result.stderr == ''
```

Complete embedded Python scenario 1 (preserve its assertions, control flow and expected values):

```python
import runpy,sys; old=sys.modules['__main__']; runpy.run_path(sys.argv[2],run_name='__main__'); assert sys.modules['__main__'] is old
```

Complete embedded Python scenario 2 (preserve its assertions, control flow and expected values):

```python
import importlib,sys; sys.path.insert(0,sys.argv[1]); a=importlib.import_module('setec.core.'+sys.argv[2]); assert a is importlib.import_module(sys.argv[2])
```

## support family

Exact collected cases:

- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-atomic_publish-_identity]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-embedding_backend-_resolve_dtype]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-embeddings-_get_nlp]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-passage_remediation_projection-_normalize_masks]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-pool_guard-refusal_reason]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-shingle_dedup_checkpoint-_canonical]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-shingle_dedup_io-_absolute]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-shingle_dedup_validate-_refuse]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-surprisal_backend-_resolve_dtype]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[False-windows_descriptor_io-_valid_component]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-atomic_publish-_identity]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-embedding_backend-_resolve_dtype]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-embeddings-_get_nlp]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-passage_remediation_projection-_normalize_masks]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-pool_guard-refusal_reason]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-shingle_dedup_checkpoint-_canonical]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-shingle_dedup_io-_absolute]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-shingle_dedup_validate-_refuse]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-surprisal_backend-_resolve_dtype]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_import_identity_private_api_and_shared_patches[True-windows_descriptor_io-_valid_component]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_legacy_class_references_and_unloaded_value_pickles[False]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_legacy_class_references_and_unloaded_value_pickles[True]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_copied_plugin_from_foreign_cwd[direct]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_copied_plugin_from_foreign_cwd[runpy]`
- `plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py::test_copied_plugin_from_foreign_cwd[package]`

### `test_import_identity_private_api_and_shared_patches`

[Complete base function, decorators and scenarios](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py#L38)

Outer assertions (complete scenario link also preserves helper calls, branch conditions and inputs):

```python
# Observable assertions are in the subprocess payloads below.
```

Complete embedded Python scenario 1 (preserve its assertions, control flow and expected values):

```python
import importlib, os, sys
sys.path.insert(0, sys.argv[1])
name, key = sys.argv[2:4]
paths = [name, 'setec.core.' + name]
if sys.argv[4] == 'True': paths.reverse()
if name == 'windows_descriptor_io' and os.name != 'nt':
    for path in paths:
        try:
            importlib.import_module(path)
        except ImportError as exc:
            assert str(exc) == 'windows_descriptor_io is Windows-only'
        else:
            raise AssertionError('Windows-only module imported on POSIX')
else:
    a, b = [importlib.import_module(path) for path in paths]
    assert a is b
    original = getattr(a, key)
    sentinel = object()
    setattr(a, key, lambda *args, **kwargs: sentinel)
    assert getattr(b, key)('synthetic') is sentinel
    setattr(b, key, original)
    assert getattr(a, key) is original
assert not any(n in sys.modules for n in (
    'torch', 'transformers', 'sentence_transformers', 'spacy', 'numpy', 'openai'))
```

### `test_legacy_class_references_and_unloaded_value_pickles`

[Complete base function, decorators and scenarios](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py#L68)

Outer assertions (complete scenario link also preserves helper calls, branch conditions and inputs):

```python
# Observable assertions are in the subprocess payloads below.
```

Complete embedded Python scenario 1 (preserve its assertions, control flow and expected values):

```python
import importlib, os, pickle, sys
sys.path.insert(0, sys.argv[1])
prefix = 'setec.core.' if sys.argv[2] == 'True' else ''
classes = {
    'embedding_backend': ('EmbeddingBackendError', 'EmbeddingBackend'),
    'embeddings': ('EmbeddingsBackendError',),
    'surprisal_backend': ('SurprisalBackendError', 'SurprisalBackend'),
    'passage_remediation_projection': ('RemediationError', 'RemediationProjection', '_UnionFind'),
    'shingle_dedup_checkpoint': ('CheckpointRefusal', '_SharedVmBudget', 'CheckpointSnapshot',
                                'CheckpointState', 'ImmutableShardDirectory', 'CheckpointDirectory'),
    'shingle_dedup_io': ('SecureIOError',),
    'shingle_dedup_validate': ('IndexValidationError', 'VmBudget'),
}
if os.name == 'nt':
    classes['windows_descriptor_io'] = (
        'UNICODE_STRING', 'OBJECT_ATTRIBUTES', 'IO_STATUS_BLOCK', 'FILETIME',
        'BY_HANDLE_FILE_INFORMATION', 'FILE_BASIC_INFORMATION', 'ACL_SIZE_INFORMATION',
        'ACE_HEADER', 'ACCESS_ALLOWED_ACE', 'TOKEN_USER', 'NodeInfo')
mods = {name: importlib.import_module(prefix + name) for name in classes}
for name, names in classes.items():
    for key in names:
        cls = getattr(mods[name], key)
        assert pickle.loads(('c' + name + '\n' + key + '\n.').encode()) is cls
        payload = pickle.dumps(cls)
        assert pickle.loads(payload) is cls
        assert b'setec.core.' not in payload
# No live model, handle or SQLite connection is serialized or created.
snapshot = mods['shingle_dedup_checkpoint'].CheckpointSnapshot(
    'inventory-00000001.sqlite', 'inventory', 1, b'synthetic', {'key': 'value'})
values = [
    mods['embedding_backend'].EmbeddingBackend('synthetic/local', device='cpu'),
    mods['surprisal_backend'].SurprisalBackend('synthetic/local', device='cpu'),
    mods['passage_remediation_projection'].RemediationProjection([], [], [], [], [], []),
    mods['passage_remediation_projection']._UnionFind(['a', 'b']),
    snapshot,
    mods['shingle_dedup_checkpoint'].CheckpointState('synthetic', (snapshot,), {}),
    mods['shingle_dedup_checkpoint']._SharedVmBudget(),
    mods['shingle_dedup_validate'].VmBudget(),
    mods['embedding_backend'].EmbeddingBackendError('synthetic'),
    mods['embeddings'].EmbeddingsBackendError('synthetic'),
    mods['surprisal_backend'].SurprisalBackendError('synthetic'),
    mods['passage_remediation_projection'].RemediationError('synthetic'),
    mods['shingle_dedup_checkpoint'].CheckpointRefusal('synthetic'),
    mods['shingle_dedup_validate'].IndexValidationError('synthetic'),
]
for value in values:
    payload = pickle.dumps(value)
    restored = pickle.loads(payload)
    assert type(restored) is type(value)
    assert restored.__dict__ == value.__dict__
    if isinstance(value, Exception):
        assert str(restored) == str(value)
    assert b'setec.core.' not in payload
assert not any(n in sys.modules for n in ('torch', 'transformers', 'spacy', 'numpy'))
```

### `test_copied_plugin_from_foreign_cwd`

[Complete base function, decorators and scenarios](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_p3_support_family_relocation.py#L128)

Outer assertions (complete scenario link also preserves helper calls, branch conditions and inputs):

```python
assert result.returncode != 0
assert 'ImportError: windows_descriptor_io is Windows-only' in result.stderr
assert result.stdout == ''
assert result.returncode == 0, result.stderr
assert result.stdout == result.stderr == ''
```

Complete embedded Python scenario 1 (preserve its assertions, control flow and expected values):

```python
import importlib, os, runpy, sys
old = sys.modules['__main__']
name, mode = sys.argv[2:4]
try:
    if mode == 'runpy':
        runpy.run_path(sys.argv[4], run_name='__main__')
    else:
        sys.path.insert(0, sys.argv[1])
        a = importlib.import_module('setec.core.' + name)
        assert a is importlib.import_module(name)
except ImportError as exc:
    assert name == 'windows_descriptor_io' and os.name != 'nt'
    assert str(exc) == 'windows_descriptor_io is Windows-only'
else:
    assert name != 'windows_descriptor_io' or os.name == 'nt'
assert sys.modules['__main__'] is old
```

## Implementation consumption record

The separate preparation is independently cleared draft PR538 at exact `c8c5df5d1e51bf77bf69ba477d8c3d0097369a3f`. This subsequent implementation consumes its cases and expectations. All63 original node IDs map one-to-one to the same IDs, with complete scenario functions, decorators, module globals and embedded payloads preserved outside the local probe/import edits. Diagnostic comparison verified the three complete module trees under that exclusion; no source-freeze test was added.

The focused Windows run passes75 cases:63 originals and12 separate behavioral controls. The controls protect live runner/executable rebinding, argument string conversions and subprocess options, None success return, nonzero-exit refusal with stderr as the assertion message, and refusal of either nonempty output. They inject module-local synthetic bindings rather than patching global sys or subprocess.

Whole-suite collection completes without errors at11014 nodes. A broader run of the21 explicit shared-conftest caller modules plus12 new controls gives629 passes and40 failures. Every657 existing case outcome matches the earlier exploratory broad run, and all12 added cases pass. The40 failing nodes were also reproduced by the earlier exact-base failed-case rerun; no cause beyond reproduction is assigned here. HEAD remained fixed throughout these collection/execution runs.

This is bounded Windows/local evidence. The complete suite was collected, not executed again. No full-suite-green, POSIX execution, hosted CI or integration clearance is claimed. Production code, fixture bytes, workflows/selectors and divergent packaging-argument probes remain unchanged.
