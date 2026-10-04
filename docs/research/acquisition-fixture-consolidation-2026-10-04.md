# Acquisition fixture helper: prospective cohort receipt

Preparation for fleet-coordination issue #76, Part A1/A4. This receipt changes no tests, runtime code, fixtures or workflows and grants no implementation clearance.

Settled source base: `ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe`. Captured before the exploratory local helper edit on 2026-10-04. The four selected modules passed 136/136 with zero skips on Python 3.13, using installed acquisition dependencies. Original collection and JUnit outputs are retained locally; no private paths are published here.

An exploratory implementation was subsequently reviewed locally, including a repaired optional-dependency test. It remains unpublished. This separate preparation records the original base and must be reviewed before an admitted implementation consumes it; it does not claim that receipt review preceded the exploratory edit.

## Selected cohort and boundaries

Only the identical constructor body of `make_fetcher` is a candidate for sharing. Each module retains its function signature, live `ac.FixtureFetcher`, fixture directory and default-map callable. Evaluate the default map only when the argument is None; an explicit empty map bypasses it. Copy either map into an independent dictionary. Constructor values remain `rate_limit_seconds=0.0` and `respect_robots=False`. Four base bodies are AST-identical; this diagnostic establishes eligibility, without adding a source-shape test.

Module-specific URL maps, argument builders, payloads, acquisition behavior and optional-dependency guards remain local. No assertion/scenario collapse, module or node renaming, contract-value change, production edit, fixture edit or packaging relocation is proposed. The affected acquirers are ingestion helpers rather than normalized-entrypoint capability surfaces; there is no new envelope or claim-license expectation.

| Module | Baseline nodes | Local behavior retained |
|---|---:|---|
| CourtListener | 31 | Auth headers and credential-free stored URLs; cursor/retry discovery, brief/text filters, minimum words, deduplication, privacy, legal_brief manifest and validation |
| GovInfo CHRG | 54 | Date-only bounds, pagination, key threading, heading-based prepared-statement extraction, oral/member exclusion, witness parsing, minimum words, testimony_policy manifest, privacy and validation |
| OpenAlex/CORE | 21 | Cursor pagination, DOI/full-text join, missing/short-text skips, scholarly_article manifest, key boundary, deduplication, privacy and validation |
| PDF URLs | 30 | JSON/bare URL/comment parsing, bytes fetch and PDF extraction, image-only/short skips, grant_proposal manifest, deduplication, privacy and validation |

## Current selection and later case mapping

The existing Linux job selects the full scripts/tests directory. The six focused-platform commands do not name these four modules. Preserve every workflow byte and selector. This is a cohort selection observation, not October hosted clearance or completion of deferred A3.

For any later helper-only implementation, every original node below must map to the exact same node ID with the same assertion/scenario semantics and outcome. Additional behavior tests must be listed separately. Count equality alone is insufficient. Retain the current optional-dependency skip behavior; no unavailable dependency may become a new collection or test failure.

The following inventory lists each exact collected node and its base test function assertions. Parametrized cases retain their decorators, fixture inputs and expected values at the settled base; repeated rows refer to the same function with distinct existing parameters. Context managers such as pytest.raises and non-assert checks are included in the source function at the linked base. The future implementation must preserve the complete function and decorators, not merely the assertions excerpted here.

