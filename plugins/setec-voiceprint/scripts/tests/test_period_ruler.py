#!/usr/bin/env python3
"""Behavior tests for period_ruler (VP-RS1, Chronologic conformal ruler).

Model-free throughout: rows carry precomputed instrument scores, or a counting
stub scorer is injected. The real DeBERTa instruments are never loaded here.
"""

from __future__ import annotations

import io
import json
import random
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]

import period_ruler as pr  # type: ignore  # noqa: E402

_FORBIDDEN_KEYS = frozenset({
    "is_ai", "is_human", "verdict", "label", "class", "classification", "decision",
    "score", "confidence", "rank", "prediction", "flag", "selection", "best", "top", "selected",
})


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def _real_row(rng, i, *, year=None, n_words=None, length_effect=0.0):
    year = year if year is not None else rng.randint(1840, 1880)
    n_words = n_words if n_words is not None else rng.randint(20, 300)
    return {
        "id": f"r{i}", "year": year, "group": f"g{i % 12}", "text": _words(n_words) + f" {i}",
        # Instrument bias (-5 years) plus a length confound the ruler must cancel.
        "date_estimate_year": year - 5 + rng.gauss(0, 15) + length_effect * n_words,
        "p_synthetic": min(1.0, max(0.0, rng.betavariate(2, 6) + length_effect * n_words / 300)),
    }


def _reference(seed=0, n=1200, length_effect=0.0):
    rng = random.Random(seed)
    rows = [_real_row(rng, i, length_effect=length_effect) for i in range(n)]
    for r in rows:
        r["n_words"] = pr.count_words(r["text"])
    return rows


def _candidates(seed=1, n=80, *, shift_years=0.0, shift_p=0.0, n_words=None, length_effect=0.0):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        r = _real_row(rng, 10_000 + i, n_words=n_words, length_effect=length_effect)
        out.append({"id": f"c{i}", "year": r["year"], "text": r["text"],
                    "n_words": pr.count_words(r["text"]),
                    "date_estimate_year": r["date_estimate_year"] + shift_years,
                    "p_synthetic": min(1.0, r["p_synthetic"] + shift_p)})
    return out


def _run(cands, ref, **kw):
    kw.setdefault("n_null", 300)
    kw.setdefault("channels", ("date", "authenticity"))
    return pr.run_period_ruler(cands, ref, **kw)


class TestConformalMath:
    def test_ties_count_against_the_candidate(self):
        assert pr.q_strict(0.5, [0.5, 0.5, 0.5]) == 0.0
        assert pr.q_strict(0.6, [0.5, 0.5]) == 1.0
        assert pr.q_strict(0.6, []) is None

    def test_w1_is_zero_for_a_uniform_grid_and_half_at_a_point_mass(self):
        n = 1000
        assert pr.wasserstein1_uniform([(i + 0.5) / n for i in range(n)]) < 1e-3
        assert pr.wasserstein1_uniform([0.0] * 50) == pytest.approx(0.5)
        assert pr.wasserstein1_uniform([1.0] * 50) == pytest.approx(0.5)

    def test_fidelity_is_100_at_the_null_and_0_at_the_extreme(self):
        assert pr.period_fidelity(0.05, 0.05) == 100.0
        assert pr.period_fidelity(0.01, 0.05) == 100.0
        assert pr.period_fidelity(0.5, 0.05) == 0.0
        assert pr.authenticity_fidelity(1.0, 0.5) == 0.0
        assert pr.authenticity_fidelity(0.3, 0.5) == 100.0


