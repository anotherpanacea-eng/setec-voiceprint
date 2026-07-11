#!/usr/bin/env python3
"""Tests for acquire_gmail_sent.py against a synthetic Takeout mbox.

Per internal/2026-07-10-acquire-gmail-sent-spec.md Tests section:
own-address + Sent-label conjunctive filtering (exact token, absent
header, custom-substring label), plain/HTML/wrapped quote trimming (no
correspondent PII survives), the HTML sibling gmail_attr DOM, forwarded
handling, the fail-closed unresolved drop vs the clean-reply keep,
auto-replied exclusion, ai_status-by-date, multi-Cc notes, no-Subject
fallback, the metadata-privacy grep, and the use/ai_status kwargs.
"""

from __future__ import annotations

import json
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import acquire_gmail_sent as G  # type: ignore
from test_data.acquisition_gmail_fixture import build_fixture as bf  # type: ignore

RAW_ADDRS = [bf.R_ALICE, bf.R_BOB, bf.R_CAROL, bf.WRAP_ADDR]
FORBIDDEN = [bf.QUOTE_SENTINEL, bf.FWD_BODY, bf.WRAP_ADDR, bf.WRAP_NAME, bf.SIG_TEXT]


@pytest.fixture
def mbox(tmp_path) -> Path:
    return bf.build_fixture(tmp_path / "sent.mbox")


def _out(tmp_path) -> Path:
    return tmp_path / "ai-prose-baselines-private" / "identity" / "personal_email" / "joshua"


def _run(mbox: Path, out: Path, extra=None) -> int:
    argv = [
        "--mbox-path", str(mbox), "--own-address", bf.OWN,
        "--output-dir", str(out), "--min-words-per-piece", "5",
        "--since", "2000-01-01", "--until", "2100-01-01",
    ]
    return G.main(argv + (extra or []))


def _entries(out: Path) -> list[dict]:
    mf = out / "draft_manifest.jsonl"
    return [json.loads(l) for l in mf.read_text().splitlines() if l.strip()]


def _txt(out: Path) -> str:
    return "\n".join(p.read_text() for p in out.glob("*.txt"))