## courtlistener

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_auth_headers`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L114)

```python
assert cl._auth_headers('TOK') == {'Authorization': 'Token TOK'}
assert cl._auth_headers('') == {}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_fetcher_extra_headers_attr_default_and_set`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L119)

```python
assert ac.Fetcher().extra_headers == {}
assert ac.Fetcher(extra_headers={'Authorization': 'Token X'}).extra_headers == {'Authorization': 'Token X'}
assert ac.FixtureFetcher(url_map={}).extra_headers == {}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_make_requests_fetcher_carries_extra_headers`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L129)

```python
assert f.extra_headers == {'Authorization': 'Token X'}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_search_and_doc_urls_are_token_free`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L142)

```python
assert 'type=rd' in s and 'q=brief' in s
assert 'Token' not in s and 'Authorization' not in s
assert d.endswith('/recap-documents/101/')
assert 'Token' not in d
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_is_brief`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L151)

```python
assert cl._is_brief('Brief for Appellant')
assert cl._is_brief('Amicus Brief of the Association')
assert cl._is_brief('Memorandum in Support of Summary Judgment')
assert not cl._is_brief('Motion for Extension of Time')
assert not cl._is_brief('Notice of Appearance')
assert not cl._is_brief('')
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_iter_search_follows_next`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L160)

```python
assert ids == [1, 2]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_iter_search_retries_transient_page_failure`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L185)

```python
assert ids == [7]
assert f.calls == 2
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_iter_search_gives_up_after_retries`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L199)

```python
assert list(cl._iter_search('brief', f)) == []
assert f.calls == cl._SEARCH_RETRIES
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_discover_filters_to_briefs`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L213)

```python
assert {it.doc_id for it in items} == {'101', '102', '104'}
assert b1.title == 'Brief for Appellant'
assert b1.date == dt.date(2018, 5, 10)
assert b1.locator == cl._recap_doc_url('101')
assert 'Token' not in b1.locator
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_discover_requires_indexed_text`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L227)

```python
assert '105' not in {it.doc_id for it in items}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_extract_one_plain_text`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L235)

```python
assert 'summary of argument' in body.lower()
assert author == cl.DEFAULT_AUTHOR
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_end_to_end`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L248)

```python
assert rc == 0
assert len(txt_files) == 2, f'Expected 2 acquired briefs, got {[f.name for f in txt_files]}'
assert len(entries) == 2
assert len({e['content_hash'] for e in entries}) == 2
assert e['corpus_role'] == 'impostor'
assert e['register'] == 'legal_brief'
assert e['consent_status'] == 'public_record'
assert e['impostor_for'] == ['argscope_legal_brief']
assert e['acquired_via'].startswith('acquire_courtlistener_')
assert e['persona'] == 'courtlistener'
assert 'Token' not in e.get('source', '')
assert TOKEN not in e.get('source', '')
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_min_words_gate_high_drops_all`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L276)

```python
assert not output_dir.exists() or not list(output_dir.glob('*.txt'))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_short_brief_dropped`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L287)

```python
assert not any(('104' in (e.get('source') or '') for e in entries))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_author_override`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L296)

```python
assert entries and all((e['author'] == 'Legal Brief Pool' for e in entries))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_dedupe`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L308)

```python
assert first == 2
assert len(list(output_dir.glob('*.txt'))) == first
assert len(read_manifest(manifest_path)) == 2
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_dry_run_writes_nothing`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L320)

```python
assert rc == 0
assert not output_dir.exists() or not list(output_dir.glob('*.txt'))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_token_default_empty`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L335)

```python
assert cl.parse_options(make_args(api_key=None)).api_token == ''
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_token_from_env`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L340)

```python
assert cl.parse_options(make_args(api_key=None)).api_token == 'ENVTOK'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_privacy_guard_refuses_non_private`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L348)

```python
assert exc.value.code == 2
assert False
assert e.code == 2
# expects pytest.raises(SystemExit)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_argparse_rejects_missing_required`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L367)

```python
assert False
# expects pytest.raises(SystemExit)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_cli_help_lists_flags`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L385)

```python
assert flag in help_text, f'--help missing {flag}'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_emitted_manifest_validates_with_legal_brief`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L395)

```python
assert errors == [], f'Manifest should validate without errors: {errors}'
assert unknown_register == [], f'legal_brief should be a known register: {unknown_register}'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_matches_doc_terms_and_modes`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L446)

```python
assert cl._matches_doc_terms('Declaration of Jane Roe', cl.AFFIDAVIT_TERMS)
assert cl._matches_doc_terms('Expert Report of Dr. Smith', cl.AFFIDAVIT_TERMS)
assert not cl._matches_doc_terms('Brief for Appellant', cl.AFFIDAVIT_TERMS)
assert cl._is_brief('Brief for Appellant')
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_is_substantive_affidavit`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L453)

```python
assert cl._is_substantive_affidavit(_AFFIDAVIT_TEXT)
assert not cl._is_substantive_affidavit(_BOILERPLATE_AFFIDAVIT)
assert not cl._is_substantive_affidavit('I declare under penalty of perjury.')
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_looks_like_ocr_garbage`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L460)

```python
assert not cl._looks_like_ocr_garbage(_AFFIDAVIT_TEXT)
assert cl._looks_like_ocr_garbage(garbage)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_doc_type_profile_defaults`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L466)

```python
assert o.doc_type == 'affidavit'
assert o.query == cl.DOC_TYPE_PROFILES['affidavit']['query']
assert o.min_words == 1000
assert ob.query == cl.DOC_TYPE_PROFILES['brief']['query']
assert ob.min_words == 3000
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_discover_affidavit_mode_filters`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L477)

```python
assert ids == {'201', '203'}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_affidavit_screen_wired_into_process`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L496)

```python
assert p is not None
assert p2 is None
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_brief_mode_skips_affidavit_screen`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L510)

```python
assert p is not None
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py::test_zero_output_exit_code`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_courtlistener.py#L521)

```python
assert cl.run(make_args(**ze), fetcher=make_fetcher()) == 1
assert cl.run(make_args(allow_empty=True, **ze), fetcher=make_fetcher()) == 0
assert cl.run(make_args(**od), fetcher=make_fetcher()) == 0
assert cl.run(make_args(**od), fetcher=make_fetcher()) == 0
```