class TestRuler:
    def test_real_like_set_sits_within_the_noise_floor(self):
        results, _ = _run(_candidates(), _reference())
        ch = results["period_ruler"]["channels"]
        assert ch["date"]["band"] == "within_reference_noise"
        assert ch["authenticity"]["band"] == "within_reference_noise"
        assert ch["date"]["period_fidelity"] > 80
        lo, hi = ch["date"]["null_period_fidelity_interval_95"]
        assert lo <= hi <= 100

    def test_anachronistic_and_synthetic_set_is_beyond_the_noise_floor(self):
        results, _ = _run(_candidates(shift_years=40, shift_p=0.4), _reference())
        ch = results["period_ruler"]["channels"]
        assert ch["date"]["band"] == "beyond_reference_noise"
        assert ch["authenticity"]["band"] == "beyond_reference_noise"
        assert ch["date"]["period_fidelity"] < 50
        assert ch["date"]["drift_years_bias_corrected"] == pytest.approx(40, abs=8)

    def test_instrument_bias_cancels(self):
        # The reference carries a -5 year instrument bias; real-like candidates
        # carry the same bias, so bias-corrected drift is near zero.
        results, _ = _run(_candidates(), _reference())
        assert abs(results["period_ruler"]["channels"]["date"]["drift_years_bias_corrected"]) < 5

    def test_length_binning_cancels_a_length_confounded_instrument(self):
        # Instruments read long passages as later and more synthetic. Real-like
        # candidates that are all long must not look anachronistic once the
        # comparison is length-matched; with a single length bin they would.
        ref = _reference(length_effect=0.1)
        cands = _candidates(n_words=290, length_effect=0.1)
        binned, _ = _run(cands, ref, n_length_bins=4)
        pooled, _ = _run(cands, ref, n_length_bins=1)
        assert binned["period_ruler"]["channels"]["date"]["band"] == "within_reference_noise"
        assert pooled["period_ruler"]["channels"]["date"]["band"] == "beyond_reference_noise"

    def test_same_seed_same_result(self):
        a, _ = _run(_candidates(), _reference(), seed=7)
        b, _ = _run(_candidates(), _reference(), seed=7)
        assert a == b

    def test_results_carry_no_verdict_or_selection_keys(self):
        results, _ = _run(_candidates(), _reference(), include_per_candidate=True)
        assert not (set(_walk_keys(results)) & _FORBIDDEN_KEYS)

    def test_single_group_reference_is_refused(self):
        ref = _reference()
        for r in ref:
            r["group"] = "only"
        with pytest.raises(pr.PeriodRulerError, match="two groups"):
            _run(_candidates(), ref)

    def test_candidates_outside_every_window_are_refused(self):
        cands = _candidates()
        for c in cands:
            c["year"] = 1990
        with pytest.raises(pr.PeriodRulerError, match="no candidate"):
            _run(cands, _reference())


class TestScoringAndResume:
    def test_cached_scores_are_reused_after_an_interrupted_run(self, tmp_path):
        calls = {"n": 0}

        def date_scorer(texts):
            calls["n"] += len(texts)
            return [1850.0 + (len(t) % 20) for t in texts]

        def auth_scorer(texts):
            return [((len(t) % 10) / 10) for t in texts]

        def strip(rows):
            return [{k: v for k, v in r.items() if k not in ("date_estimate_year", "p_synthetic")}
                    for r in rows]

        cache = tmp_path / "scores.jsonl"
        ref, cands = strip(_reference(n=200)), strip(_candidates(n=20))
        _run(cands, ref, date_scorer=date_scorer, auth_scorer=auth_scorer, cache_path=cache)
        first = calls["n"]
        assert first == 220
        _run(strip(_candidates(n=20)), strip(_reference(n=200)), date_scorer=date_scorer,
             auth_scorer=auth_scorer, cache_path=cache)
        assert calls["n"] == first


def _write_jsonl(path: Path, rows, year_key):
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            out = {k: v for k, v in r.items() if k not in ("n_words", "year")}
            out[year_key] = r["year"]
            fh.write(json.dumps(out) + "\n")


