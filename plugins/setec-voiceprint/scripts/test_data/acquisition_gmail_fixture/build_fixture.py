#!/usr/bin/env python3
"""Generate a synthetic Google-Takeout-shaped .mbox fixture for
acquire_gmail_sent.py's tests. Run standalone to (re)create
sent_fixture.mbox; tests import build_fixture() and regenerate on demand.

Each message carries an explicit semantic tag (its own leading line, so
it never merges onto a structural first line like a forward marker) so
tests can locate a case by tag and bodies stay distinct for dedup.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
MBOX_PATH = HERE / "sent_fixture.mbox"

OWN = "me@example.com"
R_ALICE = "alice@example.com"
R_BOB = "bob@example.com"
R_CAROL = "carol@example.com"

QUOTE_SENTINEL = "QUOTED_CORRESPONDENT_TEXT_SHOULD_NEVER_APPEAR"
WRAP_NAME = "Jonathan Alexander Smith-Whitmore"
WRAP_ADDR = "jonathan.smith.whitmore@longdomain.example.com"
FWD_BODY = "FORWARDED_THIRD_PARTY_BODY_SHOULD_NEVER_APPEAR"
SIG_TEXT = "Best regards, Me | Sent from my Gmail on this device signature block"
LONG = "a genuinely composed message with plenty of words in it so that it clears the minimum word floor comfortably and reads like real prose"


def _msg(*, frm, to=None, cc=None, subject, body, labels="Sent",
         date="Mon, 15 Jun 2020 12:00:00 -0400", extra_headers=None,
         ctype="text/plain; charset=utf-8"):
    headers = [f"From: {frm}"]
    if to:
        headers.append("To: " + ", ".join(to))
    if cc:
        headers.append("Cc: " + ", ".join(cc))
    if subject is not None:
        headers.append(f"Subject: {subject}")
    headers.append(f"Date: {date}")
    if labels is not None:
        headers.append(f"X-Gmail-Labels: {labels}")
    headers.append(f"Content-Type: {ctype}")
    for k, v in (extra_headers or {}).items():
        headers.append(f"{k}: {v}")
    return "\n".join(headers) + "\n\n" + body + "\n"


def _mbox_from(addr):
    return f"From {addr} Mon Jun 15 12:00:00 2020\n"


def build_fixture(path: Path = MBOX_PATH) -> Path:
    msgs: list[str] = []

    def add(tag, *, body, **kw):
        # Tag on its own leading line so it never fuses with a structural
        # first line (e.g. a forward marker). Pass tag=None for cases where
        # a leading line would change semantics (forward-no-comment).
        full = (f"{tag}.\n{body}" if tag else body)
        msgs.append(_mbox_from(kw.get("frm", OWN)) + _msg(body=full, **kw))

    add("PLAIN", frm=OWN, to=[R_ALICE], subject="Lunch plans, Alice?", body=LONG)
    add("BELOWMIN", frm=OWN, to=[R_ALICE], subject="ok", body="hi")

    add("PLAINREPLY", frm=OWN, to=[R_BOB], subject="Re: proposal",
        extra_headers={"In-Reply-To": "<abc@x>"},
        body=(LONG + "\n\nOn Sun, Jun 14, 2020 at 3:00 PM Bob <" + R_BOB
              + "> wrote:\n> " + QUOTE_SENTINEL))

    html = (
        "<div>" + LONG + " HTMLREPLY marker</div>"
        "<div class=\"gmail_quote\">"
        "<div class=\"gmail_attr\">On Sun, Jun 14, 2020, " + WRAP_NAME
        + " &lt;" + WRAP_ADDR + "&gt; wrote:</div>"
        "<blockquote class=\"gmail_quote\">" + QUOTE_SENTINEL
        + "</blockquote></div>"
    )
    add(None, frm=OWN, to=[R_BOB], subject="Re: html proposal",
        extra_headers={"References": "<def@x>"},
        ctype="text/html; charset=utf-8", body=html)

    add("WRAPPED", frm=OWN, to=[R_BOB], subject="Re: wrapped",
        extra_headers={"In-Reply-To": "<w@x>"},
        body=(LONG + "\n\nOn Sun, Jun 14, 2020 at 3:00 PM " + WRAP_NAME
              + " <" + WRAP_ADDR + ">\nwrote:\n> " + QUOTE_SENTINEL))

    add("SIGNED", frm=OWN, to=[R_ALICE], subject="With signature",
        body=LONG + "\n\n-- \n" + SIG_TEXT)

    # forward, no comment -> NO tag (a leading tag would look like a comment).
    add(None, frm=OWN, to=[R_ALICE], subject="Fwd: something",
        body="---------- Forwarded message ---------\nFrom: x\n\n" + FWD_BODY)

    add("FWDCOMMENT", frm=OWN, to=[R_ALICE], subject="Fwd: with comment",
        body=(LONG + " see below.\n\n---------- Forwarded message "
              "---------\nFrom: x\n\n" + FWD_BODY))

    add("UNRESOLVED", frm=OWN, to=[R_BOB], subject="Re: weird",
        extra_headers={"In-Reply-To": "<weird@x>"},
        body=(LONG + "\n\nsomeone somewhere wrote:\n" + QUOTE_SENTINEL))

    add("CLEANREPLY", frm=OWN, to=[R_BOB], subject="Re: clean",
        extra_headers={"In-Reply-To": "<clean@x>"}, body=LONG)

    add("NOTOWN", frm="stranger@elsewhere.com", to=[OWN],
        subject="Not from me", body=LONG)

    add("NOTSENT", frm=OWN, to=[R_ALICE], subject="Echoed",
        labels="Important", body=LONG)

    add("SUBSTRLABEL", frm=OWN, to=[R_ALICE], subject="To be sent later",
        labels="To be Sent", body=LONG)

    add("AUTOREPLIED", frm=OWN, to=[R_ALICE], subject="Out of office",
        extra_headers={"Auto-Submitted": "auto-replied"}, body=LONG)

    add("OLD2013", frm=OWN, to=[R_ALICE], subject="Old one",
        date="Tue, 15 Jan 2013 12:00:00 -0500", body=LONG)

    add("MULTICC", frm=OWN, to=[R_ALICE], cc=[R_BOB, R_CAROL],
        subject="Group note", body=LONG)

    add("NOLABELS", frm=OWN, to=[R_ALICE], subject="No labels header",
        labels=None, body=LONG)

    add("NOSUBJECT", frm=OWN, to=[R_ALICE], subject=None, body=LONG)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(msgs), encoding="utf-8")
    return path


if __name__ == "__main__":
    print("wrote", build_fixture())