def test_leak_sentinels_never_in_output(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    blob = _txt(out)
    blob += "\n".join(p.read_text() for p in out.glob("*.meta.json"))
    blob += (out / "draft_manifest.jsonl").read_text()
    for sentinel in FORBIDDEN:
        assert sentinel not in blob, f"{sentinel!r} leaked into output"


def test_html_sibling_gmail_attr_stripped(tmp_path, mbox):
    # The HTML reply's composed body survives; the gmail_attr sibling
    # (correspondent name + address) and the blockquote quote do not.
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    txt = _txt(out)
    assert "HTMLREPLY marker" in txt  # composed portion kept
    assert bf.WRAP_ADDR not in txt and bf.WRAP_NAME not in txt
    assert bf.QUOTE_SENTINEL not in txt


def test_attached_message_rfc822_body_is_not_acquired():
    outer = EmailMessage()
    outer.set_content("My short covering note stays in the acquired body.")
    attached = EmailMessage()
    attached["From"] = "third-party@example.invalid"
    attached["To"] = bf.OWN
    attached.set_content("THIRD_PARTY_ATTACHED_PROSE_MUST_NOT_APPEAR")
    outer.add_attachment(attached)

    body = G.extract_body(outer)
    assert "covering note" in body
    assert "THIRD_PARTY_ATTACHED_PROSE_MUST_NOT_APPEAR" not in body


def test_wrapped_attribution_addr_absent(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    txt = _txt(out)
    assert "WRAPPED" in txt
    assert bf.WRAP_ADDR not in txt


def test_metadata_privacy_no_raw_recipient_addr(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    meta = "\n".join(p.read_text() for p in out.glob("*.meta.json"))
    meta += (out / "draft_manifest.jsonl").read_text()
    for addr in [bf.R_ALICE, bf.R_BOB, bf.R_CAROL]:
        assert addr not in meta, f"raw recipient {addr} leaked to metadata"
    # notes use redacted recipient_NN labels.
    assert any("recipient_" in (e.get("notes") or "") for e in _entries(out))


def test_name_map_rejects_address_valued_alias(tmp_path, mbox, capsys):
    out = _out(tmp_path)
    name_map = tmp_path / "name-map.json"
    name_map.write_text(json.dumps({bf.R_ALICE: bf.R_ALICE}))

    assert _run(mbox, out, ["--name-map", str(name_map)]) == 2
    assert "contains a raw recipient address" in capsys.readouterr().err
    assert not out.exists()


def test_recipient_map_rejects_noncanonical_private_labels(tmp_path, mbox, capsys):
    out = _out(tmp_path)
    out.mkdir(parents=True)
    (out / "recipient_map.json").write_text(
        json.dumps({bf.R_ALICE: bf.R_ALICE})
    )

    assert _run(mbox, out) == 2
    assert "invalid recipient map" in capsys.readouterr().err
    assert not list(out.glob("*.txt"))
    assert not (out / "draft_manifest.jsonl").exists()


def test_conjunctive_sent_label_filter(tmp_path, mbox):
    # From matches but not-Sent / substring-'Sent' / no-header are all
    # excluded; nothing from those messages appears.
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    txt = _txt(out)
    assert "NOTSENT" not in txt   # labels: Important
    assert "SUBSTRLABEL" not in txt   # labels: "To be Sent" (substring token)
    assert "NOLABELS" not in txt   # no X-Gmail-Labels header (no crash)


def test_from_not_own_excluded(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    assert "Not from me" not in _txt(out)


def test_auto_replied_excluded(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    assert "AUTOREPLIED" not in _txt(out)  # Auto-Submitted: auto-replied


def test_forwarded_no_comment_dropped_but_with_comment_kept(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    txt = _txt(out)
    assert bf.FWD_BODY not in txt
    # forwarded-with-comment: the lead comment survives.
    assert "UNRESOLVED" not in txt  # unresolved reply dropped fail-closed
    assert "see below" in txt        # the with-comment lead survived


def test_signature_delimiter_trimmed(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    assert bf.SIG_TEXT not in _txt(out)


def test_manifest_fields_and_kwargs(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    for e in _entries(out):
        assert e["corpus_role"] == "identity_baseline"
        assert e["use"] == ["voice_profile"]
        assert e["consent_status"] == "author_consent"
        assert e["register"] == "personal"
        assert e["source"] == "gmail_takeout_local"
        assert e["acquired_via"].startswith("acquire_gmail_sent_")
        assert e["ai_status"] in {"pre_ai_human", "unknown"}


def test_ai_status_by_smart_compose_date(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    # The 2013 message is pre_ai_human; the 2020 messages are unknown.
    statuses = {}
    for e in _entries(out):
        d = e.get("date_written", "")
        statuses.setdefault(d[:4], set()).add(e["ai_status"])
    assert statuses.get("2013") == {"pre_ai_human"}, statuses
    assert statuses.get("2020") == {"unknown"}, statuses


def test_multi_cc_notes_counts_others(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    group = [e for e in _entries(out) if "MULTICC" in
             (out / (e["id"] + ".txt")).read_text()]
    assert group, "group-note piece not found"
    assert "+2 others" in (group[0].get("notes") or "")


def test_no_subject_falls_back_to_untitled(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    stems = [e["id"] for e in _entries(out)]
    assert any(s.endswith("untitled") for s in stems), stems


def test_unresolved_reply_drops_failclosed(tmp_path):
    # Direct unit test of the fail-closed contract for a headers-present
    # reply with a weak signal and no locatable boundary.
    res = G.trim_body(
        "my reply text\n\nsomeone wrote:\nquoted stuff",
        is_reply=True, own_sig_lines=None,
    )
    assert res.dropped and res.drop_reason == "quote_boundary_unresolved"


def test_clean_reply_kept(tmp_path):
    res = G.trim_body(
        "just my composed reply, no quote here at all",
        is_reply=True, own_sig_lines=None,
    )
    assert not res.dropped and res.kept_no_signal


def test_weak_signal_not_triggered_by_prose(tmp_path):
    # 'wrote:' mid-sentence must NOT count as a weak quote signal.
    assert not G._has_weak_quote_signal("as I wrote: the deadline is Friday")


def test_unwindowed_full_export_refuses_without_receipt(tmp_path, mbox):
    out = _out(tmp_path)
    with pytest.raises(SystemExit):
        G.main([
            "--mbox-path", str(mbox), "--own-address", bf.OWN,
            "--output-dir", str(out), "--min-words-per-piece", "5",
        ])


def test_live_smoke_confirmed_requires_tty(tmp_path, mbox):
    out = _out(tmp_path)
    rc = G.main([
        "--mbox-path", str(mbox), "--own-address", bf.OWN,
        "--output-dir", str(out), "--min-words-per-piece", "5",
        "--since", "2000-01-01", "--live-smoke-confirmed",
    ])
    assert rc == 2


def test_localized_sent_label_zero_output_fails_closed(tmp_path, mbox, capsys):
    out = _out(tmp_path)

    assert _run(
        mbox, out, ["--sent-label-token", "DefinitelyNotTheSentLabel"]
    ) == 1
    stderr = capsys.readouterr().err
    assert "matched --own-address" in stderr
    assert "may be localized" in stderr
    assert not out.exists()


def test_empty_date_window_uses_general_zero_output_error(tmp_path, mbox, capsys):
    out = _out(tmp_path)
    rc = G.main(
        [
            "--mbox-path", str(mbox),
            "--own-address", bf.OWN,
            "--output-dir", str(out),
            "--min-words-per-piece", "5",
            "--since", "2099-01-01",
            "--until", "2099-12-31",
        ]
    )

    assert rc == 1
    stderr = capsys.readouterr().err
    assert "no messages were acquired" in stderr
    assert "may be localized" not in stderr


def test_allow_empty_is_explicit_success(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(
        mbox,
        out,
        ["--sent-label-token", "DefinitelyNotTheSentLabel", "--allow-empty"],
    ) == 0


def test_allow_empty_cannot_mint_live_smoke_receipt(
    tmp_path, mbox, monkeypatch
):
    out = _out(tmp_path)
    monkeypatch.setattr(G.sys.stdin, "isatty", lambda: True)

    assert _run(
        mbox,
        out,
        [
            "--sent-label-token", "DefinitelyNotTheSentLabel",
            "--allow-empty",
            "--live-smoke-confirmed",
        ],
    ) == 0
    assert not (out / G.RECEIPT_NAME).exists()


def test_dedupe_only_rerun_is_success_without_manifest_growth(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    before = (out / "draft_manifest.jsonl").read_text()

    assert _run(mbox, out) == 0
    assert (out / "draft_manifest.jsonl").read_text() == before


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
