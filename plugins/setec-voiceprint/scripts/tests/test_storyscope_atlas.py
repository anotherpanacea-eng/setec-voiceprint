#!/usr/bin/env python3
"""Tests for setec.calibration.storyscope_atlas.

Model-free and network-free. They protect the run's safety boundaries (the
spend ceiling refuses before any SDK is touched; non-public-domain works are
refused), the consumer contract (emitted keyed manifests are accepted by the
spec-79 long-form audit unchanged), and the answer validation and agreement
arithmetic the pilot report depends on.
"""

from __future__ import annotations

import json
import sys
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