class TestCli:
    def test_model_free_cli_envelope(self, tmp_path):
        _write_jsonl(tmp_path / "c.jsonl", _candidates(), "target_year")
        _write_jsonl(tmp_path / "r.jsonl", _reference(), "year")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pr.main(["--candidates", str(tmp_path / "c.jsonl"), "--reference",
                          str(tmp_path / "r.jsonl"), "--n-null", "200", "--channels", "date,authenticity",
                          "--json"])
        env = json.loads(out.getvalue())
        assert rc == 0 and env["available"] is True
        assert env["task_surface"] == "period_register"
        assert "date" in env["results"]["period_ruler"]["channels"]

    def test_unscored_rows_without_torch_abstain(self, tmp_path, monkeypatch):
        rows = [{k: v for k, v in r.items() if k != "p_synthetic"} for r in _reference(n=100)]
        _write_jsonl(tmp_path / "c.jsonl", _candidates(n=5), "target_year")
        _write_jsonl(tmp_path / "r.jsonl", rows, "year")
        monkeypatch.setitem(sys.modules, "torch", None)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pr.main(["--candidates", str(tmp_path / "c.jsonl"), "--reference",
                          str(tmp_path / "r.jsonl"), "--channels", "date,authenticity", "--json"])
        env = json.loads(out.getvalue())
        assert rc == 3
        assert env["reason_category"] == "missing_dependency"

    def test_reference_without_groups_is_bad_input(self, tmp_path):
        _write_jsonl(tmp_path / "c.jsonl", _candidates(n=5), "target_year")
        rows = [{k: v for k, v in r.items() if k != "group"} for r in _reference(n=50)]
        _write_jsonl(tmp_path / "r.jsonl", rows, "year")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pr.main(["--candidates", str(tmp_path / "c.jsonl"), "--reference",
                          str(tmp_path / "r.jsonl"), "--json"])
        assert rc == 3
        assert json.loads(out.getvalue())["reason_category"] == "bad_input"

    def test_detached_launcher_runs(self, tmp_path):
        _write_jsonl(tmp_path / "c.jsonl", _candidates(n=20), "target_year")
        _write_jsonl(tmp_path / "r.jsonl", _reference(n=300), "year")
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "period_ruler.py"), "--candidates",
             str(tmp_path / "c.jsonl"), "--reference", str(tmp_path / "r.jsonl"),
             "--n-null", "100", "--channels", "date,authenticity", "--json"],
            cwd=tmp_path, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout)["tool"] == "period_ruler"


# ---------------------------------------------------------------------------
# Architecture band (p22 Score A)
# ---------------------------------------------------------------------------

_ARCH = ("clause_depth_weighted", "sentence_length_mean", "dependency_distance_mean", "hypotaxis_per_100_median")
_WORDS = [f"w{i}" for i in range(3000)]