## govinfo_chrg

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_add_query_sets_and_overrides`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L136)

```python
assert 'b=1' in u and 'api_key=K' in u
assert u2.count('api_key=') == 1 and 'api_key=K2' in u2
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_govinfo_date_is_date_only`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L144)

```python
assert gi._govinfo_date(dt.date(2018, 1, 2)) == '2018-01-02'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_witness_name`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L149)

```python
assert gi._witness_name('Jane Smith, Director, Office of Widgets') == 'Jane Smith'
assert gi._witness_name('Hon. Robert Jones') == 'Hon. Robert Jones'
assert gi._witness_name('') == gi.WITNESS_FALLBACK
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_split_prepared_statements`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L155)

```python
assert [block.witness for block in stmts] == ['Jane Doe', 'John Roe']
assert 'written statement' in doe_body
assert 'Questions and answers' not in doe_body
assert 'STATEMENT OF JANE DOE' not in doe_body
assert gi._split_prepared_statements('Markup of H.R. 1. The CHAIRMAN. ...').statements == []
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_oral_turn_closes_written_candidate_before_later_marker`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L180)

```python
assert 'Spoken questions' not in ada.body_text
assert any((item.author == 'Bea Two' for item in items))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_known_written_brackets_and_separator_keep_exact_offsets`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L199)

```python
assert result.issues == []
assert len(result.statements) == 2
assert ada.boundary_kind == 'next-prepared-heading'
assert text[ada.body_start:ada.body_end] == ada.body
assert not ada.body.endswith('____')
assert "Bea's" not in ada.body
assert marker in ada.body
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_unknown_transition_and_structural_label_refuse_prior`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L227)

```python
assert [(i.heading_ordinal, i.reason) for i in result.issues] == [(1, expected_reason)]
assert [block.witness for block in result.statements] == ['Bea Two']
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_structural_turns_quote_cue_oral_heading_and_unbounded_refusals`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L245)

```python
assert len(result.statements) == 1
assert '"Senator Vale.' in result.statements[0].body
assert gi._split_prepared_statements(paired).statements[0].body == written
assert gi._split_prepared_statements(unpaired).issues[0].reason == 'unpaired-oral-heading'
assert gi._split_prepared_statements(intervening).issues[0].reason == 'unpaired-oral-heading'
assert gi._split_prepared_statements('Prepared Statement of Ada One\n' + written).issues[0].reason == 'unbounded-eof'
assert result.issues == []
assert result.statements[0].body == written
assert result.statements[0].boundary_kind == 'oral-speaker-turn'
assert len(result.statements) == 2
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compound_speaker_name_closes_before_later_heading[Senator Van Hollen.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L311)

