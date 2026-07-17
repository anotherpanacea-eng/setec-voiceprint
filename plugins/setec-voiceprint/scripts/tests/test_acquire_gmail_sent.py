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
from email import message_from_bytes
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


def _process_flowed_message(
    tmp_path: Path,
    body_lines: list[str],
    *,
    is_reply: bool,
) -> G.PreparedMessage:
    headers = [
        f"From: {bf.OWN}",
        "To: visible-recipient@example.invalid",
        "X-Gmail-Labels: Sent",
        "Date: Fri, 17 Jul 2026 12:00:00 -0400",
        "Subject: Flowed semantic boundary",
        "Message-ID: <flowed-boundary@example.invalid>",
        "Content-Type: text/plain; charset=utf-8; format=flowed",
        "Content-Transfer-Encoding: 8bit",
    ]
    if is_reply:
        headers.insert(6, "In-Reply-To: <parent@example.invalid>")
    raw = (
        "\r\n".join(headers)
        + "\r\n\r\n"
        + "\r\n".join(body_lines)
        + "\r\n"
    ).encode("utf-8")
    message = message_from_bytes(raw)
    out = _out(tmp_path)
    args = G.build_arg_parser().parse_args([
        "--mbox-path", str(tmp_path / "unused.mbox"),
        "--own-address", bf.OWN,
        "--output-dir", str(out),
        "--min-words-per-piece", "1",
        "--since", "2000-01-01",
        "--until", "2100-01-01",
    ])
    opts = G.parse_options(args)
    recipients = G.ac.StableRedactionMap(
        opts.recipient_map_path,
        label_prefix="recipient",
        normalize_key=lambda address: address.strip().lower(),
        reuse_gaps=False,
        map_name="recipient map",
    )
    prepared = G.process_message(message, opts, recipients, G.Summary())
    assert prepared is not None
    return prepared


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


def test_crlf_is_canonicalized_before_format_flowed_unwrap():
    message = message_from_bytes(
        b"Content-Type: text/plain; charset=utf-8; format=flowed\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"\r\n"
        b"A soft wrapped line \r\n"
        b"continues here.\r\n"
        b"\r\n"
        b"A genuine paragraph remains.\r\n"
    )
    body = G.extract_body(message)
    assert body == (
        "A soft wrapped line continues here.\n\n"
        "A genuine paragraph remains.\n"
    )
    assert "\r" not in body


def test_format_flowed_delsp_yes_removes_soft_break_space():
    message = message_from_bytes(
        b"Content-Type: text/plain; charset=utf-8; format=flowed; DelSp=yes\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"\r\n"
        b"A compound soft- \r\n"
        b"wrapped word.\r\n"
    )
    assert G.extract_body(message) == "A compound soft-wrapped word.\n"


def test_format_flowed_removes_space_stuffing_and_joins_equal_quote_depth():
    assert G._unwrap_flowed(" From sender\n  indented\n") == (
        "From sender\n indented\n"
    )
    assert G._unwrap_flowed("> quoted soft \n> continuation\n") == (
        ">quoted soft continuation\n"
    )


def test_format_flowed_never_joins_authored_text_to_quoted_reply():
    message = message_from_bytes(
        b"Content-Type: text/plain; charset=utf-8; format=flowed\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"In-Reply-To: <parent@example.test>\r\n"
        b"\r\n"
        b"My authored reply ends here \r\n"
        b"> THIRD_PARTY_QUOTE_MUST_NOT_APPEAR\r\n"
    )
    body = G.extract_body(message)
    trimmed = G.trim_body(body, is_reply=True, own_sig_lines=None)
    assert trimmed.kept == "My authored reply ends here"
    assert "THIRD_PARTY_QUOTE_MUST_NOT_APPEAR" not in trimmed.kept


def test_format_flowed_never_joins_authored_text_to_reply_attribution():
    message = message_from_bytes(
        b"Content-Type: text/plain; charset=utf-8; format=flowed\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"In-Reply-To: <parent@example.test>\r\n"
        b"\r\n"
        b"My authored reply ends here \r\n"
        b"On Tue, Example Sender wrote:\r\n"
        b"> THIRD_PARTY_QUOTE_MUST_NOT_APPEAR\r\n"
    )
    body = G.extract_body(message)
    trimmed = G.trim_body(body, is_reply=True, own_sig_lines=None)
    assert trimmed.kept == "My authored reply ends here"
    assert "Example Sender" not in trimmed.kept
    assert "THIRD_PARTY_QUOTE_MUST_NOT_APPEAR" not in trimmed.kept


