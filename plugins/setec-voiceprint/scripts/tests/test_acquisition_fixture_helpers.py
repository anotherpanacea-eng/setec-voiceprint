"""Regression coverage for acquisition fixture isolation and live bindings."""
import importlib
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("name", ["courtlistener", "govinfo_chrg", "openalex_core", "pdf_urls"])
def test_fetcher_wrappers_preserve_lazy_defaults_and_live_bindings(monkeypatch, tmp_path, name):
    module = importlib.import_module(f"test_acquire_{name}")
    defaults = {"fixture": "default.txt"}
    calls = []

    def default_map():
        calls.append("default")
        return defaults

    monkeypatch.setattr(module, "fixture_url_map", default_map)
    monkeypatch.setattr(module, "FIXTURE_DIR", tmp_path)
    monkeypatch.setattr(module, "ac", SimpleNamespace(FixtureFetcher=lambda **kwargs: SimpleNamespace(**kwargs)), raising=False)
    first = module.make_fetcher()
    assert calls == ["default"]
    assert first.url_map == defaults and first.url_map is not defaults
    assert first.fixture_dir == tmp_path
    assert first.rate_limit_seconds == 0.0 and first.respect_robots is False
    defaults["fixture"] = "changed.txt"
    assert first.url_map == {"fixture": "default.txt"}

    monkeypatch.setattr(module, "FIXTURE_DIR", tmp_path / "later")
    marker = object()
    monkeypatch.setattr(module, "ac", SimpleNamespace(FixtureFetcher=lambda **kwargs: (marker, kwargs)), raising=False)
    second_marker, second = module.make_fetcher({})
    assert second_marker is marker
    assert calls == ["default"]
    assert second["fixture_dir"] == tmp_path / "later"
    assert second["url_map"] == {}

    supplied = {"fixture": "supplied.txt"}
    _, third = module.make_fetcher(supplied)
    supplied.clear()
    assert third["url_map"] == {"fixture": "supplied.txt"}
    assert calls == ["default"]