```python
assert result.issues == []
assert [(block.witness, block.body, block.boundary_kind) for block in result.statements] == [('Ada One', 'Written block.', 'oral-speaker-turn'), ('Bea Two', 'Independent body.', 'procedural-bracket')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compound_speaker_name_closes_before_later_heading[Representative De La Cruz.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L311)

```python
assert result.issues == []
assert [(block.witness, block.body, block.boundary_kind) for block in result.statements] == [('Ada One', 'Written block.', 'oral-speaker-turn'), ('Bea Two', 'Independent body.', 'procedural-bracket')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compound_speaker_name_closes_before_later_heading[Director Van Hollen.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L311)

```python
assert result.issues == []
assert [(block.witness, block.body, block.boundary_kind) for block in result.statements] == [('Ada One', 'Written block.', 'oral-speaker-turn'), ('Bea Two', 'Independent body.', 'procedural-bracket')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compound_speaker_name_closes_before_later_heading[Mr. Van Hollen.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L311)

```python
assert result.issues == []
assert [(block.witness, block.body, block.boundary_kind) for block in result.statements] == [('Ada One', 'Written block.', 'oral-speaker-turn'), ('Bea Two', 'Independent body.', 'procedural-bracket')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compound_speaker_name_closes_before_later_heading[Dr. De La Cruz.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L311)

```python
assert result.issues == []
assert [(block.witness, block.body, block.boundary_kind) for block in result.statements] == [('Ada One', 'Written block.', 'oral-speaker-turn'), ('Bea Two', 'Independent body.', 'procedural-bracket')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compound_speaker_name_closes_before_later_heading[Rear Admiral Vale.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L311)

```python
assert result.issues == []
assert [(block.witness, block.body, block.boundary_kind) for block in result.statements] == [('Ada One', 'Written block.', 'oral-speaker-turn'), ('Bea Two', 'Independent body.', 'procedural-bracket')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compound_speaker_name_closes_before_later_heading[Rear Admiral Van Hollen.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L311)

```python
assert result.issues == []
assert [(block.witness, block.body, block.boundary_kind) for block in result.statements] == [('Ada One', 'Written block.', 'oral-speaker-turn'), ('Bea Two', 'Independent body.', 'procedural-bracket')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compound_speaker_name_closes_before_later_heading[Lieutenant Colonel Vale.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L311)

```python
assert result.issues == []
assert [(block.witness, block.body, block.boundary_kind) for block in result.statements] == [('Ada One', 'Written block.', 'oral-speaker-turn'), ('Bea Two', 'Independent body.', 'procedural-bracket')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compound_speaker_name_closes_before_later_heading[Ms. De La Cruz.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L311)

```python
assert result.issues == []
assert [(block.witness, block.body, block.boundary_kind) for block in result.statements] == [('Ada One', 'Written block.', 'oral-speaker-turn'), ('Bea Two', 'Independent body.', 'procedural-bracket')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_office_title_heading_refuses_instead_of_clean_oral_close[Director of Operations.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L331)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-role-heading')]
assert [(block.witness, block.body) for block in result.statements] == [('Bea Two', 'Independent body.')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_office_title_heading_refuses_instead_of_clean_oral_close[Secretary of State.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L331)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-role-heading')]
assert [(block.witness, block.body) for block in result.statements] == [('Bea Two', 'Independent body.')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_office_title_heading_refuses_instead_of_clean_oral_close[Director for Widget Programs.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L331)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-role-heading')]
assert [(block.witness, block.body) for block in result.statements] == [('Bea Two', 'Independent body.')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_inline_office_title_remains_written_prose`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L345)

```python
assert result.issues == []
assert len(result.statements) == 1
assert 'Director of Operations' in result.statements[0].body
assert result.statements[0].boundary_kind == 'procedural-bracket'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_standalone_curly_quote_cue_refuses_ambiguous_speaker`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L356)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-quoted-boundary')]
assert [(block.witness, block.body) for block in result.statements] == [('Bea Two', 'Independent body.')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_procedural_text_in_content_bracket_refuses_prior[[Exhibit A admitted.]]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L376)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-bracket-transition')]
assert [(block.witness, block.body) for block in result.statements] == [('Bea Two', 'Independent body.')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_procedural_text_in_content_bracket_refuses_prior[[Table 2 admitted.]]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L376)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-bracket-transition')]
assert [(block.witness, block.body) for block in result.statements] == [('Bea Two', 'Independent body.')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_procedural_text_in_content_bracket_refuses_prior[[42 U.S.C. 123 admitted.]]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L376)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-bracket-transition')]
assert [(block.witness, block.body) for block in result.statements] == [('Bea Two', 'Independent body.')]
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_unsupported_structural_label_case_refuses_prior[THE STAFF DIRECTOR.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L394)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-speaker-turn')]
assert [block.witness for block in result.statements] == ['Bea Two']
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_unsupported_structural_label_case_refuses_prior[The Staff Director.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L394)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-speaker-turn')]
assert [block.witness for block in result.statements] == ['Bea Two']
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_unsupported_structural_label_case_refuses_prior[The FIELD OFFICER.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L394)

```python
assert [(issue.heading_ordinal, issue.reason) for issue in result.issues] == [(1, 'ambiguous-speaker-turn')]
assert [block.witness for block in result.statements] == ['Bea Two']
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_hard_wrapped_prose_is_not_structural_speaker[the Widget Committee. The synthetic plan continues.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L411)

```python
assert result.issues == []
assert len(result.statements) == 2
assert continuation in result.statements[0].body
assert result.statements[0].boundary_kind == 'next-prepared-heading'
assert result.statements[1].witness == 'Bea Two'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_hard_wrapped_prose_is_not_structural_speaker[the XYZ. The synthetic acronym remains in the argument.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L411)

```python
assert result.issues == []
assert len(result.statements) == 2
assert continuation in result.statements[0].body
assert result.statements[0].boundary_kind == 'next-prepared-heading'
assert result.statements[1].witness == 'Bea Two'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_hard_wrapped_prose_is_not_structural_speaker[The Widget Committee. A hard-wrapped name continues.]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L411)

```python
assert result.issues == []
assert len(result.statements) == 2
assert continuation in result.statements[0].body
assert result.statements[0].boundary_kind == 'next-prepared-heading'
assert result.statements[1].witness == 'Bea Two'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_standalone_quote_cue_closes_and_unclosed_cue_refuses`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L426)

```python
assert closed.issues == []
assert closed.statements[0].boundary_kind == 'oral-speaker-turn'
assert 'An ordinary quoted sentence.' in closed.statements[0].body
assert 'Oral turn after' not in closed.statements[0].body
assert closed.statements[1].witness == 'Bea Two'
assert [(issue.heading_ordinal, issue.reason) for issue in unclosed.issues] == [(1, 'ambiguous-open-quotation')]
assert [block.witness for block in unclosed.statements] == ['Bea Two']
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_compact_content_labels_and_inline_salutation_remain_written`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L451)

```python
assert result.issues == []
assert len(result.statements) == 1
assert result.statements[0].boundary_kind == 'procedural-bracket'
assert retained in body
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_overlong_candidate_refused_with_and_without_close`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L466)

```python
assert not result.statements
assert result.issues[0].reason == 'overlong-statement'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_iter_pages_follows_nextpage`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L476)

```python
assert [i['packageId'] for i in items] == ['P1', 'P2']
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_discover_splits_statements`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L501)

```python
assert {it.author for it in items} == {'Jane Smith', 'Bob Short', 'Robert Jones'}
assert len(items) == 3
assert smith.package_id == PKG1
assert smith.date == dt.date(2019, 5, 10)
assert smith.title == 'Prepared statement of Jane Smith'
assert 'widget' in smith.body_text.lower()
assert smith.locator == gi._granule_content_url(PKG1, PKG1)
assert 'api_key' not in smith.locator
assert not any(('Member' in it.author for it in items))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_extract_one_returns_discovered_body`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L521)

```python
assert body == item.body_text
assert title == item.title
assert author == 'Jane Smith'
assert gi.extract_one(gi.ItemMeta(locator='x'), options, make_fetcher())[0] == ''
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_end_to_end`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L543)

```python
assert rc == 0
assert len(txt_files) == 2, f'Expected 2 acquired statements, got {[f.name for f in txt_files]}'
assert len(entries) == 2
assert authors == {'Jane Smith', 'Robert Jones'}
assert len({e['content_hash'] for e in entries}) == 2
assert ac.html_text_is_clean(txt.read_text(encoding='utf-8'))
assert e['corpus_role'] == 'impostor'
assert e['register'] == 'testimony_policy'
assert e['consent_status'] == 'public_record'
assert e['era'] == 'pre_chatgpt'
assert e['impostor_for'] == ['argscope_testimony_policy']
assert e['acquired_via'].startswith('acquire_govinfo_chrg_')
assert e['content_hash'].startswith('sha256:')
assert e['persona'] == 'chrg'
assert e['language_status'] == 'unknown'
assert len(meta['source_text_sha256']) == 64
assert meta['boundary_kind'] == 'procedural-bracket'
assert meta['heading_start'] < meta['heading_end'] < meta['body_start'] < meta['body_end']
assert meta['role_review_status'] == 'pending'
assert meta['rights_review_status'] == 'pending'
assert meta['extraction_completeness_status'] == 'pending_source_review'
assert meta['source_text_sha256'] == hashlib.sha256(decoded.encode('utf-8')).hexdigest()
assert decoded[meta['body_start']:meta['body_end']].strip()
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_run_logs_boundary_refusal_and_keeps_later_heading`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L594)

```python
assert gi.run(args, fetcher=make_fetcher(mapping)) == 0
assert 'Ada One' not in {entry['author'] for entry in entries}
assert 'Bea Two' in {entry['author'] for entry in entries}
assert len(skips) == 1
assert skips[0]['url'] == gi._granule_content_url(PKG1, PKG1)
assert 'api_key' not in skips[0]['url']
assert report['skipped_parse_error'] >= 1
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_all_unbounded_candidates_fail_zero_output_with_skip_log`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L627)

```python
assert gi.run(args, fetcher=make_fetcher(mapping)) == 1
assert not list(output_dir.glob('*.txt'))
assert not list(output_dir.glob('*.meta.json'))
assert not (output_dir / 'draft_manifest.jsonl').exists()
assert [item['reason'] for item in report['skip_log']] == ['unbounded-eof', 'unbounded-eof']
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_min_words_gate_high_drops_all`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L653)

```python
assert not output_dir.exists() or not list(output_dir.glob('*.txt'))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_short_statement_dropped`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L664)

```python
assert not any((e['author'] == 'Bob Short' for e in entries))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_author_override`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L674)

```python
assert entries and all((e['author'] == 'Congressional Testimony Pool' for e in entries))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_dedupe_within_output_dir`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L688)

```python
assert first == 2
assert len(list(output_dir.glob('*.txt'))) == first
assert len(read_manifest(manifest_path)) == 2
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_dry_run_writes_nothing`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L700)

```python
assert rc == 0
assert not output_dir.exists() or not list(output_dir.glob('*.txt'))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_api_key_resolution_default_demo`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L715)

```python
assert opts.api_key == gi.DEMO_KEY
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_api_key_from_env`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L721)

```python
assert opts.api_key == 'ENVKEY'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_api_key_in_request_urls`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L727)

```python
assert fetcher.fetched_urls, 'discovery should have requested at least one URL'
assert all(('api_key=SECRET' in u for u in fetcher.fetched_urls))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_manifest_source_has_no_api_key`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L737)

```python
assert entries
assert 'api_key' not in e.get('source', '')
assert KEY not in e.get('source', '')
assert 'api_key' not in meta.get('source_url', '')
assert KEY not in meta.get('source_url', '')
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_privacy_guard_refuses_non_private`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L758)

```python
assert exc.value.code == 2
assert False, 'expected SystemExit(2)'
assert e.code == 2
# expects pytest.raises(SystemExit)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_argparse_rejects_missing_required`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L777)

```python
assert False, f'should reject {argv}'
# expects pytest.raises(SystemExit)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_cli_help_lists_flags`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L795)

```python
assert flag in help_text, f'--help missing {flag}'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_emitted_manifest_validates`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L809)