def test_format_flowed_quoted_signature_separator_is_never_joined():
    assert G._unwrap_flowed("> quoted flowed \n> -- \n> signature\n") == (
        ">quoted flowed \n>-- \n>signature\n"
    )


@pytest.mark.parametrize(
    ("boundary_lines", "is_reply"),
    (
        (
            [
                "-----Original Message-----",
                "From: THIRD_PARTY_HEADER_NAME <third.party@example.invalid>",
                "Subject: THIRD_PARTY_HEADER_SUBJECT",
                "",
                "THIRD_PARTY_PROSE_MUST_NOT_APPEAR",
            ],
            True,
        ),
        (
            [
                "---------- Forwarded message ---------",
                "From: THIRD_PARTY_HEADER_NAME <third.party@example.invalid>",
                "Subject: THIRD_PARTY_HEADER_SUBJECT",
                "",
                "THIRD_PARTY_PROSE_MUST_NOT_APPEAR",
            ],
            False,
        ),
        (
            [
                "On Fri, Jul 17, 2026, THIRD_PARTY_HEADER_NAME ",
                "<third.party@example.invalid> ",
                "wrote:",
                "> From: THIRD_PARTY_HEADER_NAME <third.party@example.invalid>",
                "> Subject: THIRD_PARTY_HEADER_SUBJECT",
                "> THIRD_PARTY_PROSE_MUST_NOT_APPEAR",
            ],
            True,
        ),
    ),
    ids=("original-message", "forward", "wrapped-attribution"),
)
def test_process_message_flowed_boundaries_exclude_third_party_content(
    tmp_path, boundary_lines, is_reply,
):
    prepared = _process_flowed_message(
        tmp_path,
        [
            "MY_AUTHORED_PROSE remains safely in this cleaned message ",
            *boundary_lines,
        ],
        is_reply=is_reply,
    )
    cleaned = prepared.piece.cleaned_text
    assert "MY_AUTHORED_PROSE" in cleaned
    assert "THIRD_PARTY_HEADER_NAME" not in cleaned
    assert "THIRD_PARTY_HEADER_SUBJECT" not in cleaned
    assert "third.party@example.invalid" not in cleaned
    assert "THIRD_PARTY_PROSE_MUST_NOT_APPEAR" not in cleaned


def test_flowed_space_stuffed_authored_gt_preserves_quote_provenance(tmp_path):
    message = message_from_bytes(
        b"Content-Type: text/plain; charset=utf-8; format=flowed\r\n"
        b"Content-Transfer-Encoding: 8bit\r\n"
        b"In-Reply-To: <parent@example.test>\r\n"
        b"\r\n"
        b" > authored comparison remains mine\r\n"
        b"> THIRD_PARTY_QUOTE_MUST_NOT_APPEAR\r\n"
    )
    extracted = G._extract_body_details(message)
    assert extracted.text.startswith("> authored comparison remains mine\n")
    assert extracted.line_provenance is not None
    assert extracted.line_provenance[0].quote_depth == 0
    assert extracted.line_provenance[0].space_stuffed
    assert extracted.line_provenance[0].authored_literal_gt
    assert extracted.line_provenance[1].quote_depth == 1

    trimmed = G.trim_body(
        extracted.text,
        is_reply=True,
        own_sig_lines=None,
        line_provenance=extracted.line_provenance,
    )
    assert trimmed.kept == "> authored comparison remains mine"
    assert "THIRD_PARTY_QUOTE_MUST_NOT_APPEAR" not in trimmed.kept

    public_body = G.extract_body(message)
    assert isinstance(public_body, str)
    assert public_body == extracted.text
    assert json.loads(json.dumps({"body": public_body})) == {
        "body": str(public_body),
    }
    with pytest.raises(AttributeError, match="immutable"):
        public_body.line_provenance = ()
    direct_trimmed = G.trim_body(
        public_body,
        is_reply=True,
        own_sig_lines=None,
    )
    assert direct_trimmed.kept == "> authored comparison remains mine"
    assert "THIRD_PARTY_QUOTE_MUST_NOT_APPEAR" not in direct_trimmed.kept

    prepared = _process_flowed_message(
        tmp_path,
        [
            " > authored comparison remains mine",
            "> THIRD_PARTY_QUOTE_MUST_NOT_APPEAR",
        ],
        is_reply=True,
    )
    assert prepared.piece.cleaned_text == "> authored comparison remains mine"