def _arch_text(rng, n=600, loop=False, dialogue=False):
    if loop:
        return " ".join((["the", "old", "grey", "house"] * (n // 4 + 1))[:n])
    words = [rng.choice(_WORDS) for _ in range(n)]
    sents = [" ".join(words[i:i + 20]) + "." for i in range(0, n, 20)]
    if dialogue:
        sents = [f'"{x}"' if k % 2 == 0 else x for k, x in enumerate(sents)]
    return " ".join(sents)


def _arch_rows(seed, n, *, shift=0.0, groups=12, loop=False, kind="ref", dialogue=False):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        feats = {f: rng.gauss(10 + j, 1.0) + shift for j, f in enumerate(_ARCH)}
        row = {"id": f"{kind}{i}", "year": 1860, "text": _arch_text(rng, loop=loop, dialogue=dialogue),
               "architecture_features": feats}
        row["n_words"] = pr.count_words(row["text"])
        if kind == "ref":
            row["group"] = f"g{i % groups}"
        rows.append(row)
    return rows


def _arch(cands, ref, **kw):
    kw.setdefault("n_null", 300)
    results, _ = pr.run_period_ruler(cands, ref, channels=("architecture",), **kw)
    return results["period_ruler"]["channels"]["architecture"]


class TestArchitectureBand:
    def test_real_like_windows_sit_inside_the_band(self):
        block = _arch(_arch_rows(2, 60, kind="c"), _arch_rows(1, 600))
        assert block["band"] == "within_reference_noise"
        assert block["displacement_direction"] == "inside"
        assert 0.6 < block["in_band_share"] <= 1.0
        lo, hi = block["null_in_band_share_interval_95"]
        assert lo <= block["in_band_share"] <= 1.0

    def test_overshoot_reads_as_over_not_better(self):
        block = _arch(_arch_rows(2, 60, shift=2.5, kind="c"), _arch_rows(1, 600))
        assert block["band"] == "beyond_reference_noise"
        assert block["displacement_direction"] == "over"
        assert block["signed_displacement"] > 30
        assert block["in_band_share"] < 0.5

    def test_undershoot_reads_as_under(self):
        block = _arch(_arch_rows(2, 60, shift=-2.5, kind="c"), _arch_rows(1, 600))
        assert block["displacement_direction"] == "under"
        assert block["signed_displacement"] < -30

    def test_looping_windows_are_vetoed_and_read_beyond_noise(self):
        block = _arch(_arch_rows(2, 30, kind="c", loop=True), _arch_rows(1, 600))
        assert block["veto_rate"] == 1.0
        assert block["in_band_share"] is None
        assert block["band"] == "beyond_reference_noise"

    def test_mostly_degraded_arm_cannot_read_clean_by_being_vetoed(self):
        """Vetoed windows leave the in-band share, so 30 clean windows hiding 270
        looping ones once read within noise at 0.80 (Codex review of PR 818)."""
        cands = _arch_rows(2, 30, kind="c") + _arch_rows(3, 270, kind="l", loop=True)
        block = _arch(cands, _arch_rows(1, 600))
        assert block["veto_rate"] > 0.85
        assert block["band"] == "beyond_reference_noise"

    def test_a_looping_reference_source_cannot_make_vetoes_read_clean(self):
        """One reference source of refrain-laden windows lifted the null veto ceiling
        to 1.0, so 270 looping windows beside 30 clean ones read within noise while
        270 shifted windows read beyond (Codex re-review of PR 818). Vetoing an
        outlying window must never improve the reading."""
        rng = random.Random(9)
        ref = _arch_rows(1, 600)
        for k in range(10):
            row = _arch_rows(50 + k, 1)[0]
            row["text"] = (row["text"] + " the old grey house") * 1 + " the old grey house. the old grey house."
            row.update(id=f"refrain{k}", group="refrain")
            ref.append(row)
        clean = _arch_rows(2, 30, kind="c")
        shifted = _arch(clean + _arch_rows(3, 270, shift=4.0, kind="s"), ref)
        looped = _arch(clean + _arch_rows(3, 270, kind="l", loop=True), ref)
        assert shifted["band"] == looped["band"] == "beyond_reference_noise"
        assert looped["in_band_share_with_vetoes"] <= shifted["in_band_share_with_vetoes"] + 0.05

    def test_a_null_with_no_source_disjoint_reference_fails_closed(self):
        """Two sources in separate dialogue strata: every pseudo-model widens to the
        other source, so excluding all its sources leaves nothing to rank against.
        That once fell back to own-source ranks and read within noise."""
        ref = [{**r, "group": "A"} for r in _arch_rows(1, 60, dialogue=True)] + \
              [{**r, "id": f"b{i}", "group": "B"} for i, r in enumerate(_arch_rows(2, 60))]
        cands = _arch_rows(3, 20, kind="c", dialogue=True) + _arch_rows(4, 20, kind="d")
        with pytest.raises(pr.PeriodRulerError, match="source-disjoint"):
            _arch(cands, ref)

    def test_ratio_veto_switches_off_when_no_cut_meets_its_budget(self):
        """One author's vocabulary below every other author's cannot be cut at any
        tail; the ratio veto turns off and the achieved rate is reported."""
        rng = random.Random(4)
        rows = []
        for a, rich in enumerate((60, 400, 800, 1600)):
            for k in range(40):
                text = " ".join(" ".join(f"w{rng.randrange(rich)}" for _ in range(20)) + "." for _ in range(30))
                feats = {f: 10 + j + rng.gauss(0, 1.0) for j, f in enumerate(_ARCH)}
                rows.append({"id": f"a{a}k{k}", "year": 1860, "text": text, "n_words": 600,
                             "architecture_features": feats, "group": f"a{a}w{k % 2}", "author": f"a{a}"})
        block = _arch(_arch_rows(2, 30, kind="c"), rows)
        assert block["veto"]["distinct_token_ratio_tail"] is None
        assert block["veto"]["achieved_real_veto_rate"]["distinct_token_ratio"] == 0.0

    def test_per_feature_reading_names_the_axis_that_moved(self):
        cands = _arch_rows(2, 60, kind="c")
        for c in cands:
            c["architecture_features"]["sentence_length_mean"] += 4.0
        per = _arch(cands, _arch_rows(1, 600))["per_feature"]
        assert per["sentence_length_mean"]["signed_displacement"] > 40
        assert abs(per["clause_depth_weighted"]["signed_displacement"]) < 15

    def test_texts_are_cut_into_windows_and_featurized(self):
        def feature_fn(text):
            n = len(text.split())
            return {"sentence_length_mean": float(len(set(text.split())) % 17), "_sentence_lengths": [n % 40 + 5, 20, 30]}

        rng = random.Random(4)
        ref = [{"id": f"r{i}", "year": 1860, "group": f"g{i % 6}", "text": _arch_text(rng, 2500)} for i in range(60)]
        cands = [{"id": f"c{i}", "year": 1860, "text": _arch_text(rng, 2500)} for i in range(10)]
        for r in ref + cands:
            r["n_words"] = pr.count_words(r["text"])
        block = _arch(cands, ref, feature_fn=feature_fn)
        assert block["n_candidate_windows"] == 30  # 3 x 800 words each; the 100-word tail is dropped
        assert "sentence_length_w1_to_pool" in block["per_feature"]


def _authored_rows(seed, n_authors, *, offsets=None, works=3, per_work=10, prefix="a", kind="ref"):
    """Windows whose features vary mostly between authors, a little between works."""
    rng = random.Random(seed)
    rows = []
    for a in range(n_authors):
        off = offsets[a] if offsets else rng.gauss(0, 1.5)
        for w in range(works):
            for k in range(per_work):
                feats = {f: 10 + j + off + rng.gauss(0, 0.3) for j, f in enumerate(_ARCH)}
                row = {"id": f"{prefix}{a}w{w}k{k}", "year": 1860, "text": _arch_text(rng),
                       "architecture_features": feats}
                row["n_words"] = pr.count_words(row["text"])
                if kind == "ref":
                    row.update(group=f"{prefix}{a}w{w}", author=f"{prefix}{a}")
                rows.append(row)
    return rows


class TestAuthorBlocking:
    def test_loader_blocks_by_author_when_rows_name_one(self, tmp_path):
        path = tmp_path / "r.jsonl"
        path.write_text(json.dumps({"text": "x y z.", "year": 1860, "group": "w1", "author": "A"}) + "\n"
                        + json.dumps({"text": "x y z.", "year": 1860, "group": "w2"}) + "\n", encoding="utf-8")
        rows = pr.load_jsonl(path, year_field="year", kind="reference")
        assert [(r["group"], r["block"]) for r in rows] == [("w1", "A"), ("w2", "w2")]

    def test_unseen_authors_read_inside_when_the_null_matches_their_sources(self):
        """Real held-out authors must read as noise: each pseudo-model is drawn from as
        many reference authors as the arm has, so between-author spread is in the floor.
        Drawing every pseudo-model from all authors at once hid it (VP-RS1 qualification)."""
        ref = [{**r, "block": r["author"]} for r in _authored_rows(5, 16)]
        cands = [{**r, "block": f"new{r['id'][3]}"} for r in _authored_rows(6, 3, prefix="new", kind="cand")]
        matched = _arch(cands, ref)
        assert matched["null_sources_per_pseudo_model"] == 3
        assert matched["null_sources_from"] == "candidate_authors"
        assert matched["band"] == "within_reference_noise"
        # A pseudo-model drawn from every reference author at once has nothing
        # source-disjoint left to rank against, so it fails closed.
        unnamed = [{k: v for k, v in r.items() if k != "block"} for r in cands]
        with pytest.raises(pr.PeriodRulerError, match="source-disjoint"):
            _arch(unnamed, ref, null_blocks=16)

    def test_unseen_authors_keep_their_windows_through_the_veto(self):
        """Authors differ in vocabulary richness; a veto band fit on the reference
        itself dropped 16-23% of unseen real authors' windows and counted them as
        out of band (VP-RS1 qualification v2). Calibrated with each author left
        out, unseen authors lose few windows and vetoes never count as out of band."""
        rng = random.Random(11)
        vocab = [f"w{i}" for i in range(3000)]
        rows = []
        for a in range(25):
            rich = rng.randint(250, 1400)
            off = rng.gauss(0, 0.5)
            for w in range(3):
                for k in range(8):
                    text = " ".join(" ".join(rng.choice(vocab[:rich]) for _ in range(20)) + "." for _ in range(30))
                    feats = {f: 10 + j + off + rng.gauss(0, 0.3) for j, f in enumerate(_ARCH)}
                    rows.append({"id": f"a{a}w{w}k{k}", "year": 1860, "text": text, "n_words": 600,
                                 "architecture_features": feats, "group": f"a{a}w{w}",
                                 "author": f"a{a}", "block": f"a{a}"})
        held = {f"a{i}" for i in rng.sample(range(25), 6)}
        block = _arch([r for r in rows if r["author"] in held], [r for r in rows if r["author"] not in held])
        assert block["veto_rate"] <= 0.15
        assert block["band"] == "within_reference_noise"
        assert block["veto"]["distinct_token_ratio_tail"] < 0.05

    def test_an_arm_with_no_named_authors_is_read_as_one_voice(self):
        ref = [{**r, "block": r["author"]} for r in _authored_rows(5, 16)]
        block = _arch(_authored_rows(6, 1, offsets=[0.0], prefix="m", kind="cand"), ref)
        assert block["null_sources_per_pseudo_model"] == 1
        assert block["null_blocked_by"] == "author"


class TestSceneWindows:
    def test_windows_are_complete_and_inside_the_word_range(self):
        text = _arch_text(random.Random(0), 2500)
        wins = pr.split_scene_windows(text, 500, 800)
        assert all(500 <= len(w.split()) <= 800 for w in wins)
        assert all(w.endswith(".") for w in wins)  # never cut inside a sentence

    def test_paragraph_end_is_preferred_and_a_scene_break_closes_the_window(self):
        rng = random.Random(1)
        text = _arch_text(rng, 520) + "\n\n" + _arch_text(rng, 300) + "\n\n* * *\n\n" + _arch_text(rng, 560)
        wins = pr.split_scene_windows(text, 500, 800)
        assert [len(w.split()) for w in wins] == [520, 560]  # 300 words before the break are too short

    def test_an_overflowing_sentence_never_makes_a_window_past_the_maximum(self):
        """490 words then a 400-word sentence must not become an 890-word window (Codex review)."""
        text = _words(490) + ". " + _words(400) + ". " + _words(600) + "."
        wins = pr.split_scene_windows(text, 500, 800)
        assert wins and all(500 <= len(w.split()) <= 800 for w in wins)
        assert pr.split_scene_windows(_words(500) + ". " + _words(900) + ".", 500, 800) == [_words(500) + "."]

    def test_a_lone_short_text_is_one_window(self):
        assert len(pr.split_scene_windows(_words(300) + ".", 500, 800)) == 1

    def test_narrative_strata(self):
        st = pr.narrative_strata('"Come in," she said. I sat by my fire and we talked.')
        assert st["narrative_mode"] == "first_person"
        assert 0 < st["dialogue_share"] < 0.5

    def test_dialogue_heavy_windows_are_read_against_dialogue_heavy_real_windows(self):
        # Real dialogue windows have shorter sentences; a dialogue-heavy arm with
        # real-like values must not read as under-shifted against narration.
        ref = _arch_rows(1, 300, dialogue=True) + _arch_rows(3, 300, shift=3.0)
        for i, r in enumerate(ref):
            r["id"], r["group"] = f"r{i}", f"g{i % 12}"
        cands = _arch_rows(2, 60, kind="c", dialogue=True)
        block = _arch(cands, ref)
        assert block["candidate_match_levels"]["length_dialogue_mode"] == 60
        assert block["displacement_direction"] == "inside"
        assert abs(block["signed_displacement"]) < 12

    def test_energy_distance_sits_in_the_human_band_for_real_like_and_beyond_for_overshoot(self):
        ref = _arch_rows(1, 600)
        real_like = _arch(_arch_rows(2, 60, kind="c"), ref)
        over = _arch(_arch_rows(2, 60, shift=2.5, kind="c"), ref)
        assert real_like["energy_band"] == "within_human_vs_human"
        assert over["energy_band"] == "beyond_human_vs_human"
        assert over["energy_distance"] > real_like["energy_distance"]

    def test_architecture_is_primary_and_the_model_channels_secondary(self):
        ref = _reference()
        for r in ref:
            r["architecture_features"] = {"sentence_length_mean": random.Random(r["id"]).gauss(20, 3)}
        cands = _candidates()
        for c in cands:
            c["architecture_features"] = {"sentence_length_mean": random.Random(c["id"]).gauss(20, 3)}
        results, _ = pr.run_period_ruler(cands, ref, n_null=200)
        ch = results["period_ruler"]["channels"]
        assert ch["architecture"]["role"] == "primary"
        assert ch["date"]["role"] == ch["authenticity"]["role"] == "secondary_diagnostic"

    def test_results_carry_no_verdict_or_selection_keys(self):
        block = _arch(_arch_rows(2, 20, kind="c"), _arch_rows(1, 200), include_per_candidate=True)
        assert not (set(_walk_keys(block)) & _FORBIDDEN_KEYS)

    def test_cli_architecture_only_with_precomputed_features(self, tmp_path):
        def dump(path, rows, year_key):
            with path.open("w", encoding="utf-8") as fh:
                for r in rows:
                    out = {k: v for k, v in r.items() if k not in ("n_words", "year")}
                    out[year_key] = r["year"]
                    fh.write(json.dumps(out) + "\n")

        dump(tmp_path / "c.jsonl", _arch_rows(2, 20, kind="c"), "target_year")
        dump(tmp_path / "r.jsonl", _arch_rows(1, 200), "year")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pr.main(["--candidates", str(tmp_path / "c.jsonl"), "--reference", str(tmp_path / "r.jsonl"),
                          "--channels", "architecture", "--n-null", "200", "--json"])
        env = json.loads(out.getvalue())
        assert rc == 0
        assert set(env["results"]["period_ruler"]["channels"]) == {"architecture"}

    def test_cli_abstains_without_a_parser(self, tmp_path, monkeypatch):
        _write_jsonl(tmp_path / "c.jsonl", _candidates(n=5), "target_year")
        _write_jsonl(tmp_path / "r.jsonl", _reference(n=50), "year")
        monkeypatch.setattr(pr, "_load_feature_fn", lambda: None)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = pr.main(["--candidates", str(tmp_path / "c.jsonl"), "--reference", str(tmp_path / "r.jsonl"),
                          "--json"])
        assert rc == 3
        assert json.loads(out.getvalue())["reason_category"] == "missing_dependency"


_HAS_PARSER = pr._load_feature_fn() is not None


@pytest.mark.skipif(not _HAS_PARSER, reason="spaCy en_core_web_sm not installed")
def test_real_parser_features_are_finite():
    text = ("When the old man, who had lived there forty years, came at last to the door, "
            "he stood; and the rain fell. It was late. ") * 20
    feats = pr._load_feature_fn()(text)
    assert feats["sentence_length_mean"] > 0
    assert feats["clauses_per_sentence"] > 0