```python
assert errors == [], f'Manifest should validate without errors: {errors}'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py::test_zero_output_exit_code`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_govinfo_chrg.py#L839)

```python
assert gi.run(make_args(**ze), fetcher=make_fetcher()) == 1
assert gi.run(make_args(allow_empty=True, **ze), fetcher=make_fetcher()) == 0
assert gi.run(make_args(**od), fetcher=make_fetcher()) == 0
assert gi.run(make_args(**od), fetcher=make_fetcher()) == 0
```

## openalex_core

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_add_query_overrides_without_dup`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L120)

```python
assert u2.count('api_key=') == 1 and 'api_key=K2' in u2
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_bare_doi`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L126)

```python
assert oc._bare_doi('https://doi.org/10.1/x') == '10.1/x'
assert oc._bare_doi('http://doi.org/10.1/x') == '10.1/x'
assert oc._bare_doi('doi:10.1/x') == '10.1/x'
assert oc._bare_doi('10.1/x') == '10.1/x'
assert oc._bare_doi('') == ''
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_first_author_and_date`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L134)

```python
assert oc._first_author(work) == 'Jane Scholar'
assert oc._work_date(work) == dt.date(2018, 5, 10)
assert oc._work_date({'publication_year': 2015}) == dt.date(2015, 1, 1)
assert oc._first_author({}) == 'Unknown'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_core_doi_search_url_is_key_free`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L146)

```python
assert 'api_key' not in u
assert 'doi' in u and 'd1' in u
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_iter_openalex_follows_cursor`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L152)

```python
assert ids == ['W1', 'W2']
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_discover_yields_candidates`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L175)

```python
assert len(items) == 4
assert d1.title == 'On the Justification of Legal Norms'
assert d1.author == 'Jane Scholar'
assert d1.date == dt.date(2018, 5, 10)
assert d1.locator == 'https://doi.org/10.1234/d1'
assert 'api_key' not in d1.locator
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_extract_one_core_join`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L188)

```python
assert 'legal norms' in body.lower()
assert author == 'Jane Scholar'
assert oc.extract_one(d3, options, fetcher)[0] == ''
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_end_to_end`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L204)

```python
assert rc == 0
assert len(txt_files) == 2, f'Expected 2 acquired articles, got {[f.name for f in txt_files]}'
assert len(entries) == 2
assert {e['author'] for e in entries} == {'Jane Scholar', 'Robert Theorist'}
assert len({e['content_hash'] for e in entries}) == 2
assert e['corpus_role'] == 'impostor'
assert e['register'] == 'scholarly_article'
assert e['consent_status'] == 'cc_licensed'
assert e['impostor_for'] == ['argscope_scholarly_article']
assert e['acquired_via'].startswith('acquire_openalex_core_')
assert e['persona'] == 'scholar'
assert 'api_key' not in e.get('source', '')
assert KEY not in e.get('source', '')
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_min_words_gate_high_drops_all`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L233)

```python
assert not output_dir.exists() or not list(output_dir.glob('*.txt'))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_zero_output_exit_code`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L244)

```python
assert oc.run(make_args(**ze), fetcher=make_fetcher()) == 1
assert oc.run(make_args(allow_empty=True, **ze), fetcher=make_fetcher()) == 0
assert oc.run(make_args(**od), fetcher=make_fetcher()) == 0
assert oc.run(make_args(**od), fetcher=make_fetcher()) == 0
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_short_article_dropped`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L258)

```python
assert not any(('Short Research Note' in (e.get('source') or '') for e in entries))
assert not any(('d4' in (e.get('source') or '') for e in entries))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_author_override`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L269)

```python
assert entries and all((e['author'] == 'Scholarly Article Pool' for e in entries))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_dedupe`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L281)

```python
assert first == 2
assert len(list(output_dir.glob('*.txt'))) == first
assert len(read_manifest(manifest_path)) == 2
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_dry_run_writes_nothing`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L293)

```python
assert rc == 0
assert not output_dir.exists() or not list(output_dir.glob('*.txt'))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_core_key_default_empty`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L308)

```python
assert oc.parse_options(make_args(api_key=None)).core_api_key == ''
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_core_key_from_env`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L313)

```python
assert oc.parse_options(make_args(api_key=None)).core_api_key == 'ENVKEY'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_core_key_added_at_fetch_only`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L318)

```python
assert fetcher.fetched_urls
assert all(('api_key' not in u for u in fetcher.fetched_urls)), 'OpenAlex discovery is keyless'
assert any(('api_key=SECRET' in u for u in f2.fetched_urls))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_privacy_guard_refuses_non_private`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L336)

```python
assert exc.value.code == 2
assert False
assert e.code == 2
# expects pytest.raises(SystemExit)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_argparse_rejects_missing_required`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L355)

