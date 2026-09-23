#!/usr/bin/env python3
"""Tests for setec.calibration.storyscope_atlas.

Model-free and network-free. They protect the run's safety boundaries (the
spend ceiling refuses before any SDK is touched; non-public-domain works are
refused; the headless transport pins model, effort and prompt and stops on a
usage limit), the consumer contract (emitted keyed manifests are accepted by the
spec-79 long-form audit unchanged), and the answer validation and agreement
arithmetic the pilot report depends on.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

import narrative_decision_long_form as ndlf  # type: ignore
import narrative_feature_schema as nfs  # type: ignore
from setec.calibration import storyscope_atlas as sa


def _taxonomy() -> dict:
    """A minimal stand-in for the StoryScope taxonomy: every adopted id,
    each a three-option single-select, one multi-select."""
    feats = []
    for a in sa.load_atlas_schema()["adopted"]:
        feats.append({"id": a["id"], "name": a["id"], "type": "categorical",
                      "values": ["x", "y", "z"], "question": f"q {a['id']}"})
    feats[0]["type"] = "multi_select"
    return {"feature_taxonomy": {"d": {"aspects": {"a": {"features": feats}}}}}


def _novel(n_chapters: int = 7, paras: int = 22, words: int = 200) -> str:
    """Synthetic public-domain-shaped text: CHAPTER headings, a tiny
    table-of-contents run at the front, quoted dialogue, blank-line
    paragraphs."""
    toc = "\n".join(f"CHAPTER {i}" for i in range(1, n_chapters + 1))
    chapters = []
    for c in range(n_chapters):
        body = []
        for p in range(paras):
            w = " ".join(f"c{c}p{p}w{i}" for i in range(words))
            body.append(f'{w}. "Speech {c} {p} here," said one.')
        chapters.append(f"CHAPTER {c + 1}\n\n" + "\n\n".join(body))
    return toc + "\n\n" + "\n\n".join(chapters) + "\n"


def _pg_wrap(body: str, title: str = "A Test Novel") -> str:
    return (f"Title: {title}\nAuthor: Nobody\n\n"
            f"*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\n{body}"
            "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***\n")


@pytest.fixture()
def run(tmp_path):
    body, meta = sa.strip_gutenberg(_pg_wrap(_novel()))
    sa._add_work(tmp_path, "pg1", body, meta, "gutenberg:1")
    tax = tmp_path / "taxonomy.json"
    tax.write_text(json.dumps(_taxonomy()), encoding="utf-8")
    rc = sa.main(["plan", "--run", str(tmp_path), "--taxonomy", str(tax),
                  "--sample-per-work", "99", "--gold-per-work", "3"])
    assert rc == 0
    return tmp_path


# ---------- ingestion boundary ---------------------------------------

def test_gutenberg_wrapper_removed_and_metadata_read():
    body, meta = sa.strip_gutenberg(_pg_wrap("Once upon a time.\n", "Hard Times"))
    assert body == "Once upon a time.\n"
    assert meta == {"title": "Hard Times", "author": "Nobody"}


def test_crlf_gutenberg_text_plans_after_fetch(tmp_path):
    # Reproduces the Code-PC pilot refusal: Gutenberg serves CRLF, and the
    # saved text must hash the same when plan reads it back.
    raw = _pg_wrap(_novel()).replace("\n", "\r\n")
    body, meta = sa.strip_gutenberg(raw)
    assert "\r" not in body
    sa._add_work(tmp_path, "pg1", body, meta, "gutenberg:1")
    tax = tmp_path / "taxonomy.json"
    tax.write_text(json.dumps(_taxonomy()), encoding="utf-8")
    assert sa.main(["plan", "--run", str(tmp_path), "--taxonomy", str(tax)]) == 0


def test_truncated_gutenberg_download_refused():
    raw = _pg_wrap("text\n").split("*** END")[0]
    with pytest.raises(sa.AtlasError):
        sa.strip_gutenberg(raw)


def test_plan_refuses_work_outside_public_domain_boundary(tmp_path):
    sa._add_work(tmp_path, "mine", _novel(), {"title": "t"}, "private-corpus")
    tax = tmp_path / "taxonomy.json"
    tax.write_text(json.dumps(_taxonomy()), encoding="utf-8")
    assert sa.main(["plan", "--run", str(tmp_path), "--taxonomy", str(tax)]) == 2
    assert not (tmp_path / "plan.json").exists()


def test_add_local_requires_attestation(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text(_novel(), encoding="utf-8")
    assert sa.main(["add-local", "--run", str(tmp_path), "--file", str(f),
                    "--work-id", "x"]) == 2
    assert not (tmp_path / "works.json").exists()


def test_taxonomy_missing_an_adopted_id_refuses():
    tax = _taxonomy()
    feats = tax["feature_taxonomy"]["d"]["aspects"]["a"]["features"]
    feats.pop()
    with pytest.raises(sa.AtlasError):
        sa.build_features(tax)


# ---------- plan ------------------------------------------------------

def test_table_of_contents_folds_into_first_chapter():
    chapters = sa.split_chapters(_novel(n_chapters=7))
    assert len(chapters) == 7
    assert all(c["n_words"] >= sa.MIN_CHAPTER_WORDS for c in chapters)


@pytest.mark.parametrize("n,k", [(1, 6), (5, 6), (23, 6), (40, 4), (10, 1)])
def test_sample_positions_spread_and_bounded(n, k):
    got = sa.sample_positions(n, k)
    assert got == sorted(set(got))
    assert len(got) == min(n, k)
    assert all(0 <= i < n for i in got)
    if n > 1 and k > 1:
        assert got[0] == 0 and got[-1] == n - 1


def test_gold_segments_are_a_subset_of_the_sonnet_sample(run):
    plan = json.loads((run / "plan.json").read_text())
    for w in plan["works"]:
        assert set(w["gold"]) <= set(w["sample"])


# ---------- spend ceiling ---------------------------------------------

def test_submit_over_ceiling_refuses_before_any_network(run, monkeypatch):
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    monkeypatch.setitem(sys.modules, "anthropic", None)  # any import would raise
    assert sa.main(["submit", "--run", str(run), "--step", "features",
                    "--max-usd", "0.0001"]) == 2
    assert not (run / "state.json").exists()


def test_submit_refuses_edited_request_file(run, monkeypatch):
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    p = run / "requests" / "features.jsonl"
    p.write_text(p.read_text() + "\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    assert sa.main(["submit", "--run", str(run), "--step", "features",
                    "--max-usd", "100"]) == 2


def test_committed_spend_counts_uncollected_ceilings(run):
    (run / "state.json").write_text(json.dumps(
        {"steps": {"cards": {"ceiling_usd": 4.5, "collected": False}}}))
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    with pytest.raises(sa.AtlasError):
        sa.check_budget(run, "features", 4.5)


# ---------- emit: consumer contract -----------------------------------

def _answer(feats, seed: int) -> dict:
    vals = {}
    for f in feats:
        if f.scope != "segment":
            continue
        opt = f.options[seed % len(f.options)]
        vals[f.id] = [opt] if f.kind == "multi" else opt
    return vals


def _fake_results(run: Path, step: str, *, flip: set[str] = frozenset()) -> None:
    """Result rows in the shape `collect` writes."""
    plan = json.loads((run / "plan.json").read_text())
    feats = sa._plan_features(plan)
    rows = []
    for r in sa._read_jsonl(run / "requests" / f"{step}.jsonl"):
        seed = r["unit"]["segment"]
        vals = _answer(feats, seed)
        for fid in flip:
            f = next(x for x in feats if x.id == fid)
            vals[fid] = f.options[(seed + 1) % len(f.options)]
        rows.append({"custom_id": r["custom_id"], "unit": r["unit"], "type": "succeeded",
                     "model": r["params"]["model"], "stop_reason": "end_turn",
                     "text": "```json\n" + json.dumps({"values": vals}) + "\n```",
                     "usage": {"input_tokens": 1000, "output_tokens": 500,
                               "cache_creation_input_tokens": 0,
                               "cache_read_input_tokens": 0}})
    sa._write_jsonl(run / "results" / f"{step}.jsonl", rows)


def test_emitted_manifest_is_accepted_by_the_long_form_audit(run, tmp_path):
    plan = json.loads((run / "plan.json").read_text())
    assert plan["works"][0]["segmentation"]["n_segments"] >= 3
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    _fake_results(run, "features")
    assert sa.main(["emit", "--run", str(run)]) == 0
    manifest = run / "out" / "manifest-core-features.json"
    keyed = json.loads(manifest.read_text())
    assert set(next(iter(keyed.values()))["values"]) == {f.key for f in nfs.CORE_FEATURES}
    out = tmp_path / "env.json"
    rc = ndlf.main([str(run / "works" / "pg1.txt"), "--judge", "manifest",
                    "--judge-manifest", str(manifest), "--out", str(out)])
    env = json.loads(out.read_text())
    assert rc == 0, env.get("warnings")
    assert env["available"] is True


def test_out_of_vocabulary_answers_become_missing():
    feats = [sa.Feature("a", "single", ("x", "y"), "q", "segment", "new"),
             sa.Feature("b", "multi", ("p", "q"), "q", "segment", "new"),
             sa.Feature("c", "single", ("1", "2"), "q", "segment", "taxonomy")]
    vals, warns = sa.validate_values({"a": "maybe", "b": ["p", "zzz"], "c": 2}, feats)
    assert vals == {"a": None, "b": None, "c": "2"}
    assert len(warns) == 2


def test_agreement_separates_stable_from_unstable_features(run):
    for step in ("features", "gold"):
        assert sa.main(["build", "--run", str(run), "--step", step]) == 0
    plan = json.loads((run / "plan.json").read_text())
    unstable = "nv_social_strata"
    _fake_results(run, "features")
    _fake_results(run, "gold", flip={unstable})
    rows = sa.agreement(
        {r["unit"]["content_sha256"]: sa.validate_values(
            sa.extract_json(r["text"])["values"], sa._plan_features(plan))[0]
         for r in sa._read_jsonl(run / "results" / "features.jsonl")},
        {r["unit"]["content_sha256"]: sa.validate_values(
            sa.extract_json(r["text"])["values"], sa._plan_features(plan))[0]
         for r in sa._read_jsonl(run / "results" / "gold.jsonl")},
        sa._plan_features(plan), min_pairs=1)
    by = {r["feature_id"]: r for r in rows}
    assert by[unstable]["percent_agreement"] == 0.0
    assert by["thematic_explicitness_and_moralizing"]["percent_agreement"] == 1.0


def test_cohen_kappa_known_values():
    assert sa.cohen_kappa(list("aabb"), list("aabb")) == 1.0
    assert sa.cohen_kappa(list("abab"), list("baba")) == -1.0
    # po = 0.5, pe = 0.5  ->  kappa 0
    assert sa.cohen_kappa(list("aabb"), list("abab")) == 0.0
    assert sa.cohen_kappa([], []) is None


def test_actual_cost_uses_batch_and_cache_prices():
    rows = [{"model": "claude-sonnet-5", "usage": {
        "input_tokens": 1_000_000, "output_tokens": 100_000,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 1_000_000}}]
    # (2.00 + 0.20 cache read + 1.00 output) * 0.5 batch
    assert sa.actual_cost(rows)["usd"] == pytest.approx(1.6)


def test_constant_labels_do_not_claim_perfect_chance_corrected_agreement():
    assert sa.cohen_kappa(["absent"] * 8, ["absent"] * 8) is None


def test_budget_reserves_maximum_output_and_model_override(run):
    assert sa.main(["build", "--run", str(run), "--step", "features",
                    "--model", "claude-opus-5-5"]) == 0
    meta = sa._verified_meta(run, "features")
    output_only = meta["n_requests"] * sa.MAX_TOKENS * 20 / 1e6 * sa.BATCH_DISCOUNT
    assert meta["estimate"]["model"] == "claude-opus-5-5"
    assert meta["estimate"]["ceiling_usd"] > output_only
    with pytest.raises(sa.AtlasError):
        sa.check_budget(run, "features", output_only)


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), -1.0])
def test_invalid_budget_cannot_bypass_refusal(run, limit):
    with pytest.raises(sa.AtlasError):
        sa.check_budget(run, "features", limit)


def test_changed_work_cannot_be_judged_under_old_segment_hashes(run):
    path = run / "works" / "pg1.txt"
    path.write_bytes(path.read_bytes().replace(b"Speech", b"Altered"))
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 2
    assert not (run / "requests" / "features.jsonl").exists()


def test_rebuild_cannot_relabel_existing_results(run):
    args = ["build", "--run", str(run), "--step", "features"]
    assert sa.main(args) == 0
    _fake_results(run, "features")
    before = (run / "requests" / "features.meta.json").read_bytes()
    assert sa.main(args + ["--model", "claude-opus-5-5"]) == 2
    assert (run / "requests" / "features.meta.json").read_bytes() == before


def test_changed_plan_refuses_emit_and_transport(run):
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    _fake_results(run, "features")
    plan = sa._read_json(run / "plan.json")
    plan["features"][0]["options"] = ["different"]
    sa._write_json(run / "plan.json", plan)
    assert sa.main(["emit", "--run", str(run)]) == 2
    with pytest.raises(sa.AtlasError):
        sa.check_budget(run, "features", 100)


def test_replan_refuses_after_requests_built(run):
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    before = (run / "plan.json").read_bytes()
    assert sa.main(["plan", "--run", str(run), "--taxonomy", str(run / "taxonomy.json")]) == 2
    assert (run / "plan.json").read_bytes() == before


def test_emit_retires_previous_manifests_and_agreement(run):
    for step in ("features", "gold"):
        assert sa.main(["build", "--run", str(run), "--step", step]) == 0
        _fake_results(run, step)
    assert sa.main(["emit", "--run", str(run)]) == 0
    assert sa._read_json(run / "out" / "manifest-core-features.json")
    assert sa._read_json(run / "out" / "agreement.json")
    rows = sa._read_jsonl(run / "results" / "features.jsonl")
    for row in rows:
        row["text"] = "{}"
    sa._write_jsonl(run / "results" / "features.jsonl", rows)
    (run / "results" / "gold.jsonl").unlink()
    assert sa.main(["emit", "--run", str(run)]) == 0
    assert sa._read_json(run / "out" / "manifest-core-features.json") == {}
    assert sa._read_json(run / "out" / "manifest-core-gold.json") == {}
    assert sa._read_json(run / "out" / "agreement.json") == []


@pytest.mark.parametrize("mutation", ["unit", "unknown", "duplicate", "model", "transport"])
def test_emit_rejects_results_rebound_to_other_requests(run, mutation):
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    _fake_results(run, "features")
    rows = sa._read_jsonl(run / "results" / "features.jsonl")
    if mutation == "unit":
        rows[0]["unit"]["content_sha256"] = "f" * 64
    elif mutation == "unknown":
        rows[0]["custom_id"] = "unknown"
    elif mutation == "duplicate":
        rows.append(rows[0])
    elif mutation == "model":
        rows[0]["model"] = "claude-opus-5-5"
    else:
        rows[0]["transport"] = sa.HEADLESS
    sa._write_jsonl(run / "results" / "features.jsonl", rows)
    assert sa.main(["emit", "--run", str(run)]) == 2
    assert not (run / "out" / "manifest-core-features.json").exists()


# ---------- headless transport (subscription) ------------------------

_FAKE_CLAUDE = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    if sys.argv[1:] == ["--version"]:
        print("9.9.9 (Claude Code)")
        sys.exit(0)
    prompt = sys.stdin.read()
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({{"argv": sys.argv[1:], "stdin": prompt}}) + "\\n")
    model = sys.argv[sys.argv.index("--model") + 1]
    if os.environ.get("FAKE_MODE") == "limit":
        print(json.dumps({{"type": "result", "subtype": "error_during_execution",
                          "is_error": True, "api_error_status": 429,
                          "result": "Claude usage limit reached"}}))
        sys.exit(1)
    print(json.dumps({{
        "type": "result", "subtype": "success", "is_error": False,
        "stop_reason": "end_turn", "total_cost_usd": 0.01,
        "result": open(os.environ["FAKE_ANSWER"], encoding="utf-8").read(),
        "usage": {{"input_tokens": 3, "output_tokens": 40,
                   "cache_creation_input_tokens": 900, "cache_read_input_tokens": 0}},
        "modelUsage": {{
            "claude-haiku-4-5-20251001": {{"outputTokens": 10, "canonicalModel": "claude-haiku-4-5"}},
            model: {{"outputTokens": 40, "canonicalModel": model}}}}}}))
""")


