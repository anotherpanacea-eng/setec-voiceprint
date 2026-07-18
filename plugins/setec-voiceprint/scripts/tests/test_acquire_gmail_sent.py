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
import re
import sys
import time
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
    delsp: bool = False,
) -> G.PreparedMessage:
    headers = [
        f"From: {bf.OWN}",
        "To: visible-recipient@example.invalid",
        "X-Gmail-Labels: Sent",
        "Date: Fri, 17 Jul 2026 12:00:00 -0400",
        "Subject: Flowed semantic boundary",
        "Message-ID: <flowed-boundary@example.invalid>",
        (
            "Content-Type: text/plain; charset=utf-8; format=flowed; DelSp=yes"
            if delsp
            else "Content-Type: text/plain; charset=utf-8; format=flowed"
        ),
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
        "acquire",
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


def test_format_flowed_delsp_boundary_emerging_after_join_is_not_absorbed(
    tmp_path,
):
    prepared = _process_flowed_message(
        tmp_path,
        [
            "MY_AUTHORED_PROSE remains safely in this message ",
            "On Fri, Jul 17, 2026, THIRD_PARTY_HEADER_NAME wro ",
            "te:",
            "> THIRD_PARTY_QUOTE_MUST_NOT_APPEAR",
        ],
        is_reply=True,
        delsp=True,
    )
    cleaned = prepared.piece.cleaned_text
    assert cleaned == "MY_AUTHORED_PROSE remains safely in this message"
    assert "THIRD_PARTY_HEADER_NAME" not in cleaned
    assert "THIRD_PARTY_QUOTE_MUST_NOT_APPEAR" not in cleaned


def test_format_flowed_delsp_detects_attribution_split_inside_prefix(
    tmp_path,
):
    prepared = _process_flowed_message(
        tmp_path,
        [
            "MY_AUTHORED_PROSE remains safely in this message ",
            "O ",
            "n Fri, Jul 17, 2026, THIRD_PARTY_HEADER_NAME wro ",
            "te:",
            "> THIRD_PARTY_QUOTE_MUST_NOT_APPEAR",
        ],
        is_reply=True,
        delsp=True,
    )
    cleaned = prepared.piece.cleaned_text
    assert cleaned == "MY_AUTHORED_PROSE remains safely in this message"
    assert "THIRD_PARTY_HEADER_NAME" not in cleaned
    assert "THIRD_PARTY_QUOTE_MUST_NOT_APPEAR" not in cleaned


@pytest.mark.parametrize(
    "split",
    range(1, len("Begin forwarded message:")),
)
def test_format_flowed_delsp_detects_forward_marker_at_every_split(
    tmp_path, split,
):
    marker_text = "Begin forwarded message:"
    tail = marker_text[split:]
    if tail.startswith(" "):
        tail = " " + tail  # RFC 3676 space-stuffing preserves content space
    prepared = _process_flowed_message(
        tmp_path,
        [
            "MY_AUTHORED_PROSE remains safely in this message ",
            marker_text[:split] + " ",
            tail,
            "THIRD_PARTY_PROSE_MUST_NOT_APPEAR",
        ],
        is_reply=False,
        delsp=True,
    )
    assert prepared.piece.cleaned_text == (
        "MY_AUTHORED_PROSE remains safely in this message"
    )


def test_format_flowed_boundary_scan_is_bounded_on_long_chain():
    text = "\n".join(["On x "] * 2000 + ["end"])
    unwrapped = G._unwrap_flowed(text, delsp=True)
    assert unwrapped.startswith("On xOn x")
    assert unwrapped.endswith("end")


def test_format_flowed_terminal_scans_stay_linear():
    unresolved = "\n".join(["x wrote: "] * 10_000 + ["end"])
    crossed_boundary = "\n".join(
        ["On sender ", "Begin forwarded message: "]
        + ["filler "] * 10_000
        + ["x wrote: "] * 10_000
        + ["end"]
    )
    long_dash_run = "\n".join(["- "] * 20_000 + ["end"])
    started = time.perf_counter()
    G._unwrap_flowed(unresolved, delsp=True)
    G._unwrap_flowed(crossed_boundary, delsp=True)
    G._unwrap_flowed(long_dash_run, delsp=True)
    assert time.perf_counter() - started < 2.0


def test_format_flowed_delsp_detects_attribution_across_eight_fragments(
    tmp_path,
):
    prepared = _process_flowed_message(
        tmp_path,
        [
            "MY_AUTHORED_PROSE remains safely in this message ",
            "O ",
            "n Fri,  ",
            "Jul 17,  ",
            "2026,  ",
            "THIRD_ ",
            "PARTY_ ",
            "NAME wro ",
            "te:",
            "> THIRD_PARTY_QUOTE_MUST_NOT_APPEAR",
        ],
        is_reply=True,
        delsp=True,
    )
    cleaned = prepared.piece.cleaned_text
    assert cleaned == "MY_AUTHORED_PROSE remains safely in this message"
    assert "THIRD_PARTY" not in cleaned
    assert "THIRD_PARTY_QUOTE_MUST_NOT_APPEAR" not in cleaned


@pytest.mark.parametrize(
    ("marker", "is_reply"),
    (
        ("-----Original Message-----", True),
        ("---------- Forwarded message ---------", False),
    ),
)
def test_format_flowed_delsp_detects_dashed_marker_split_at_every_character(
    tmp_path, marker, is_reply,
):
    fragments = [
        "   " if character == " " else character + " "
        for character in marker[:-1]
    ] + [marker[-1]]
    prepared = _process_flowed_message(
        tmp_path,
        [
            "MY_AUTHORED_PROSE remains safely in this message ",
            *fragments,
            "THIRD_PARTY_PROSE_MUST_NOT_APPEAR",
        ],
        is_reply=is_reply,
        delsp=True,
    )
    assert prepared.piece.cleaned_text == (
        "MY_AUTHORED_PROSE remains safely in this message"
    )


def test_format_flowed_marker_offsets_survive_expanding_unicode_casefold(tmp_path):
    marker = "Begin forwarded message:"
    fragments = [
        "   " if character == " " else character + " "
        for character in marker[:-1]
    ] + [marker[-1]]
    prepared = _process_flowed_message(
        tmp_path,
        [
            "MY_AUTHORED Stra\u00dfe remains mine ",
            *fragments,
            "THIRD_PARTY_PROSE_MUST_NOT_APPEAR",
        ],
        is_reply=False,
        delsp=True,
    )
    assert prepared.piece.cleaned_text == "MY_AUTHORED Stra\u00dfe remains mine"


def test_format_flowed_dashed_marker_after_authored_trailing_dash_is_detected(
    tmp_path,
):
    marker = "-----Original Message-----"
    fragments = [
        "   " if character == " " else character + " "
        for character in marker[:-1]
    ] + [marker[-1]]
    prepared = _process_flowed_message(
        tmp_path,
        [
            "MY_AUTHORED_PROSE safely ends-with- ",
            *fragments,
            "THIRD_PARTY_PROSE_MUST_NOT_APPEAR",
        ],
        is_reply=True,
        delsp=True,
    )
    assert prepared.piece.cleaned_text == "MY_AUTHORED_PROSE safely ends-with-"


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


def test_subject_line_recipient_address_is_redacted_to_label(tmp_path):
    recipients = G.ac.StableRedactionMap(
        tmp_path / "recipient_map.json",
        label_prefix="recipient",
        normalize_key=lambda address: address.strip().lower(),
        reuse_gaps=False,
        map_name="recipient map",
    )
    raw = "Re: forward to Alice.Example@mail.invalid before Friday"
    redacted = G._redact_addresses(raw, recipients)
    assert "Alice.Example@mail.invalid" not in redacted
    assert "@mail.invalid" not in redacted
    assert "recipient_" in redacted
    assert G._redact_addresses(raw, recipients) == redacted


def test_process_message_redacts_address_in_subject(tmp_path):
    message = message_from_bytes(
        b"From: " + bf.OWN.encode() + b"\r\n"
        b"To: visible-recipient@example.invalid\r\n"
        b"X-Gmail-Labels: Sent\r\n"
        b"Date: Fri, 17 Jul 2026 12:00:00 -0400\r\n"
        b"Subject: notes for third.party@leak.invalid re the plan\r\n"
        b"Message-ID: <subject-addr@example.invalid>\r\n"
        b"\r\n"
        b"This is my authored body with enough words to pass the floor easily.\r\n"
    )
    out = _out(tmp_path)
    args = G.build_arg_parser().parse_args([
        "acquire",
        "--mbox-path", str(tmp_path / "unused.mbox"),
        "--own-address", bf.OWN, "--output-dir", str(out),
        "--min-words-per-piece", "1",
    ])
    opts = G.parse_options(args)
    recipients = G.ac.StableRedactionMap(
        opts.recipient_map_path, label_prefix="recipient",
        normalize_key=lambda address: address.strip().lower(),
        reuse_gaps=False, map_name="recipient map",
    )
    prepared = G.process_message(message, opts, recipients, G.Summary())
    assert prepared is not None
    assert "third.party@leak.invalid" not in prepared.piece.title
    assert "recipient_" in prepared.piece.title


def test_multiline_zillow_share_footer_is_stripped_with_provenance():
    body = (
        "Thought you might like this place, it is near the park.\n"
        "\n"
        "View this home on Zillow:\n"
        "http://www.zillow.com/homedetails/2123354710_zpid\n"
        "\n"
        "Download the free Zillow iPhone app:\n"
        "http://itunes.apple.com/us/app/id310738695?mt=8"
    )
    details = G._unwrap_flowed_details(body)
    kept = G._trim_signature(
        details.text, None, details.line_provenance,
    )
    assert "zillow.com" not in kept.lower()
    assert "itunes.apple.com" not in kept
    assert kept == "Thought you might like this place, it is near the park."


def test_authored_service_citation_midbody_is_preserved():
    body = (
        "Start with Oxford's Peter Millican at iTunes U: "
        "http://itunes.apple.com/x\n"
        "and tell me what you think of the lectures."
    )
    assert G._trim_signature(body, None) == body

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


def test_inband_live_smoke_confirmed_is_hard_refused(
    tmp_path, mbox, monkeypatch, capsys,
):
    # The acquisition-time in-band approval path is removed. --live-smoke-confirmed
    # hard-refuses (rc 2) even from an interactive TTY, and mints no receipt —
    # approval may only happen through the separated smoke/approve-smoke flow.
    out = _out(tmp_path)
    monkeypatch.setattr(G.sys.stdin, "isatty", lambda: True)
    rc = G.main([
        "--mbox-path", str(mbox), "--own-address", bf.OWN,
        "--output-dir", str(out), "--min-words-per-piece", "5",
        "--since", "2000-01-01", "--live-smoke-confirmed",
    ])
    assert rc == 2
    assert "has been removed" in capsys.readouterr().err
    assert not (out / G.RECEIPT_NAME).exists()


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


def test_inband_live_smoke_confirmed_refused_even_with_allow_empty(
    tmp_path, mbox, monkeypatch
):
    # Even combined with --allow-empty on a TTY, the removed in-band approval
    # path refuses (rc 2) before acquiring anything and mints no receipt.
    out = _out(tmp_path)
    monkeypatch.setattr(G.sys.stdin, "isatty", lambda: True)

    assert _run(
        mbox,
        out,
        ["--allow-empty", "--live-smoke-confirmed"],
    ) == 2
    assert not (out / G.RECEIPT_NAME).exists()


def test_dedupe_only_rerun_is_success_without_manifest_growth(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    before = (out / "draft_manifest.jsonl").read_text()

    assert _run(mbox, out) == 0
    assert (out / "draft_manifest.jsonl").read_text() == before


# =================================================================
# Crash-safe resume: kill-point / reconciliation / single-invocation
# =================================================================
#
# The advertised replay invariant is NOT full byte-identity. Two provenance
# stamps are re-generated on every run: the manifest ``acquired_via`` (built from
# date.today()) and the sidecar ``acquired_at`` (datetime.now()). Instead of
# silently dropping them before comparing — which would HIDE a real difference
# and let the test claim more than it proves — ``_capture`` ASSERTS each is
# present and well-formed on every row/sidecar, then normalizes it to a fixed
# placeholder that stays in the compared structure. The equality assertions
# therefore prove exactly the true invariant: identical content (.txt bytes,
# content hashes, recipient map) and identical manifest/sidecar rows EXCEPT for
# those two named, verified-present, re-stamped provenance fields.

_ACQUIRED_VIA_RE = re.compile(r"^acquire_gmail_sent_\d{4}-\d{2}-\d{2}$")
_ACQUIRED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _capture(out: Path):
    manifest = [
        json.loads(l)
        for l in (out / "draft_manifest.jsonl").read_text().splitlines()
        if l.strip()
    ]
    for row in manifest:
        via = row.get("acquired_via")
        assert isinstance(via, str) and _ACQUIRED_VIA_RE.match(via), via
        row["acquired_via"] = "<acquired_via>"  # verified present, then normalized
    txts = {p.name: p.read_bytes() for p in sorted(out.glob("*.txt"))}
    metas = {}
    for p in sorted(out.glob("*.meta.json")):
        data = json.loads(p.read_text())
        at = data.get("acquired_at")
        assert isinstance(at, str) and _ACQUIRED_AT_RE.match(at), at
        data["acquired_at"] = "<acquired_at>"  # verified present, then normalized
        metas[p.name] = data
    rmap = b""
    if (out / "recipient_map.json").exists():
        rmap = (out / "recipient_map.json").read_bytes()
    return manifest, txts, metas, rmap


@pytest.fixture
def golden(tmp_path_factory, mbox):
    out = (tmp_path_factory.mktemp("golden")
           / "ai-prose-baselines-private" / "identity" / "personal_email" / "joshua")
    assert _run(mbox, out) == 0
    cap = _capture(out)
    assert cap[0], "golden produced no manifest rows"
    return cap


def _committed_count(out: Path) -> int:
    mf = out / "draft_manifest.jsonl"
    if not mf.exists():
        return 0
    return sum(1 for l in mf.read_text().splitlines() if l.strip())


def test_single_full_invocation_matches_uninterrupted(tmp_path, mbox, golden):
    # One invocation = index pass + one processing pass; output equals golden.
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    assert (out / G.THREAD_INDEX_NAME).exists()  # index persisted for resume
    assert _capture(out) == golden


def test_resume_reuses_persisted_index_and_skips_pass1(tmp_path, mbox, golden, monkeypatch):
    # Run 1: complete pass 1 (index persisted), raise before the first emit.
    out = _out(tmp_path)
    monkeypatch.setattr(G, "emit_piece", _raiser("kill before first emit"))
    with pytest.raises(RuntimeError):
        _run(mbox, out)
    assert (out / G.THREAD_INDEX_NAME).exists()
    monkeypatch.undo()

    # Run 2 (resume): build_thread_roots must NOT be called (index reused).
    calls = {"n": 0}
    real = G.build_thread_roots
    monkeypatch.setattr(
        G, "build_thread_roots",
        lambda box: (calls.__setitem__("n", calls["n"] + 1), real(box))[1],
    )
    assert _run(mbox, out) == 0
    assert calls["n"] == 0, "resume redid pass 1 despite a valid index"
    assert _capture(out) == golden


def _raiser(msg, fail_on=1):
    state = {"n": 0}

    def go(*a, **k):
        state["n"] += 1
        if state["n"] >= fail_on:
            raise RuntimeError(msg)

    return go


def _counting_wrapper(orig, fail_on, side_effect=None):
    state = {"n": 0}

    def wrapper(*a, **k):
        state["n"] += 1
        if state["n"] == fail_on:
            if side_effect is not None:
                side_effect(*a, **k)
            raise RuntimeError("kill-point")
        return orig(*a, **k)

    return wrapper


def test_kill_after_txt_before_meta_replays_to_golden(tmp_path, mbox, golden, monkeypatch):
    out = _out(tmp_path)
    total = len(golden[0])
    fail_on = total // 2 + 1
    # ac._write_text_atomic backs ONLY the sidecar write inside write_piece.
    monkeypatch.setattr(
        G.ac, "_write_text_atomic",
        _counting_wrapper(G.ac._write_text_atomic, fail_on),
    )
    with pytest.raises(RuntimeError):
        _run(mbox, out)
    # An orphan .txt (stem uncommitted) exists; no manifest row for it.
    assert _committed_count(out) == fail_on - 1
    monkeypatch.undo()

    assert _run(mbox, out) == 0
    assert _capture(out) == golden


def test_kill_after_meta_before_manifest_replays_to_golden(tmp_path, mbox, golden, monkeypatch):
    out = _out(tmp_path)
    total = len(golden[0])
    fail_on = total // 2 + 1
    monkeypatch.setattr(
        G.ac, "append_manifest_entry",
        _counting_wrapper(G.ac.append_manifest_entry, fail_on),
    )
    with pytest.raises(RuntimeError):
        _run(mbox, out)
    assert _committed_count(out) == fail_on - 1
    monkeypatch.undo()

    assert _run(mbox, out) == 0
    assert _capture(out) == golden


def test_torn_manifest_tail_is_repaired_on_resume(tmp_path, mbox, golden, monkeypatch):
    out = _out(tmp_path)
    total = len(golden[0])
    fail_on = total // 2 + 1

    def torn_write(manifest_path, entry, **kw):
        with open(manifest_path, "a", encoding="utf-8") as f:
            f.write('{"id": "torn-partial-line-no-newl')  # partial, unterminated

    monkeypatch.setattr(
        G.ac, "append_manifest_entry",
        _counting_wrapper(G.ac.append_manifest_entry, fail_on, side_effect=torn_write),
    )
    with pytest.raises(RuntimeError):
        _run(mbox, out)
    raw = (out / "draft_manifest.jsonl").read_bytes()
    assert not raw.endswith(b"\n"), "expected a torn (unterminated) tail line"
    monkeypatch.undo()

    assert _run(mbox, out) == 0
    final = (out / "draft_manifest.jsonl").read_bytes()
    assert final.endswith(b"\n")
    for line in final.decode().splitlines():
        json.loads(line)  # every line parses; torn line was dropped
    assert _capture(out) == golden


def test_mid_stream_resume_preserves_row_order_and_recipient_map(tmp_path, mbox, golden, monkeypatch):
    out = _out(tmp_path)
    total = len(golden[0])
    fail_on = total // 2 + 1
    monkeypatch.setattr(
        G.ac, "append_manifest_entry",
        _counting_wrapper(G.ac.append_manifest_entry, fail_on),
    )
    with pytest.raises(RuntimeError):
        _run(mbox, out)
    partial_rows = [json.loads(l) for l in
                    (out / "draft_manifest.jsonl").read_text().splitlines() if l.strip()]
    for row in partial_rows:
        # Normalize the one re-stamped provenance field the same way _capture
        # does (after asserting it is present), so the pre-crash rows compare
        # against the resumed capture on their stable content only.
        assert isinstance(row.get("acquired_via"), str)
        row["acquired_via"] = "<acquired_via>"
    monkeypatch.undo()

    assert _run(mbox, out) == 0
    manifest, _, _, rmap = _capture(out)
    # Rows 1..N committed pre-crash keep their content and order across the resume.
    assert manifest[: len(partial_rows)] == partial_rows
    assert (manifest, rmap) == (golden[0], golden[3])


def test_post_crash_rerun_is_idempotent(tmp_path, mbox, golden):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    before = (out / "draft_manifest.jsonl").read_bytes()
    assert _run(mbox, out) == 0  # dedupe-only rerun
    assert (out / "draft_manifest.jsonl").read_bytes() == before
    assert _capture(out) == golden


def test_preexisting_orphan_is_reacquired_not_skipped(tmp_path, mbox, golden):
    # F2 regression: a .txt+.meta with a matching content_hash but NO manifest
    # row must be reconciled (deleted + re-acquired), not skipped forever.
    src = _out(tmp_path / "src")
    assert _run(mbox, src) == 0
    # Seed a FRESH tree with one piece's orphan pair (no manifest, no index).
    out = _out(tmp_path / "dst")
    out.mkdir(parents=True)
    one_txt = sorted(src.glob("*.txt"))[0]
    one_meta = src / (one_txt.stem + ".meta.json")
    (out / one_txt.name).write_bytes(one_txt.read_bytes())
    (out / one_meta.name).write_bytes(one_meta.read_bytes())

    assert _run(mbox, out) == 0
    ids = {r["id"] for r in _entries(out)}
    assert one_txt.stem in ids, "orphan was skipped instead of re-acquired"
    assert _capture(out) == golden


def test_stray_tmp_files_are_swept(tmp_path, mbox, golden):
    out = _out(tmp_path)
    out.mkdir(parents=True)
    (out / "leftover.txt.deadbeef.tmp").write_text("junk")
    (out / "draft_manifest.jsonl.deadbeef.tmp").write_text("junk")
    assert _run(mbox, out) == 0
    assert not list(out.glob("*.tmp"))
    assert _capture(out) == golden


def test_mbox_identity_guard_fails_loud_on_changed_mbox(tmp_path, mbox):
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    assert _committed_count(out) > 0
    # Mutate the source mbox → its sha no longer matches the checkpoint.
    with open(mbox, "ab") as f:
        f.write(b"\nFrom mutation@example.com Mon Jun 15 12:00:00 2020\n")
    with pytest.raises(SystemExit):
        _run(mbox, out)


def test_max_items_counts_committed_rows_across_resume(tmp_path, mbox, monkeypatch):
    # A crash after k commits, then an identical resume, must finish at exactly
    # --max-items TOTAL rows (not k + max-items). The fixture has more acquirable
    # pieces than `cap`, so the buggy k+cap total is distinguishable from cap.
    out = _out(tmp_path)
    cap = 6
    # Run 1: crash on the 4th manifest append → exactly 3 committed rows.
    monkeypatch.setattr(
        G.ac, "append_manifest_entry",
        _counting_wrapper(G.ac.append_manifest_entry, 4),
    )
    with pytest.raises(RuntimeError):
        _run(mbox, out, ["--max-items", str(cap)])
    assert _committed_count(out) == 3
    monkeypatch.undo()

    # Run 2 (identical resume): the 3 already-committed rows count toward the
    # cap, so the run stops at exactly `cap` total, never 3 + cap.
    assert _run(mbox, out, ["--max-items", str(cap)]) == 0
    assert _committed_count(out) == cap


def test_recipient_map_is_durable_before_each_commit(
    tmp_path, mbox, golden, monkeypatch,
):
    # Every committed manifest row references recipient_NN labels whose raw->label
    # mapping lives ONLY in recipient_map.json. That map must be on disk before a
    # dependent row commits, so a mid-run crash never leaves a committed row whose
    # label mapping was never persisted. (Pre-fix, the map was written only at a
    # clean close, so a crash left committed rows with no recipient map at all.)
    out = _out(tmp_path)
    total = len(golden[0])
    fail_on = total // 2 + 1
    monkeypatch.setattr(
        G.ac, "append_manifest_entry",
        _counting_wrapper(G.ac.append_manifest_entry, fail_on),
    )
    with pytest.raises(RuntimeError):
        _run(mbox, out)

    rmap_path = out / "recipient_map.json"
    assert rmap_path.exists(), "recipient map was not persisted before commits"
    persisted_labels = set(json.loads(rmap_path.read_text()).values())
    committed = [
        json.loads(l)
        for l in (out / "draft_manifest.jsonl").read_text().splitlines()
        if l.strip()
    ]
    assert committed, "expected committed rows before the crash"
    for row in committed:
        for label in re.findall(r"recipient_\d+", row.get("notes", "")):
            assert label in persisted_labels, (
                f"{label} referenced by a committed row but absent from the "
                "durably persisted recipient map"
            )
    monkeypatch.undo()

    # A clean resume still reproduces the golden recipient map byte-for-byte.
    assert _run(mbox, out) == 0
    assert _capture(out)[3] == golden[3]


# =================================================================
# Lane B: smoke approval separated from acquisition
# =================================================================


def _smoke(mbox, out, extra=None):
    argv = [
        "smoke", "--mbox-path", str(mbox), "--own-address", bf.OWN,
        "--output-dir", str(out), "--min-words-per-piece", "5",
        "--since", "2000-01-01", "--until", "2100-01-01",
    ]
    return G.main(argv + (extra or []))


def test_smoke_writes_descriptor_and_mints_no_receipt(tmp_path, mbox):
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    assert _smoke(mbox, smoke_dir) == 0
    desc = json.loads((smoke_dir / G.SMOKE_DESCRIPTOR_NAME).read_text())
    assert desc["schema"] == G.SMOKE_DESCRIPTOR_SCHEMA
    assert desc["acquired"] > 0
    assert desc["manifest_rows"] == desc["acquired"]
    assert "behavior_fingerprint" in desc
    assert not (smoke_dir / G.RECEIPT_NAME).exists()  # smoke never mints approval


def test_smoke_requires_window(tmp_path, mbox):
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    rc = G.main([
        "smoke", "--mbox-path", str(mbox), "--own-address", bf.OWN,
        "--output-dir", str(smoke_dir), "--min-words-per-piece", "5",
    ])
    assert rc == 2
    assert not (smoke_dir / G.SMOKE_DESCRIPTOR_NAME).exists()


def test_validate_smoke_clean_tree_exits_zero_and_writes_nothing(tmp_path, mbox):
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    assert _smoke(mbox, smoke_dir) == 0
    before = {p.name: p.read_bytes() for p in smoke_dir.iterdir() if p.is_file()}
    rc = G.main(["validate-smoke", "--mbox-path", str(mbox),
                 "--smoke-dir", str(smoke_dir)])
    assert rc == 0
    after = {p.name: p.read_bytes() for p in smoke_dir.iterdir() if p.is_file()}
    assert after == before  # read-only: nothing written or changed


def test_validate_smoke_detects_orphan(tmp_path, mbox):
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    assert _smoke(mbox, smoke_dir) == 0
    # Drop one manifest row, leaving its .txt/.meta → orphan sidecar.
    mf = smoke_dir / "draft_manifest.jsonl"
    lines = [l for l in mf.read_text().splitlines() if l.strip()]
    mf.write_text("\n".join(lines[:-1]) + "\n")
    rc = G.main(["validate-smoke", "--mbox-path", str(mbox),
                 "--smoke-dir", str(smoke_dir)])
    assert rc == 2


def test_validate_smoke_detects_stale_mbox(tmp_path, mbox):
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    assert _smoke(mbox, smoke_dir) == 0
    with open(mbox, "ab") as f:
        f.write(b"\n")  # mbox changed since smoke → stale
    rc = G.main(["validate-smoke", "--mbox-path", str(mbox),
                 "--smoke-dir", str(smoke_dir)])
    assert rc == 2


def test_approve_smoke_requires_tty(tmp_path, mbox, monkeypatch):
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    full = tmp_path / "ai-prose-baselines-private" / "full"
    assert _smoke(mbox, smoke_dir) == 0
    monkeypatch.setattr(G.sys.stdin, "isatty", lambda: False)
    rc = G.main(["approve-smoke", "--mbox-path", str(mbox),
                 "--smoke-dir", str(smoke_dir), "--output-dir", str(full)])
    assert rc == 2
    assert not (full / G.RECEIPT_NAME).exists()


def test_approve_smoke_mints_receipt_without_acquiring(tmp_path, mbox, monkeypatch):
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    full = tmp_path / "ai-prose-baselines-private" / "full"
    assert _smoke(mbox, smoke_dir) == 0
    monkeypatch.setattr(G.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "y")
    rc = G.main(["approve-smoke", "--mbox-path", str(mbox),
                 "--smoke-dir", str(smoke_dir), "--output-dir", str(full)])
    assert rc == 0
    receipt = json.loads((full / G.RECEIPT_NAME).read_text())
    assert receipt["schema"] == G.RECEIPT_SCHEMA
    # approve acquires nothing: FULL holds ONLY the receipt.
    assert not list(full.glob("*.txt"))
    assert not (full / "draft_manifest.jsonl").exists()


def test_full_run_accepts_receipt_from_separate_smoke_tree(tmp_path, mbox, monkeypatch):
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    full = tmp_path / "ai-prose-baselines-private" / "full"
    assert _smoke(mbox, smoke_dir) == 0
    monkeypatch.setattr(G.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "y")
    assert G.main(["approve-smoke", "--mbox-path", str(mbox),
                   "--smoke-dir", str(smoke_dir), "--output-dir", str(full)]) == 0
    monkeypatch.undo()
    # Unwindowed full run into the DIFFERENT tree is accepted by the gate.
    rc = G.main([
        "acquire", "--mbox-path", str(mbox), "--own-address", bf.OWN,
        "--output-dir", str(full), "--min-words-per-piece", "5",
    ])
    assert rc == 0
    assert list(full.glob("*.txt"))


def test_gate_refuses_when_min_words_differs_from_smoke(tmp_path, mbox, monkeypatch):
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    full = tmp_path / "ai-prose-baselines-private" / "full"
    assert _smoke(mbox, smoke_dir) == 0
    monkeypatch.setattr(G.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "y")
    assert G.main(["approve-smoke", "--mbox-path", str(mbox),
                   "--smoke-dir", str(smoke_dir), "--output-dir", str(full)]) == 0
    monkeypatch.undo()
    # A looser word floor than the reviewed smoke must refuse at the gate.
    with pytest.raises(SystemExit):
        G.main([
            "acquire", "--mbox-path", str(mbox), "--own-address", bf.OWN,
            "--output-dir", str(full), "--min-words-per-piece", "9",
        ])


def _tamper_descriptor(smoke_dir: Path, **changes) -> None:
    path = smoke_dir / G.SMOKE_DESCRIPTOR_NAME
    desc = json.loads(path.read_text())
    desc.update(changes)
    path.write_text(
        json.dumps(desc, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


@pytest.mark.parametrize("changes,reason", [
    ({"manifest_rows": 999}, "descriptor_manifest_rows_mismatch"),
    ({"acquired": 999}, "descriptor_acquired_mismatch"),
    ({"behavior_fingerprint": "deadbeef" * 8}, "descriptor_fingerprint_mismatch"),
])
def test_validate_smoke_rejects_tampered_descriptor(
    tmp_path, mbox, changes, reason, capsys,
):
    # A hand-edited descriptor whose recorded counts/fingerprint no longer match
    # the actual tree (and the recorded params) is rejected — the values are
    # recomputed and verified, not trusted.
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    assert _smoke(mbox, smoke_dir) == 0
    _tamper_descriptor(smoke_dir, **changes)
    capsys.readouterr()  # drain the smoke-run summary from stderr
    rc = G.main(["validate-smoke", "--mbox-path", str(mbox),
                 "--smoke-dir", str(smoke_dir)])
    assert rc == 2
    assert json.loads(capsys.readouterr().err)["smoke_validate"] == reason


def test_validate_smoke_rejects_tampered_behavior_params(tmp_path, mbox, capsys):
    # Editing behavior_params alone breaks the fingerprint it must reproduce.
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    assert _smoke(mbox, smoke_dir) == 0
    path = smoke_dir / G.SMOKE_DESCRIPTOR_NAME
    desc = json.loads(path.read_text())
    desc["behavior_params"]["min_words"] = desc["behavior_params"]["min_words"] + 100
    path.write_text(
        json.dumps(desc, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    capsys.readouterr()  # drain the smoke-run summary from stderr
    rc = G.main(["validate-smoke", "--mbox-path", str(mbox),
                 "--smoke-dir", str(smoke_dir)])
    assert rc == 2
    assert json.loads(capsys.readouterr().err)["smoke_validate"] == (
        "descriptor_fingerprint_mismatch"
    )


def test_approve_smoke_refuses_tampered_descriptor_and_mints_nothing(
    tmp_path, mbox, monkeypatch,
):
    # approve-smoke recomputes/verifies the descriptor BEFORE prompting or
    # minting, so a tampered descriptor yields no receipt even from a TTY 'y'.
    smoke_dir = tmp_path / "ai-prose-baselines-private" / "smoke"
    full = tmp_path / "ai-prose-baselines-private" / "full"
    assert _smoke(mbox, smoke_dir) == 0
    _tamper_descriptor(smoke_dir, acquired=999)
    monkeypatch.setattr(G.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda: "y")
    rc = G.main(["approve-smoke", "--mbox-path", str(mbox),
                 "--smoke-dir", str(smoke_dir), "--output-dir", str(full)])
    assert rc == 2
    assert not (full / G.RECEIPT_NAME).exists()
    assert not list(full.glob("*.txt"))


def test_legacy_flat_cli_still_routes_to_acquire(tmp_path, mbox):
    # The subcommand refactor must not break the flat CLI (all prior callers).
    out = _out(tmp_path)
    assert _run(mbox, out) == 0
    assert list(out.glob("*.txt"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