```python
assert False
# expects pytest.raises(SystemExit)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_cli_help_lists_flags`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L373)

```python
assert flag in help_text, f'--help missing {flag}'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py::test_emitted_manifest_validates_with_scholarly_article`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_openalex_core.py#L383)

```python
assert errors == [], f'Manifest should validate without errors: {errors}'
assert unknown_register == [], f'scholarly_article should be a known register: {unknown_register}'
```

## pdf_urls

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_fetch_bytes_via_fixture`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L124)

```python
assert data and b'SPECIFIC AIMS' in data
assert f.fetch_bytes('https://ex.test/imageonly.pdf') == b''
assert f.fetch_bytes('https://ex.test/missing.pdf') is None
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_pdf_text_from_bytes_real`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L133)

```python
assert isinstance(text, str) and len(text.strip()) > 0
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_pdf_text_from_bytes_garbage`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L148)

```python
assert ac.pdf_text_from_bytes(b'') == ''
assert ac.pdf_text_from_bytes(b'not a pdf at all') == ''
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_fetcher_bytes_path_unaffects_text_path`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L153)

```python
assert 'SPECIFIC AIMS' in r.text
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_parse_line`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L163)

```python
assert pu._parse_line('# comment') is None
assert pu._parse_line('   ') is None
assert pu._parse_line('https://x.test/a.pdf') == {'url': 'https://x.test/a.pdf'}
assert pu._parse_line('{"url": "https://x.test/b.pdf", "title": "T"}') == {'url': 'https://x.test/b.pdf', 'title': 'T'}
assert pu._parse_line('{"title": "no url"}') is None
assert pu._parse_line('{bad json') is None
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_tanner_artifact_profile_is_explicitly_routed_to_pdf_extraction`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L174)

```python
assert text
assert seen == ['tanner']
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_title_from_url`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L196)

```python
assert pu._title_from_url('https://x.test/path/MyGrant.pdf') == 'MyGrant'
assert pu._title_from_url('https://x.test/') == 'untitled'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_discover_parses_all_entries`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L201)

```python
assert urls == {'https://ex.test/grant1.pdf', 'https://ex.test/grant2.pdf', 'https://ex.test/grant3.pdf', 'https://ex.test/imageonly.pdf'}
assert g1.title == 'Specific Aims and Significance'
assert g1.author == 'Dr. PI One'
assert g1.date == dt.date(2018, 5, 10)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_discover_date_window`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L215)

```python
assert dated == {'https://ex.test/grant1.pdf'}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_discover_tolerates_utf8_bom`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L224)

```python
assert urls.read_bytes().startswith(b'\xef\xbb\xbf')
assert {it.locator for it in items} == {'https://ex.test/a.pdf', 'https://ex.test/b.pdf'}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_end_to_end`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L245)

```python
assert rc == 0
assert len(txt_files) == 2, f'Expected 2 acquired PDFs, got {[f.name for f in txt_files]}'
assert len(entries) == 2
assert 'https://ex.test/grant1.pdf' in by_src
assert 'https://ex.test/grant2.pdf' in by_src
assert by_src['https://ex.test/grant1.pdf']['author'] == 'Dr. PI One'
assert by_src['https://ex.test/grant2.pdf']['author'] == 'Unknown'
assert len({e['content_hash'] for e in entries}) == 2
assert e['corpus_role'] == 'impostor'
assert e['register'] == 'grant_proposal'
assert e['consent_status'] == 'cc_licensed'
assert e['impostor_for'] == ['argscope_grant_proposal']
assert e['acquired_via'].startswith('acquire_pdf_urls_')
assert e['persona'] == 'opengrants'
assert e['language_status'] == 'unknown'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_fetched_pdf_hash_and_profile_in_preprocessing`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L277)

```python
assert pu.run(args, fetcher=make_fetcher()) == 0
assert len(entries) == 1
assert sidecar['scraper_version'] == '1.1'
assert prep['source_pdf_sha256'] == hashlib.sha256((FIXTURE_DIR / 'grant1.pdf').read_bytes()).hexdigest()
assert prep['artifact_profile'] == 'tanner'
assert sidecar['content_hash'] == entries[0]['content_hash']
assert sidecar['content_hash'] == ac.compute_content_hash(next(output_dir.glob('*.txt')).read_bytes().decode('utf-8'))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_fetched_pdf_receipt_is_not_reused_for_mismatched_manual_processing`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L305)

```python
assert piece is not None
assert 'source_pdf_sha256' not in piece.preprocessing_meta
assert 'artifact_profile' not in piece.preprocessing_meta
assert item.fetched_pdf_sha256 is None
assert item.extracted_text_sha256 is None
assert piece is not None
assert 'source_pdf_sha256' not in piece.preprocessing_meta
assert pu.extract_one(item, options, make_fetcher({})) == ('', '', '', None)
assert item.fetched_pdf_sha256 is None
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_explicit_language_status_reaches_manifest[native]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L352)