@pytest.fixture()
def fake_claude(run, tmp_path_factory, monkeypatch):
    d = tmp_path_factory.mktemp("bin")
    exe = d / "claude"
    exe.write_text(_FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    plan = json.loads((run / "plan.json").read_text())
    answer = d / "answer.txt"
    answer.write_text(json.dumps({"values": _answer(sa._plan_features(plan), 0)}), encoding="utf-8")
    log = d / "log.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_ANSWER", str(answer))
    return exe, log


def _calls(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


@pytest.mark.skipif(os.name == "nt", reason="the stand-in executable is a POSIX script")
@pytest.mark.parametrize("relative", [False, True])
def test_headless_pins_model_effort_and_prompt_and_resumes(run, fake_claude, monkeypatch, relative):
    exe, log = fake_claude
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    reqs = sa._read_jsonl(run / "requests" / "features.jsonl")
    monkeypatch.chdir(run.parent)
    args = ["headless", "--run", run.name if relative else str(run), "--step", "features", "--claude", str(exe)]
    assert sa.main(args) == 0

    calls = _calls(log)
    assert len(calls) == len(reqs)
    assert sorted(c["stdin"] for c in calls) == sorted(r["params"]["messages"][0]["content"] for r in reqs)
    system = reqs[0]["params"]["system"][0]["text"]
    for c in calls:
        a = c["argv"]
        assert a[a.index("--model") + 1] == "claude-sonnet-5"
        assert a[a.index("--effort") + 1] == "low"
        assert a[a.index("--tools") + 1] == ""
        assert "--safe-mode" in a
        prompt_file = Path(a[a.index("--system-prompt-file") + 1])
        assert prompt_file.is_absolute()
        assert prompt_file.read_text(encoding="utf-8") == system

    assert sa.main(args) == 0  # everything succeeded, so a rerun calls nothing
    assert len(_calls(log)) == len(reqs)

    assert sa.main(["emit", "--run", str(run)]) == 0
    keyed = json.loads((run / "out" / "manifest-core-features.json").read_text())
    ident = next(iter(keyed.values()))["judge_identity"]
    pv = json.loads((run / "requests" / "features.meta.json").read_text())["prompt_version"]
    assert ident == {"model": "claude-sonnet-5", "model_revision": "claude-sonnet-5",
                     "prompt_version": f"{pv};claude-code/9.9.9 (Claude Code)"}
    cost = json.loads((run / "out" / "cost.json").read_text())
    assert cost["total_usd"] == 0
    assert cost["subscription_list_usd"] == pytest.approx(0.01 * len(reqs))


@pytest.mark.skipif(os.name == "nt", reason="the stand-in executable is a POSIX script")
def test_headless_stops_on_usage_limit_and_resumes_later(run, fake_claude, monkeypatch):
    exe, log = fake_claude
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    n = len(sa._read_jsonl(run / "requests" / "features.jsonl"))
    assert n > 3
    args = ["headless", "--run", str(run), "--step", "features", "--claude", str(exe),
            "--parallel", "1"]
    monkeypatch.setenv("FAKE_MODE", "limit")
    assert sa.main(args) == 2
    assert len(_calls(log)) < n
    monkeypatch.delenv("FAKE_MODE")
    assert sa.main(args) == 0
    rows = sa._read_jsonl(run / "results" / "features.jsonl")
    assert len(rows) == n and all(r["type"] == "succeeded" for r in rows)


def test_headless_refuses_a_step_already_sent_as_a_batch(run):
    assert sa.main(["build", "--run", str(run), "--step", "features"]) == 0
    (run / "state.json").write_text(json.dumps(
        {"steps": {"features": {"batch_id": "b", "ceiling_usd": 1.0, "collected": False}}}))
    assert sa.main(["headless", "--run", str(run), "--step", "features"]) == 2


def test_headless_answer_from_a_fallback_model_is_an_error():
    out = json.dumps({"subtype": "success", "is_error": False, "result": "{}",
                      "usage": {}, "modelUsage": {"claude-opus-5": {"canonicalModel": "claude-opus-5"}}})
    row = sa.parse_headless(out, "claude-opus-5-5")
    assert row["type"] == "errored" and "claude-opus-5" in row["error"]


@pytest.mark.parametrize("output", [[], None, {"subtype": "success", "modelUsage": [1]}])
def test_malformed_headless_output_is_a_retryable_error(output):
    assert sa.parse_headless(json.dumps(output), "claude-sonnet-5")["type"] == "errored"


@pytest.mark.parametrize("field,value", [("usage", []), ("modelUsage", []),
    ("total_cost_usd", "1"), ("total_cost_usd", float("inf")), ("total_cost_usd", -1)])
def test_malformed_headless_accounting_is_a_retryable_error(field, value):
    output = {"subtype": "success", "result": "{}", "usage": {},
              "modelUsage": {"claude-sonnet-5": {}}, "total_cost_usd": 0}
    output[field] = value
    assert sa.parse_headless(json.dumps(output), "claude-sonnet-5")["type"] == "errored"


def test_work_judging_refuses_incomplete_chapter_cards(run):
    assert sa.main(["build", "--run", str(run), "--step", "cards"]) == 0
    req = sa._read_jsonl(run / "requests" / "cards.jsonl")[0]
    sa._write_jsonl(run / "results" / "cards.jsonl", [{
        "custom_id": req["custom_id"], "unit": req["unit"], "type": "succeeded",
        "model": req["params"]["model"], "text": "{\"card\": {}}",
    }])
    assert sa.main(["build", "--run", str(run), "--step", "works"]) == 2
    assert not (run / "requests" / "works.jsonl").exists()
