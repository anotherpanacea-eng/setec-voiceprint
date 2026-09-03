#!/usr/bin/env python3
"""Regression tests for fetch_mage.py.

Mirrors test_fetch_raid.py's strategy: mock huggingface_hub via
sys.modules injection. Verify file selection, license
verification, and CLI behavior.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CALIB_DIR = ROOT / "calibration"
if str(CALIB_DIR) not in sys.path:
    sys.path.insert(0, str(CALIB_DIR))

MAGE_PUBLIC_REVISION = "342663f0a2b775455c023f5d36a1341ff0ec5402"
MAGE_PUBLIC_FILES = [
    "train.csv",
    "valid.csv",
    "test.csv",
    "test_ood_set_gpt.csv",
    "test_ood_set_gpt_para.csv",
    "README.md",
]

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None


@pytest.fixture(autouse=True)
def _module_cleanup():
    """Snapshot sys.modules entries the tests overwrite, and
    restore them after each test."""
    saved = {
        name: sys.modules.get(name)
        for name in ("huggingface_hub", "fetch_mage")
    }
    try:
        yield
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def _install_mock_huggingface_hub(
    *,
    license_str: str = "mit",
    revision: str = "abcd1234" * 5,
    repo_files: list[str] | None = None,
    moving_main: bool = False,
):
    if repo_files is None:
        repo_files = [
            "data/train-00000-of-00001.parquet",
            "data/validation-00000-of-00001.parquet",
            "data/test-00000-of-00001.parquet",
            "README.md",
        ]

    fake_hub = types.ModuleType("huggingface_hub")
    fake_hub.calls = []
    resolved_revision = revision
    state = {"main_reads": 0}

    class _FakeCardData(dict):
        pass

    class _FakeDatasetInfo:
        def __init__(self, current_license, current_revision) -> None:
            self.card_data = _FakeCardData(license=current_license)
            self.tags = [f"license:{current_license}"]
            self.sha = current_revision

    class _FakeApi:
        def __init__(self, token=None):
            self.token = token

        def dataset_info(self, repo_id, revision=None):
            fake_hub.calls.append(("dataset_info", repo_id, revision))
            if revision is not None:
                return _FakeDatasetInfo(license_str, revision)
            state["main_reads"] += 1
            current_revision = (
                "moved-main" if moving_main and state["main_reads"] > 1
                else resolved_revision
            )
            return _FakeDatasetInfo(license_str, current_revision)

        def list_repo_files(
            self, repo_id, repo_type="dataset", revision=None,
        ):
            fake_hub.calls.append(
                ("list_repo_files", repo_id, revision)
            )
            if moving_main and revision is None:
                return ["moved-main.csv"]
            return list(repo_files)

    def _fake_hf_hub_download(
        *, repo_id, filename, repo_type, revision, local_dir, token,
    ):
        fake_hub.calls.append(
            ("hf_hub_download", repo_id, revision, filename)
        )
        out = Path(local_dir) / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00MAGE FAKE\x00")
        return str(out)

    fake_hub.HfApi = _FakeApi
    fake_hub.hf_hub_download = _fake_hf_hub_download
    sys.modules["huggingface_hub"] = fake_hub
    return fake_hub


def _import_fetch_mage():
    if "fetch_mage" in sys.modules:
        del sys.modules["fetch_mage"]
    import fetch_mage  # type: ignore
    return fetch_mage


# ---------- File selection ----------


class TestSelectFiles:
    def test_train_split(self):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        files = fm._select_files([
            "data/train-x.parquet",
            "data/validation-x.parquet",
            "data/test-x.parquet",
        ], "train")
        assert files == ["data/train-x.parquet"]

    def test_validation_synonym_val(self):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        files = fm._select_files([
            "data/val-x.parquet",
            "data/validation-x.parquet",
            "data/train-x.parquet",
        ], "validation")
        # Both val and validation should match.
        assert "data/val-x.parquet" in files
        assert "data/validation-x.parquet" in files
        assert "data/train-x.parquet" not in files

    def test_all_returns_every_parquet(self):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        files = fm._select_files([
            "data/train.parquet",
            "data/val.parquet",
            "data/test.parquet",
            "README.md",
        ], "all")
        assert len(files) == 3
        assert all(f.endswith(".parquet") for f in files)

    def test_unknown_split_raises(self):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        with pytest.raises(ValueError, match="Unknown split"):
            fm._select_files([], "completely_unknown")


# ---------- License verification ----------


class TestVerifyLicense:
    def test_mit_accepted(self):
        _install_mock_huggingface_hub(license_str="mit")
        fm = _import_fetch_mage()
        ok, observed = fm._verify_license(token=None, revision="pinned")
        assert ok is True
        assert "mit" in observed

    def test_apache_accepted(self):
        # Reviewer-noticed at fetch time: the HF dataset card
        # for MAGE declares Apache-2.0, not MIT as the paper
        # cites. Both are permissive — the fetcher accepts
        # either.
        _install_mock_huggingface_hub(license_str="apache-2.0")
        fm = _import_fetch_mage()
        ok, observed = fm._verify_license(token=None, revision="pinned")
        assert ok is True
        assert "apache" in observed

    def test_wrong_license_rejected(self):
        _install_mock_huggingface_hub(license_str="cc-by-nc-sa-4.0")
        fm = _import_fetch_mage()
        ok, _ = fm._verify_license(token=None, revision="pinned")
        assert ok is False

    def test_license_substring_lookalike_rejected(self):
        _install_mock_huggingface_hub(license_str="apache-2.0-ish")
        fm = _import_fetch_mage()
        ok, observed = fm._verify_license(
            token=None, revision="pinned",
        )
        assert ok is False
        assert observed == "apache-2.0-ish"


# ---------- CLI ----------


class TestCli:
    def test_cli_pins_metadata_listing_and_download(
        self, tmp_path, monkeypatch,
    ):
        hub = _install_mock_huggingface_hub(moving_main=True)
        fm = _import_fetch_mage()
        monkeypatch.setattr(fm, "TARGET_DIR", tmp_path)
        monkeypatch.setattr(fm, "REPO_ROOT", tmp_path.parent)

        rc = fm.main([])

        assert rc == 0
        revision = "abcd1234" * 5
        assert hub.calls[0] == ("dataset_info", "yaful/MAGE", None)
        assert ("dataset_info", "yaful/MAGE", revision) in hub.calls
        assert ("list_repo_files", "yaful/MAGE", revision) in hub.calls
        downloads = [call for call in hub.calls if call[0] == "hf_hub_download"]
        assert downloads
        assert all(call[2] == revision for call in downloads)

    def test_public_csv_metadata_selection(self):
        _install_mock_huggingface_hub(
            repo_files=MAGE_PUBLIC_FILES,
            revision=MAGE_PUBLIC_REVISION,
            license_str="apache-2.0",
        )
        fm = _import_fetch_mage()
        assert fm._select_files(
            MAGE_PUBLIC_FILES, "validation",
        ) == ["valid.csv"]
        assert fm._select_files(MAGE_PUBLIC_FILES, "all") == sorted(
            MAGE_PUBLIC_FILES[:-1]
        )

    def test_cli_dry_run(self, tmp_path, monkeypatch, capsys):
        hub = _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        monkeypatch.setattr(fm, "TARGET_DIR", tmp_path)
        rc = fm.main(["--dry-run"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        assert not any(call[0] == "hf_hub_download" for call in hub.calls)
        assert not (tmp_path / "NOTICE.md").exists()
        assert not (tmp_path / ".fetch_record.json").exists()

    def test_cli_full_flow_writes_notice_and_record(
        self, tmp_path, monkeypatch,
    ):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        monkeypatch.setattr(fm, "TARGET_DIR", tmp_path)
        monkeypatch.setattr(fm, "REPO_ROOT", tmp_path.parent)
        rc = fm.main([])  # default: all splits
        assert rc == 0
        notice = tmp_path / "NOTICE.md"
        record = tmp_path / ".fetch_record.json"
        assert notice.is_file()
        assert record.is_file()
        notice_text = notice.read_text(encoding="utf-8")
        assert "MAGE" in notice_text
        # NOTICE records pinned wrapper metadata and keeps content local-only.
        assert "Repository wrapper-license metadata" in notice_text
        assert "SETEC content posture:** local-only" in notice_text
        assert "public redistribution" not in notice_text
        assert "inherit the" not in notice_text
        record_data = json.loads(record.read_text(encoding="utf-8"))
        assert record_data["repo_id"] == "yaful/MAGE"
        assert record_data["split"] == "all"
        assert record_data["record_schema_version"] == 2
        assert record_data["observed_wrapper_license"] == "mit"
        assert record_data["license_check"] == "verified"
        assert record_data["content_posture"] == "local_only"

    def test_cli_skip_license_check_receipt_is_nonaffirmative(
        self, tmp_path, monkeypatch,
    ):
        _install_mock_huggingface_hub(license_str="some-other")
        fm = _import_fetch_mage()
        monkeypatch.setattr(fm, "TARGET_DIR", tmp_path)
        monkeypatch.setattr(fm, "REPO_ROOT", tmp_path.parent)

        rc = fm.main(["--skip-license-check"])

        assert rc == 0
        record_data = json.loads(
            (tmp_path / ".fetch_record.json").read_text(encoding="utf-8")
        )
        assert record_data["observed_wrapper_license"] is None
        assert record_data["license_check"] == "skipped"
        notice = (tmp_path / "NOTICE.md").read_text(encoding="utf-8")
        assert "check skipped; no license conclusion" in notice

    def test_cli_license_mismatch_returns_2(
        self, tmp_path, monkeypatch,
    ):
        # cc-by-nc-sa-4.0 is not in the accepted-license set
        # (MIT or Apache-2.0). The fetcher refuses the run.
        _install_mock_huggingface_hub(
            license_str="cc-by-nc-sa-4.0",
        )
        fm = _import_fetch_mage()
        monkeypatch.setattr(fm, "TARGET_DIR", tmp_path)
        rc = fm.main(["--dry-run"])
        assert rc == 2

    @pytest.mark.parametrize(
        ("target", "replacement", "expected"),
        [
            ("_resolve_revision", lambda token: "", 3),
            (
                "_verify_license",
                lambda token, revision: (_ for _ in ()).throw(
                    RuntimeError("metadata unavailable")
                ),
                2,
            ),
            (
                "_list_repo_files",
                lambda token, revision: (_ for _ in ()).throw(
                    RuntimeError("listing unavailable")
                ),
                3,
            ),
        ],
    )
    def test_cli_metadata_failures_write_no_receipt(
        self, tmp_path, monkeypatch, target, replacement, expected,
    ):
        _install_mock_huggingface_hub()
        fm = _import_fetch_mage()
        monkeypatch.setattr(fm, "TARGET_DIR", tmp_path)
        monkeypatch.setattr(fm, target, replacement)

        rc = fm.main([])

        assert rc == expected
        assert not (tmp_path / "NOTICE.md").exists()
        assert not (tmp_path / ".fetch_record.json").exists()


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