```python
assert pu.run(args, fetcher=make_fetcher()) == 0
assert {e['language_status'] for e in read_manifest(manifest_path)} == {language_status}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_explicit_language_status_reaches_manifest[non_native_advanced]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L352)

```python
assert pu.run(args, fetcher=make_fetcher()) == 0
assert {e['language_status'] for e in read_manifest(manifest_path)} == {language_status}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_explicit_language_status_reaches_manifest[non_native_intermediate]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L352)

```python
assert pu.run(args, fetcher=make_fetcher()) == 0
assert {e['language_status'] for e in read_manifest(manifest_path)} == {language_status}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_explicit_language_status_reaches_manifest[learner]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L352)

```python
assert pu.run(args, fetcher=make_fetcher()) == 0
assert {e['language_status'] for e in read_manifest(manifest_path)} == {language_status}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_explicit_language_status_reaches_manifest[unknown]`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L352)

```python
assert pu.run(args, fetcher=make_fetcher()) == 0
assert {e['language_status'] for e in read_manifest(manifest_path)} == {language_status}
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_cli_language_status_default_and_invalid_choice`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L367)

```python
assert parser.parse_args(required).language_status == 'unknown'
# expects pytest.raises(SystemExit)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_image_only_skipped`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L378)

```python
assert not any(('imageonly' in (e.get('source') or '') for e in entries))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_short_dropped`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L387)

