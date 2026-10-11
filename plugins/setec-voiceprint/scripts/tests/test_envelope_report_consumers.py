"""Real producer output reaches final report evidence; unavailable stays absent."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import adversarial_robustness_card as arc
import before_after_restoration as bar
import bigram_diff
import generate_voice_report as gvr
import idiolect_detector
import restoration_packet as rp
import variance_audit
import voice_distance
import voice_drift_tracker as vdt
import voice_profile
from setec.paths import scripts_dir

SCRIPTS = scripts_dir()
FIXTURES = SCRIPTS / 'test_data' / 'voice_report_fixture'


def _variance(burstiness=-0.48, kl=0.20):
    return variance_audit.build_audit_payload({
        'audit': {'tier1': {'sentence_length': {'burstiness_B': burstiness, 'sd': 12},
                            'mtld': 80, 'mattr': {'value': 0.65}}},
        'compression': {'pos_bigram_kl': {'value': kl}, 'compression_fraction': 0.2},
    }, target_path='public-synthetic.txt')


def _bigram(value=0.30):
    return bigram_diff.build_audit_payload(
        target_path='public-synthetic.txt', target_counts={'DET-ADJ': 20},
        cluster_loaded=[Path('public-control.txt')], cluster_skipped=[],
        pooled_diff=[] if value is None else [{'bigram': 'DET-ADJ', 'kl_contrib': value}],
        mean_diff=None, top=10, alpha=0.5, min_count=1,
    )


def _voice(value):
    return voice_distance.build_audit_payload(
        {'overall': {'weighted_delta': value}}, target_path='public-synthetic.txt',
    )


def _idiolect(body=None):
    return idiolect_detector.build_audit_payload(
        body or {'method': {'keyness': 'log_odds'}, 'rankings': {}, 'preservation_list': ['quiet harbor']},
        target_path='public-synthetic.txt', reference_path='public-control.txt',
    )


def _packet(pid='variance_burstiness_B_direct', evidence=None):
    return rp.Packet(pid, 'direct', 'burstiness_B', 'under_represented', 'moderate',
                     evidence or {}, 'Synthetic diagnostic', [], [], [])


def _packets(*packets):
    return json.loads(rp.render_json(packets, {}, inputs={}, target_scope='whole document',
                                     genre='essay', private=False))


def _unavailable(envelope, bare=False):
    result = copy.deepcopy(envelope['results'] if bare else envelope)
    result['available'] = False
    if not bare:
        result['results']['available'] = True  # plausible payload cannot override refusal
    return result


def _write(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')
    return str(path)


def _evaluate(packet, **overrides):
    args = dict(before_variance=None, after_variance=None, before_bigram=None,
                after_bigram=None, before_voice=None, after_voice=None,
                before_idiolect=None, after_idiolect=None)
    args.update(overrides)
    return bar.evaluate_packet(packet, **args)


def _diff(**overrides):
    args = dict(before_variance=None, after_variance=None, before_bigram=None,
                after_bigram=None, before_voice=None, after_voice=None)
    args.update(overrides)
    return bar.diff_all_signals(**args)


def test_before_after_final_reports_match_real_producer_payloads(tmp_path):
    envelopes = {
        'packet': _packets(_packet(), _packet('pos_bigram_DET_ADJ_over_represented'),
                          _packet('idiolect_preservation', {'phrases_preview': ['quiet harbor']})),
        'before-variance': _variance(), 'after-variance': _variance(-0.30, 0.19),
        'before-bigram': _bigram(), 'after-bigram': _bigram(0.10),
        'before-voice': _voice(0.4), 'after-voice': _voice(0.8),
        'before-idiolect': _idiolect(), 'after-idiolect': _idiolect(),
    }
    original = copy.deepcopy(envelopes)
    revised = tmp_path / 'revised.txt'
    revised.write_text('We returned to the quiet harbor.', encoding='utf-8')
    outputs = []
    for mode in ('bare', 'envelope'):
        args = ['--revised-text', str(revised)]
        for name, envelope in envelopes.items():
            data = envelope if mode == 'envelope' else {
                **envelope['results'], 'task_surface': envelope['task_surface'],
            }
            args += ['--'+name+'-json', _write(tmp_path / (mode+name+'.json'), data)]
        md, result = tmp_path / (mode+'.md'), tmp_path / (mode+'-out.json')
        assert bar.main(args + ['--out', str(md), '--json-out', str(result)]) == 0
        payload = json.loads(result.read_text('utf-8'))['results']
        payload.pop('inputs')
        outputs.append((payload, md.read_text('utf-8')))
    assert outputs[0] == outputs[1]
    payload, report = outputs[1]
    assert payload['packet_summary']['n_packets'] == 3
    assert payload['verdicts'][0]['before'] == -0.48
    assert payload['verdicts'][0]['verdict'] == 'gamed'  # actual voice-distance aggregate rose
    assert payload['verdicts'][1]['delta'] == pytest.approx(-0.2)
    assert payload['preservation_check']['survival_rate'] == 1
    assert 'Idiolect preservation list' in report
    assert envelopes == original


@pytest.mark.parametrize('bare', [False, True])
def test_unavailable_after_bigram_cannot_be_graded_as_improved(bare):
    result = _evaluate(_packet('pos_bigram_DET_ADJ_over_represented').to_dict(),
                       before_bigram=_bigram()['results'], after_bigram=_unavailable(_bigram(0.01), bare))
    assert result.before == 0.3
    assert result.after is None
    assert result.verdict == 'not_measurable'
    assert result.signed_improvement is None
    # A usable audit with the particular row absent retains its zero-row convention.
    available = _evaluate(_packet('pos_bigram_DET_ADJ_over_represented').to_dict(),
                          before_bigram=_bigram()['results'], after_bigram=_bigram(None)['results'])
    assert available.after == 0
    assert available.verdict == 'improved'


def test_absent_after_audit_is_unmeasurable():
    result = _evaluate(_packet('pos_bigram_DET_ADJ_over_represented').to_dict(),
                       before_bigram=_bigram()['results'])
    assert result.after is None
    assert result.verdict == 'not_measurable'


@pytest.mark.parametrize('bare', [False, True])
def test_unavailable_paired_evidence_is_not_a_numeric_diff(bare):
    assert _diff(before_variance=_variance(), after_variance=_unavailable(_variance(), bare),
                 before_bigram=_bigram(), after_bigram=_unavailable(_bigram(), bare),
                 before_voice=_voice(0.2), after_voice=_unavailable(_voice(0.1), bare)) == {
        'variance': {}, 'bigram': {}, 'voice': {},
    }


@pytest.mark.parametrize('before,after,expected', [
    (_voice(0.4), _voice(0), (0.4, 0.0)),
    ({'overall_distance': 0, 'score': 9, 'overall': {'weighted_delta': 8}}, {'score': 0}, (0.0, 0.0)),
    ({'overall_distance': 2, 'score': 9}, {'score': 3}, (2.0, 3.0)),
])
def test_voice_distance_actual_field_and_legacy_numeric_precedence(before, after, expected):
    result = _diff(before_voice=before, after_voice=after)['voice']['overall_distance']
    assert (result['before'], result['after']) == expected
    packet = _evaluate(_packet('voice_distance_aggregate').to_dict(), before_voice=before, after_voice=after)
    assert (packet.before, packet.after) == expected
    assert packet.verdict == 'not_measurable'  # aggregate never entitles an improvement claim


def test_unavailable_packet_and_idiolect_produce_no_preservation_advice(tmp_path):
    packet = _unavailable(_packets(_packet('idiolect_preservation', {'phrases_preview': ['quiet harbor']})))
    revised = tmp_path / 'revised.txt'
    revised.write_text('The revised public text.', encoding='utf-8')
    result = tmp_path / 'report.json'
    assert bar.main(['--packet-json', _write(tmp_path/'packet.json', packet),
                     '--before-idiolect-json', _write(tmp_path/'idiolect.json', _unavailable(_idiolect())),
                     '--revised-text', str(revised), '--json-out', str(result)]) == 0
    payload = json.loads(result.read_text('utf-8'))['results']
    assert payload['verdicts'] == []
    assert payload['packet_summary'] is None
    assert payload['preservation_check'] is None


def test_robustness_final_card_and_markdown_keep_nonzero_evidence():
    base, changed = _variance(), _variance(-0.2)
    original = copy.deepcopy((base, changed))
    bare = arc.build_robustness_card(base=base['results'], fixtures=[('changed', changed['results'])])
    wrapped = arc.build_robustness_card(base=base, fixtures=[('changed', changed)])
    assert wrapped == bare
    assert arc.render_report(wrapped) == arc.render_report(bare)
    assert wrapped['per_signal']['burstiness_B']['base_value'] == -0.48
    assert (base, changed) == original


@pytest.mark.parametrize('unavailable_base', [False, True])
@pytest.mark.parametrize('bare', [False, True])
def test_robustness_partial_unavailable_values_stay_unknown(unavailable_base, bare):
    base, fixture = _variance()['results'], _variance(-0.2)['results']
    if unavailable_base:
        base = _unavailable(_variance(), bare)
    else:
        fixture = _unavailable(_variance(-0.2), bare)
    card = arc.build_robustness_card(base=base, fixtures=[('missing', fixture)])
    row = card['per_signal']['burstiness_B']
    assert row['overall_robustness'] == 'unknown'
    assert row['per_fixture']['missing']['relative_change'] is None
    assert row['per_fixture']['missing']['label'] == 'unknown'


def _drift(filename):
    body = json.loads((FIXTURES/filename).read_text('utf-8'))
    def pairs(rows):
        return {(r['period_a'], r['period_b']): r for r in rows}
    return json.loads(vdt.render_json(
        profiles={p['label']: vdt.PeriodProfile(p['label'], p['n_docs'], p['n_words'], [], {})
                  for p in body['periods']},
        family_distances={f: pairs(rows) for f, rows in body.get('cross_period_distances_per_family', {}).items()},
        weighted_distances=pairs(body['cross_period_distances_weighted']),
        drift=body['drift_scores'], dropped_periods=body.get('dropped_periods', []), inputs={},
        granularity=body['granularity'],
    ))


def _report_envelopes():
    return dict(
        voice_profile=voice_profile.build_audit_payload(json.loads((FIXTURES/'voice_profile.json').read_text('utf-8')), target_path='public-corpus'),
        voice_drift=_drift('voice_drift.json'), comparison_drift=_drift('control_drift.json'),
        idiolect_n1=_idiolect(json.loads((FIXTURES/'idiolect_n1.json').read_text('utf-8'))),
        idiolect_n2=_idiolect(json.loads((FIXTURES/'idiolect_n2.json').read_text('utf-8'))),
        idiolect_n3=_idiolect(json.loads((FIXTURES/'idiolect_n2.json').read_text('utf-8'))),
    )


def test_full_voice_report_real_builders_match_bare_with_sections_and_todos(tmp_path, capsys):
    envelopes = _report_envelopes()
    original = copy.deepcopy(envelopes)
    metadata = dict(author_name='Synthetic', corpus_label='public fixture')
    bare = gvr.ReportInputs(**{k: e['results'] for k, e in envelopes.items()}, **metadata)
    wrapped = gvr.ReportInputs(**envelopes, today=bare.today, **metadata)
    report = gvr.render_report(wrapped)
    assert report == gvr.render_report(bare)
    assert gvr.render_header(wrapped) == gvr.render_header(bare)
    assert '142,318 words' in report
    assert '0.272' in report
    assert 'Comparison to' in report
    assert '{TODO: interpret:' in report
    args = ['--author-name', 'Synthetic', '--corpus-label', 'public fixture']
    for name, envelope in envelopes.items():
        args += ['--'+name.replace('_', '-'), _write(tmp_path/(name+'.json'), envelope)]
    assert gvr.main(args) == 0
    assert capsys.readouterr().out == report
    assert envelopes == original


@pytest.mark.parametrize('bare', [False, True])
def test_voice_report_required_unavailable_profile_refuses_programmatically_and_cli(tmp_path, capsys, bare):
    profile = _unavailable(_report_envelopes()['voice_profile'], bare)
    with pytest.raises(ValueError, match='voice.profile.*unavailable'):
        gvr.ReportInputs(voice_profile=profile)
    target = tmp_path/'must-not-exist.md'
    assert gvr.main(['--voice-profile', _write(tmp_path/'profile.json', profile),
                     '--author-name', 'Synthetic', '--corpus-label', 'public fixture',
                     '--out', str(target), '--allow-public-output']) == 2
    captured = capsys.readouterr()
    assert 'unavailable' in captured.err
    assert captured.out == ''
    assert not target.exists()


def test_voice_report_all_optional_unavailable_inputs_are_absent():
    envelopes = _report_envelopes()
    profile = envelopes.pop('voice_profile')
    optional = {k: _unavailable(e) for k, e in envelopes.items()}
    assert gvr.render_report(gvr.ReportInputs(voice_profile=profile, **optional, today='2026-01-01')) == gvr.render_report(
        gvr.ReportInputs(voice_profile=profile['results'], today='2026-01-01'))


def test_normalized_voice_profile_still_obeys_output_privacy(tmp_path):
    profile = _report_envelopes()['voice_profile']
    target = tmp_path/'public-report.md'
    with pytest.raises(SystemExit) as exc:
        gvr.main(['--voice-profile', _write(tmp_path/'profile.json', profile), '--author-name', 'Synthetic',
                  '--corpus-label', 'public fixture', '--out', str(target)])
    assert exc.value.code == 2
    assert not target.exists()


def test_actual_variance_producer_cli_to_robustness_and_before_after_cli(tmp_path):
    text = tmp_path/'public-synthetic.txt'
    text.write_text(('We walked to the harbor. The afternoon wind moved the sails, and the gulls circled above us. '
                     'I waited. Then we opened the old gate and watched the sun fall behind the hills. ')*8, encoding='utf-8')
    env = os.environ.copy()
    # Explicit optional-backend absence in children: no parser/embedding/model loads.
    absence = tmp_path/'absence'
    absence.mkdir()
    (absence/'sitecustomize.py').write_text("import sys\nsys.modules['spacy'] = None\nsys.modules['sentence_transformers'] = None\n", encoding='utf-8')
    env['PYTHONPATH'] = str(absence) + os.pathsep + str(SCRIPTS)
    env['PYTHONUTF8'] = '1'
    env['HF_HUB_OFFLINE'] = '1'
    env['TRANSFORMERS_OFFLINE'] = '1'
    def cli(name, *args):
        result = subprocess.run([sys.executable, str(SCRIPTS/name), *map(str, args)], env=env,
                                capture_output=True, text=True, encoding='utf-8', timeout=90)
        assert result.returncode == 0, result.stderr
        return result.stdout
    produced = json.loads(cli('variance_audit.py', text, '--no-tier2', '--no-tier3', '--json'))
    mtld = produced['results']['audit']['tier1']['mtld']
    assert mtld > 0
    audit = _write(tmp_path/'audit.json', produced)
    card = json.loads(cli('adversarial_robustness_card.py', '--base', audit, '--fixture', 'same:'+audit, '--json'))
    assert card['results']['per_signal']['mtld']['base_value'] == mtld
    assert card['results']['per_signal']['mtld']['overall_robustness'] == 'stable'
    output = tmp_path/'diff.json'
    cli('before_after_restoration.py', '--before-variance-json', audit, '--after-variance-json', audit,
        '--diff-only', '--json-out', output, '--out', tmp_path/'diff.md')
    delta = json.loads(output.read_text('utf-8'))['results']['diff_only']['variance']['mtld']
    assert delta['before'] == delta['after'] == mtld
    assert delta['delta'] == 0
    assert '`mtld`' in (tmp_path/'diff.md').read_text('utf-8')