@pytest.mark.parametrize(
    ("authored_line", "is_reply"),
    (
        (" > I wrote:", True),
        (" > On Friday I wrote:", False),
    ),
    ids=("reply-terminal", "nonreply-attribution-shaped"),
)
def test_process_message_keeps_space_stuffed_authored_attribution_shapes(
    tmp_path, authored_line, is_reply,
):
    prepared = _process_flowed_message(
        tmp_path,
        [authored_line, "MY AUTHORED CONTINUATION remains in this message"],
        is_reply=is_reply,
    )
    cleaned = prepared.piece.cleaned_text
    assert authored_line[1:] in cleaned
    assert "MY AUTHORED CONTINUATION" in cleaned


@pytest.mark.parametrize(
    ("body_lines", "expected"),
    (
        (
            [
                "My authored lead ",
                " > I wrote:",
                "MY AUTHORED CONTINUATION remains here",
                "> THIRD_PARTY_QUOTE_MUST_NOT_APPEAR",
            ],
            "My authored lead > I wrote:",
        ),
        (
            [
                " > On Friday ",
                "I wrote:",
                "MY AUTHORED CONTINUATION remains here",
                "> THIRD_PARTY_QUOTE_MUST_NOT_APPEAR",
            ],
            "> On Friday I wrote:",
        ),
    ),
    ids=("literal-gt-continuation", "literal-gt-flow-start"),
)
def test_process_message_preserves_literal_gt_across_flowed_joins(
    tmp_path, body_lines, expected,
):
    prepared = _process_flowed_message(
        tmp_path, body_lines, is_reply=True,
    )
    cleaned = prepared.piece.cleaned_text
    assert expected in cleaned
    assert "MY AUTHORED CONTINUATION" in cleaned
    assert "THIRD_PARTY_QUOTE_MUST_NOT_APPEAR" not in cleaned


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


def test_private_thread_and_entry_locators_are_structural_and_nonreversible():
    msg = G.email.message_from_string(
        "Message-ID: <mine@example.com>\n"
        "References: <root@example.com> <parent@example.com>\n\nbody"
    )
    thread, entry = G.private_message_locators(msg)
    assert thread and entry and thread.startswith("sha256:") and entry.startswith("sha256:")
    assert thread != entry
    assert "root@example.com" not in thread and "mine@example.com" not in entry
    no_ids = G.email.message_from_string("Subject: no ids\n\nbody")
    assert G.private_message_locators(no_ids) == (None, None)


def test_global_thread_roots_join_irt_only_reply_chain():
    inbound = G.email.message_from_string(
        "Message-ID: <root@example.invalid>\n\nroot"
    )
    first = G.email.message_from_string(
        "Message-ID: <first@example.invalid>\n"
        "In-Reply-To: <root@example.invalid>\n\nfirst"
    )
    second = G.email.message_from_string(
        "Message-ID: <second@example.invalid>\n"
        "In-Reply-To: <first@example.invalid>\n\nsecond"
    )
    roots = G.build_thread_roots([inbound, first, second])
    first_thread, _ = G.private_message_locators(first, roots)
    second_thread, _ = G.private_message_locators(second, roots)
    assert first_thread is not None and first_thread == second_thread
    # Without the global graph an IRT-only row degrades instead of guessing.
    assert G.private_message_locators(first)[0] is None


def test_global_thread_roots_handle_deep_newest_first_chain_iteratively():
    messages = []
    for index in range(1_100):
        parent = f"<message-{index - 1}@example.invalid>" if index else None
        headers = f"Message-ID: <message-{index}@example.invalid>\n"
        if parent:
            headers += f"In-Reply-To: {parent}\n"
        messages.append(G.email.message_from_string(headers + "\nbody"))
    roots = G.build_thread_roots(reversed(messages))
    expected = "<message-0@example.invalid>"
    assert roots["<message-1099@example.invalid>"] == expected


def test_private_sidecars_bind_canonical_order_timestamp(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    sidecars = [json.loads(path.read_text()) for path in out.glob("*.meta.json")]
    assert sidecars
    assert all("author_corpus_order_timestamp" in row for row in sidecars)
    for row in sidecars:
        timestamp = row["author_corpus_order_timestamp"]
        assert timestamp is None or timestamp.endswith("+00:00")


def test_nonempty_malformed_date_refuses():
    msg = G.email.message_from_string("Date: definitely-not-a-date\n\nbody")
    with pytest.raises(ValueError, match="Date header"):
        G._message_datetime(msg)


def test_timezone_naive_date_degrades_to_undated():
    # A parseable but timezone-naive Date can't be canonicalized to UTC, so it
    # degrades to undated (like a missing Date) instead of aborting the run.
    msg = G.email.message_from_string("Date: Mon, 1 Jan 2024 10:00:00\n\nbody")
    assert G._message_datetime(msg) is None
    assert G._message_order_timestamp(G._message_datetime(msg)) is None


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