```python
assert not any(('grant3' in (e.get('source') or '') for e in entries))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_min_words_gate_high_drops_all`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L396)

```python
assert not output_dir.exists() or not list(output_dir.glob('*.txt'))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_author_override`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L407)

```python
assert entries and all((e['author'] == 'Grant Proposal Pool' for e in entries))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_dedupe`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L419)

```python
assert first == 2
assert len(list(output_dir.glob('*.txt'))) == first
assert len(read_manifest(manifest_path)) == 2
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_dry_run_writes_nothing`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L431)

```python
assert rc == 0
assert not output_dir.exists() or not list(output_dir.glob('*.txt'))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_privacy_guard_refuses_non_private`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L446)

```python
assert exc.value.code == 2
assert False
assert e.code == 2
# expects pytest.raises(SystemExit)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_argparse_rejects_missing_required`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L465)

```python
assert False
# expects pytest.raises(SystemExit)
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_cli_help_lists_flags`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L486)

```python
assert flag in help_text, f'--help missing {flag}'
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_emitted_manifest_validates_with_grant_proposal`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L496)

```python
assert errors == [], f'Manifest should validate without errors: {errors}'
assert register_issues
assert all((i['severity'] == 'warning' for i in register_issues))
assert all(('Deprecated register' in i['message'] for i in register_issues))
assert all(('grant_proposal_academic' in i['message'] for i in register_issues))
assert all(('grant_proposal_nonprofit' in i['message'] for i in register_issues))
assert not any(('Unknown register' in i['message'] for i in register_issues))
```

### `plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py::test_zero_output_exit_code`

[Base scenario and complete expectations](https://github.com/anotherpanacea-eng/setec-voiceprint/blob/ecb3f9d732240f2d08d2b2bf1176f452e15bc5fe/plugins/setec-voiceprint/scripts/tests/test_acquire_pdf_urls.py#L529)

```python
assert pu.run(make_args(**ze), fetcher=make_fetcher()) == 1
assert pu.run(make_args(allow_empty=True, **ze), fetcher=make_fetcher()) == 0
assert pu.run(make_args(**od), fetcher=make_fetcher()) == 0
assert pu.run(make_args(**od), fetcher=make_fetcher()) == 0
```

## Implementation consumption record

The separate preparation is Voiceprint draft PR534, exact `c170c9f6b884fc1eae7c08226990d1852f5d0f85`, independently CLEAR before publication. This later branch consumes that receipt. The existing 136 nodes map one-to-one to identical IDs, with unchanged complete scenario functions, decorators, fixture maps and module globals outside the helper/import edits. Diagnostic comparison verified all four source trees outside those two edits; no source-freeze test was added.

All 136 original acquisition cases and four added behavioral cases pass on Python 3.13. The four added cases also pass on Python 3.12 without acquisition extras, using module-local synthetic constructor bindings rather than mutating shared acquisition_core or requiring missing optional modules. They cover lazy default evaluation, explicit empty-map bypass, dictionary isolation, constructor options and live per-module bindings. Existing dependency skip markers remain intact.

Broader suite and final implementation review remain pending. No full-suite, hosted CI or integration clearance is claimed by these focused results. Runtime code, fixture bytes and workflows are unchanged.
