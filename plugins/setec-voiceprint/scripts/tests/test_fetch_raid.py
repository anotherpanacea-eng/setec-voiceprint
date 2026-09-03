#!/usr/bin/env python3
"""Regression tests for fetch_raid.py.

Strategy: mock huggingface_hub via sys.modules injection so tests
don't hit HF and don't require the dependency to be installed.
Verify file selection, license verification, and CLI behavior.
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

RAID_PUBLIC_REVISION = "865cac74188466cb0c3b7574a10204007b57a459"
RAID_PUBLIC_FILES = [
    "train.csv", "test.csv", "extra.csv", "README.md",
]

try:
    import pytest  # type: ignore
except ImportError:  # pragma: no cover
    pytest = None


@pytest.fixture(autouse=True)
def _module_cleanup():
    """Snapshot sys.modules entries the tests overwrite, and
    restore them after each test. Prevents the mocked
    `huggingface_hub` from leaking into downstream tests."""
    saved = {
        name: sys.modules.get(name)
        for name in ("huggingface_hub", "fetch_raid")
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
    license_str: str = "apache-2.0",
    revision: str = "deadbeef" * 5,
    repo_files: list[str] | None = None,
    download_writes: dict[str, bytes] | None = None,
    moving_main: bool = False,
) -> mock.MagicMock:
    """Inject a fake `huggingface_hub` module into sys.modules.
    Returns the mock for assertions."""
    if repo_files is None:
        repo_files = [
            "data/train-00000-of-00010.parquet",
            "data/train_paraphrase-00000-of-00001.parquet",
            "data/test-00000-of-00001.parquet",
            "data/extra-00000-of-00001.parquet",
            "data/extra_homoglyph-00000-of-00001.parquet",
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
                return ["data/moved-main.csv"]
            return list(repo_files)

    def _fake_hf_hub_download(
        *, repo_id, filename, repo_type, revision, local_dir, token,
    ):
        fake_hub.calls.append(
            ("hf_hub_download", repo_id, revision, filename)
        )
        out = Path(local_dir) / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        content = (
            download_writes.get(filename, b"\x00FAKE PARQUET\x00")
            if download_writes else b"\x00FAKE PARQUET\x00"
        )
        out.write_bytes(content)
        return str(out)

    fake_hub.HfApi = _FakeApi
    fake_hub.hf_hub_download = _fake_hf_hub_download
    sys.modules["huggingface_hub"] = fake_hub
    return fake_hub


# ---------- Module reload helper ----------


def _import_fetch_raid():
    """Force re-import of fetch_raid so module-level state picks
    up the fresh sys.modules mock."""
    if "fetch_raid" in sys.modules:
        del sys.modules["fetch_raid"]
    import fetch_raid  # type: ignore
    return fetch_raid


# ---------- Token loading ----------


class TestLoadToken:
    def test_token_from_env_var(self, monkeypatch):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        monkeypatch.setenv("HF_TOKEN", "literal-token")
        args = type("A", (), {"token": None})()
        assert fr._load_token(args) == "literal-token"

    def test_token_from_file(self, tmp_path):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        token_file = tmp_path / "tok.txt"
        token_file.write_text("file-token\n", encoding="utf-8")
        args = type("A", (), {"token": str(token_file)})()
        assert fr._load_token(args) == "file-token"

    def test_token_from_env_var_name(self, monkeypatch):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        monkeypatch.setenv("MY_HF_VAR", "indirect-token")
        args = type("A", (), {"token": "MY_HF_VAR"})()
        assert fr._load_token(args) == "indirect-token"


# ---------- File selection ----------


class TestSelectFiles:
    def test_train_subset_returns_train_files(self):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        repo_files = [
            "data/train-00000-of-00010.parquet",
            "data/train_paraphrase-00000-of-00001.parquet",
            "data/test-00000-of-00001.parquet",
            "data/extra-00000-of-00001.parquet",
        ]
        files = fr._select_files(
            repo_files, "train", include_adversarial=True,
        )
        assert "data/train-00000-of-00010.parquet" in files
        assert "data/train_paraphrase-00000-of-00001.parquet" in files
        assert "data/test-00000-of-00001.parquet" not in files
        assert "data/extra-00000-of-00001.parquet" not in files

    def test_no_adversarial_filters_attack_variants(self):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        repo_files = [
            "data/train-00000.parquet",
            "data/train_paraphrase-00000.parquet",
            "data/train_homoglyph-00000.parquet",
            "data/train_misspelling-00000.parquet",
        ]
        files = fr._select_files(
            repo_files, "train", include_adversarial=False,
        )
        assert "data/train-00000.parquet" in files
        assert "data/train_paraphrase-00000.parquet" not in files
        assert "data/train_homoglyph-00000.parquet" not in files
        assert "data/train_misspelling-00000.parquet" not in files

    def test_all_subset_returns_train_test_extra(self):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        repo_files = [
            "data/train-x.parquet",
            "data/test-x.parquet",
            "data/extra-x.parquet",
            "README.md",
        ]
        files = fr._select_files(
            repo_files, "all", include_adversarial=True,
        )
        assert len(files) == 3
        assert all(f.endswith(".parquet") for f in files)

    def test_unknown_subset_raises(self):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        with pytest.raises(ValueError, match="Unknown subset"):
            fr._select_files(
                [], "completely_unknown",
                include_adversarial=True,
            )

    def test_non_parquet_files_excluded(self):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        repo_files = [
            "data/train-x.parquet",
            "data/train-x.json",
            "README.md",
            ".gitattributes",
        ]
        files = fr._select_files(
            repo_files, "train", include_adversarial=True,
        )
        assert files == ["data/train-x.parquet"]


# ---------- Adversarial detection ----------


class TestIsAdversarialFile:
    def test_paraphrase_recognized(self):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        assert fr._is_adversarial_file(
            "data/train_paraphrase-00000.parquet"
        ) is True

    def test_homoglyph_recognized(self):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        assert fr._is_adversarial_file(
            "data/train_homoglyph-00000.parquet"
        ) is True

    def test_base_file_not_adversarial(self):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        assert fr._is_adversarial_file(
            "data/train-00000.parquet"
        ) is False


# ---------- License verification ----------


class TestVerifyLicense:
    def test_apache_license_accepted(self):
        _install_mock_huggingface_hub(license_str="apache-2.0")
        fr = _import_fetch_raid()
        ok, observed = fr._verify_license(token=None, revision="pinned")
        assert ok is True
        assert "apache" in observed

    def test_mit_license_accepted(self):
        # Reviewer-noticed at fetch time: the HF dataset card
        # for RAID declares MIT, not Apache-2.0 as the paper
        # cites. Both are permissive — the fetcher accepts
        # either.
        _install_mock_huggingface_hub(license_str="mit")
        fr = _import_fetch_raid()
        ok, observed = fr._verify_license(token=None, revision="pinned")
        assert ok is True
        assert "mit" in observed

    def test_wrong_license_rejected(self):
        _install_mock_huggingface_hub(license_str="cc-by-nc-sa-4.0")
        fr = _import_fetch_raid()
        ok, observed = fr._verify_license(token=None, revision="pinned")
        assert ok is False

    def test_license_substring_lookalike_rejected(self):
        _install_mock_huggingface_hub(license_str="not-mit")
        fr = _import_fetch_raid()
        ok, observed = fr._verify_license(
            token=None, revision="pinned",
        )
        assert ok is False
        assert observed == "not-mit"


# ---------- CLI ----------


class TestCli:
    def test_cli_pins_metadata_listing_and_download(
        self, tmp_path, monkeypatch,
    ):
        hub = _install_mock_huggingface_hub(moving_main=True)
        fr = _import_fetch_raid()
        monkeypatch.setattr(fr, "TARGET_DIR", tmp_path)
        monkeypatch.setattr(fr, "REPO_ROOT", tmp_path.parent)

        rc = fr.main(["--subset", "train"])

        assert rc == 0
        revision = "deadbeef" * 5
        assert hub.calls[0] == ("dataset_info", "liamdugan/raid", None)
        assert ("dataset_info", "liamdugan/raid", revision) in hub.calls
        assert ("list_repo_files", "liamdugan/raid", revision) in hub.calls
        downloads = [call for call in hub.calls if call[0] == "hf_hub_download"]
        assert downloads
        assert all(call[2] == revision for call in downloads)

    def test_cli_dry_run(self, tmp_path, monkeypatch, capsys):
        hub = _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        # Redirect TARGET_DIR to tmp so we don't touch the real
        # private dir.
        monkeypatch.setattr(fr, "TARGET_DIR", tmp_path)
        rc = fr.main([
            "--subset", "train", "--dry-run",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out
        assert not any(call[0] == "hf_hub_download" for call in hub.calls)
        assert not (tmp_path / "NOTICE.md").exists()
        assert not (tmp_path / ".fetch_record.json").exists()

    def test_cli_dry_run_no_adversarial_filters(
        self, tmp_path, monkeypatch, capsys,
    ):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        monkeypatch.setattr(fr, "TARGET_DIR", tmp_path)
        rc = fr.main([
            "--subset", "train",
            "--no-adversarial",
            "--dry-run",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        # Default mock includes train + train_paraphrase. With
        # --no-adversarial the paraphrase variant should be
        # filtered out.
        assert "train-00000-of-00010.parquet" in out
        assert "train_paraphrase" not in out

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_cli_no_adversarial_rejects_monolithic_public_csv(
        self, tmp_path, monkeypatch, capsys, dry_run,
    ):
        hub = _install_mock_huggingface_hub(
            repo_files=RAID_PUBLIC_FILES,
            revision=RAID_PUBLIC_REVISION,
            license_str="mit",
        )
        fr = _import_fetch_raid()
        monkeypatch.setattr(fr, "TARGET_DIR", tmp_path)
        argv = ["--subset", "train", "--no-adversarial"]
        if dry_run:
            argv.append("--dry-run")

        rc = fr.main(argv)

        assert rc == 4
        err = capsys.readouterr().err
        assert "co-locate attack and non-attack rows" in err
        assert "raid_to_manifest.py --no-adversarial" in err
        assert not any(call[0] == "hf_hub_download" for call in hub.calls)
        assert not (tmp_path / "NOTICE.md").exists()
        assert not (tmp_path / ".fetch_record.json").exists()

    def test_cli_license_mismatch_returns_2(
        self, tmp_path, monkeypatch,
    ):
        _install_mock_huggingface_hub(license_str="cc-by-nc-sa-4.0")
        fr = _import_fetch_raid()
        monkeypatch.setattr(fr, "TARGET_DIR", tmp_path)
        rc = fr.main(["--subset", "train", "--dry-run"])
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
        fr = _import_fetch_raid()
        monkeypatch.setattr(fr, "TARGET_DIR", tmp_path)
        monkeypatch.setattr(fr, target, replacement)

        rc = fr.main(["--subset", "train"])

        assert rc == expected
        assert not (tmp_path / "NOTICE.md").exists()
        assert not (tmp_path / ".fetch_record.json").exists()

    def test_cli_skip_license_check_proceeds(
        self, tmp_path, monkeypatch,
    ):
        _install_mock_huggingface_hub(license_str="some-other")
        fr = _import_fetch_raid()
        monkeypatch.setattr(fr, "TARGET_DIR", tmp_path)
        rc = fr.main([
            "--subset", "train",
            "--skip-license-check",
            "--dry-run",
        ])
        assert rc == 0

    def test_cli_full_flow_writes_notice_and_record(
        self, tmp_path, monkeypatch,
    ):
        _install_mock_huggingface_hub()
        fr = _import_fetch_raid()
        monkeypatch.setattr(fr, "TARGET_DIR", tmp_path)
        monkeypatch.setattr(fr, "REPO_ROOT", tmp_path.parent)
        rc = fr.main(["--subset", "train"])
        assert rc == 0
        notice = tmp_path / "NOTICE.md"
        record = tmp_path / ".fetch_record.json"
        assert notice.is_file()
        assert record.is_file()
        # Notice carries the right paper + license claim.
        notice_text = notice.read_text(encoding="utf-8")
        assert "RAID" in notice_text
        # NOTICE now records the observed license string from
        # the pinned HF wrapper metadata and keeps content local-only.
        assert "Repository wrapper-license metadata" in notice_text
        assert "SETEC content posture:** local-only" in notice_text
        assert "public redistribution" not in notice_text
        assert "inherit the" not in notice_text
        # Record carries revision SHA + subset + adversarial flag.
        record_data = json.loads(record.read_text(encoding="utf-8"))
        assert record_data["repo_id"] == "liamdugan/raid"
        assert record_data["subset"] == "train"
        assert record_data["include_adversarial"] is True
        assert record_data["record_schema_version"] == 2
        assert record_data["observed_wrapper_license"] == "apache-2.0"
        assert record_data["license_check"] == "verified"
        assert record_data["content_posture"] == "local_only"

    def test_cli_skip_license_check_receipt_is_nonaffirmative(
        self, tmp_path, monkeypatch,
    ):
        _install_mock_huggingface_hub(license_str="some-other")
        fr = _import_fetch_raid()
        monkeypatch.setattr(fr, "TARGET_DIR", tmp_path)
        monkeypatch.setattr(fr, "REPO_ROOT", tmp_path.parent)

        rc = fr.main([
            "--subset", "train", "--skip-license-check",
        ])

        assert rc == 0
        record_data = json.loads(
            (tmp_path / ".fetch_record.json").read_text(encoding="utf-8")
        )
        assert record_data["observed_wrapper_license"] is None
        assert record_data["license_check"] == "skipped"
        notice = (tmp_path / "NOTICE.md").read_text(encoding="utf-8")
        assert "check skipped; no license conclusion" in notice

    def test_cli_huggingface_hub_missing(self, monkeypatch, capsys):
        # Force the import-check branch.
        if "huggingface_hub" in sys.modules:
            monkeypatch.delitem(sys.modules, "huggingface_hub")
        if "fetch_raid" in sys.modules:
            del sys.modules["fetch_raid"]
        # Block the import by inserting a sentinel that raises.
        sys.modules["huggingface_hub"] = None  # type: ignore
        try:
            import fetch_raid  # type: ignore
            rc = fetch_raid.main(["--subset", "train"])
            assert rc == 1
            err = capsys.readouterr().err
            assert "huggingface_hub" in err
        finally:
            sys.modules.pop("huggingface_hub", None)


if __name__ == "__main__":
    if pytest is None:
        sys.stderr.write("pytest not installed; cannot run tests.\n")
        sys.exit(2)
    sys.exit(pytest.main([__file__, "-v"]))
