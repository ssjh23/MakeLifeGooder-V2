"""Generate the four failure-shape fixture PDFs.

    uv run python -m tests.fixtures.generate

These four are generated because they can be: a failure shape is defined by a
structural property, not by a bank's layout. A real DBS statement cannot be
generated, which is why the happy path needs documents you supply.

The four map one to one onto the recovery paths on screen 03c, and each is a
different kind of unreadable:

  encrypted        has a text layer, needs a password first
  scanned          is an image, so there is no text to extract at all
  not_a_statement  is a perfectly readable PDF of the wrong thing
  malformed        is not a valid PDF

Written to ``tests/fixtures/pdfs/generated/``, which is gitignored: they are
reproducible from this script, so committing binaries would only add noise to
diffs.
"""

from __future__ import annotations

from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "pdfs" / "generated"


def _write_text_pdf(path: Path, lines: list[str]) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    for line in lines:
        pdf.drawString(60, y, line)
        y -= 18
    pdf.save()


def make_encrypted(path: Path) -> None:
    """A readable statement behind a password.

    Common in Singapore: several banks send statements encrypted with the last
    four digits of an identity number. Screen 03c asks for the password, uses it
    once in memory, and never stores it.
    """
    from pypdf import PdfReader, PdfWriter

    plain = path.with_suffix(".plain.pdf")
    _write_text_pdf(
        plain,
        [
            "EXAMPLE BANK - CARD STATEMENT",
            "Statement period: 15 Jul 2026 to 14 Aug 2026",
            "Card ending 4429",
            "22 Jul  MCDONALDS-JUNCTION8      12.40",
            "24 Jul  FAIRPRICE FINEST NEX     48.10",
            "TOTAL                            60.50",
        ],
    )
    writer = PdfWriter(clone_from=str(plain))
    writer.encrypt("secret123")
    with path.open("wb") as handle:
        writer.write(handle)
    plain.unlink()


def make_scanned(path: Path) -> None:
    """An image with no text layer.

    Must be detected and refused rather than run through OCR. Optical
    recognition puts character-level error into amounts, and an 8 read as a 3
    passes every type check, ties to nothing, and silently corrupts a month
    (ADR-002, TC-FAIL-005).
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    draw.text((80, 80), "EXAMPLE BANK - CARD STATEMENT", fill="black")
    draw.text((80, 120), "22 Jul  MCDONALDS-JUNCTION8   12.40", fill="black")
    draw.text((80, 160), "TOTAL                          12.40", fill="black")
    image.save(path, "PDF", resolution=150.0)


def make_not_a_statement(path: Path) -> None:
    """A valid, readable PDF that is not a bank statement.

    The interesting failure of the three: nothing is wrong with the file, so
    only the content can reject it. A parser that finds no rows must say "this
    is not a statement" rather than "this statement has no transactions".
    """
    _write_text_pdf(
        path,
        [
            "TENANCY AGREEMENT",
            "This agreement is made between the parties on 1 August 2026.",
            "1. The tenant shall pay rent monthly in advance.",
        ],
    )


def make_malformed(path: Path) -> None:
    """Bytes that are not a PDF.

    Truncated downloads and renamed files both land here. Must fail as a named
    extraction failure, not as an unhandled exception.
    """
    path.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    make_encrypted(OUTPUT_DIR / "encrypted.pdf")
    make_scanned(OUTPUT_DIR / "scanned.pdf")
    make_not_a_statement(OUTPUT_DIR / "not_a_statement.pdf")
    make_malformed(OUTPUT_DIR / "malformed.pdf")
    print(f"Wrote four failure-shape fixtures to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
