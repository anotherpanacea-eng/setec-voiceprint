#!/usr/bin/env python3
"""Build (re-build) the fixture PDFs used by pdf_inventory + pdf_extract tests.

Run this once when adding or refreshing fixture PDFs. The output is
committed to the repo so CI doesn't need ``reportlab`` installed.

Usage:

    python3 plugins/setec-voiceprint/scripts/test_data/pdf_inventory_fixture/_make_fixtures.py

Generates four PDFs that exercise every classification path the
inventory tool needs to handle:

  text_layer_with_metadata.pdf
      Multi-page born-digital PDF carrying title / author /
      creation-date metadata. The text layer is a deterministic
      synthetic essay — not real third-party prose — so the fixture
      is committable without any consent question.

  text_layer_without_metadata.pdf
      Same content shape as above but with the metadata fields
      stripped. Tests that the inventory still classifies the file
      as text_extractable and reports ``metadata_quality: "none"``.

  image_only.pdf
      A PDF page with a vector-drawn rectangle and zero text
      operators. ``pypdf.extract_text()`` returns an empty string,
      so the inventory must classify as image_only. We don't ship
      a real raster image to keep the fixture under the spec's
      1 MB ceiling.

  corrupted.pdf
      Random bytes prefixed with the PDF magic header. ``pypdf``
      raises during open; the inventory must record the failure as
      ``classification: corrupted`` instead of aborting the run.

The fixture is intentionally synthetic so we don't ship someone
else's prose under fair-use ambiguity.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import inch
except ImportError as exc:  # pragma: no cover
    print(
        "reportlab is needed to (re)build fixture PDFs. "
        "pip install --user reportlab",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


HERE = Path(__file__).resolve().parent


# --------------- Synthetic prose for the text-layer PDFs ---------


SYNTHETIC_PARAGRAPHS = [
    "The discipline of attention is older than the disciplines that "
    "depend on it. The mathematician and the carpenter share a single "
    "habit: each looks at the object until the object begins to "
    "explain itself. Books, lathes, equations, paragraphs all yield "
    "to the same patient looking; the looker, in turn, comes back "
    "altered.",

    "What an archive teaches, slowly, is that even the most particular "
    "case sits in a series. The single letter belongs to a "
    "correspondence, the correspondence to a life, the life to a "
    "neighborhood of contemporary lives whose details the writer would "
    "not have known to enumerate. Reading a single document well "
    "always involves estimating, somewhere out of the corner of the "
    "eye, the size of the surrounding series.",

    "The trouble with originality, said the painter, is that it cannot "
    "be planned for. You can prepare the workshop, sharpen the tools, "
    "stretch the canvas, lay out the colors in the order you intend "
    "to use them, and the morning still arrives in which the work "
    "either turns up or doesn't, and there is no procedure that makes "
    "the difference. The procedure narrows the field; the field still "
    "has to be entered.",

    "When a body of writing has accumulated long enough, the writer's "
    "habits become legible to the writer in a way they were not "
    "legible at the moment of composition. This is one of the gifts "
    "of an archive — it allows you to be your own first reader at a "
    "critical distance you could not produce on demand. What you "
    "thought was a fresh choice you find you have made eleven times "
    "before; what you thought was an inheritance you find you "
    "yourself invented and forgot.",
]


# --------------- PDF builders -----------------------------------


def _draw_paragraphs(c: canvas.Canvas, paragraphs: list[str]) -> None:
    """Lay out paragraphs on letter pages with simple word-wrap.

    reportlab's high-level Platypus would do nicer typesetting, but
    we deliberately keep the fixture build minimal: the goal is text
    on the page, not visual quality. The wrap width and font size
    are chosen so a reasonable number of words fit per page; the
    inventory's word-count estimation is exercised by having more
    than one page of meaningful text.
    """
    width, height = letter
    margin = 1.0 * inch
    line_height = 14
    text_object = c.beginText(margin, height - margin)
    text_object.setFont("Helvetica", 11)
    for para in paragraphs:
        # Simple greedy word wrap to ~80 chars.
        line = ""
        for word in para.split():
            candidate = (line + " " + word).strip()
            if len(candidate) > 80:
                text_object.textLine(line)
                line = word
            else:
                line = candidate
        if line:
            text_object.textLine(line)
        text_object.textLine("")
        # Page break when we've gotten close to the bottom margin.
        # Exact arithmetic isn't important — we just want > 1 page.
        if text_object.getY() < margin + 6 * line_height:
            c.drawText(text_object)
            c.showPage()
            text_object = c.beginText(margin, height - margin)
            text_object.setFont("Helvetica", 11)
    c.drawText(text_object)


def build_text_layer_with_metadata(out: Path) -> None:
    c = canvas.Canvas(str(out), pagesize=letter)
    c.setTitle("On the Discipline of Attention")
    c.setAuthor("Synthetic Author")
    c.setSubject("Fixture for pdf_inventory.py tests")
    c.setCreator("setec-voiceprint test fixture")
    # CreationDate gets set automatically by reportlab; force a stable
    # value so our test assertions don't depend on the build clock.
    os.environ["SOURCE_DATE_EPOCH"] = "1577836800"  # 2020-01-01 UTC
    _draw_paragraphs(c, SYNTHETIC_PARAGRAPHS)
    c.save()


def build_text_layer_without_metadata(out: Path) -> None:
    c = canvas.Canvas(str(out), pagesize=letter)
    # reportlab sets "untitled" / "anonymous" defaults when title /
    # author aren't supplied. Force them to empty strings so pypdf
    # reads them as falsy and the inventory reports
    # ``metadata_quality: "none"``.
    c.setTitle("")
    c.setAuthor("")
    c.setSubject("")
    _draw_paragraphs(c, SYNTHETIC_PARAGRAPHS)
    c.save()


def build_image_only(out: Path) -> None:
    """A PDF with one vector-drawn page and zero text operators."""
    c = canvas.Canvas(str(out), pagesize=letter)
    # Strip the auto-set "untitled" / "anonymous" defaults so the
    # image_only fixture can also exercise the metadata-quality
    # "none" bucket.
    c.setTitle("")
    c.setAuthor("")
    c.setSubject("")
    width, height = letter
    # Draw a centered rectangle and a circle. No text whatsoever.
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(2)
    c.rect(2 * inch, 4 * inch, 4 * inch, 3 * inch)
    c.circle(width / 2, height / 2, 50)
    c.showPage()
    c.save()


def build_corrupted(out: Path) -> None:
    """A file with the PDF magic header followed by random bytes.

    pypdf opens it, fails during the trailer parse, and the
    inventory records ``classification: corrupted``. We use a
    deterministic byte pattern (not random) so the file hash stays
    stable across rebuilds.
    """
    out.write_bytes(b"%PDF-1.4\n" + bytes(range(256)) * 4)


def main() -> int:
    targets = {
        HERE / "text_layer_with_metadata.pdf": build_text_layer_with_metadata,
        HERE / "text_layer_without_metadata.pdf": build_text_layer_without_metadata,
        HERE / "image_only.pdf": build_image_only,
        HERE / "corrupted.pdf": build_corrupted,
    }
    for path, fn in targets.items():
        fn(path)
        size = path.stat().st_size
        print(f"  wrote {path.name} ({size:,} bytes)")
    print("Total fixture size: "
          f"{sum(p.stat().st_size for p in targets):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
