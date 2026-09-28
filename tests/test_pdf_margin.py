"""Adding white space beside the pages of a PDF: the side the reader sees, as a share of the page width."""

import io

import pytest
from pypdf import PdfReader, PdfWriter

from automation_desk.tools.pdf_margin import add_margin


def pdf(*pages: tuple[float, float, int]) -> bytes:
    """A PDF of blank pages (width, height, rotation)."""
    writer = PdfWriter()
    for width, height, rotation in pages:
        writer.add_blank_page(width, height).rotate(rotation)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def boxes(content: bytes) -> list[tuple[float, float, float, float]]:
    """Each page's visible box as (left, bottom, right, top)."""
    return [tuple(round(float(v)) for v in page.cropbox) for page in PdfReader(io.BytesIO(content)).pages]


def test_white_space_on_the_right_by_default_share() -> None:
    assert boxes(add_margin(pdf((600, 800, 0)), 'right', 33)) == [(0, 0, 798, 800)]


def test_left_and_both_sides() -> None:
    assert boxes(add_margin(pdf((600, 800, 0)), 'left', 50)) == [(-300, 0, 600, 800)]
    assert boxes(add_margin(pdf((600, 800, 0)), 'both', 10)) == [(-60, 0, 660, 800)]


def test_a_turned_page_gets_the_space_on_the_side_the_reader_sees() -> None:
    """A page turned a quarter clockwise shows its top edge on the right; its seen width is its height."""
    assert boxes(add_margin(pdf((600, 800, 90), (600, 800, 270)), 'right', 25)) == [(0, 0, 600, 1000), (0, -200, 600, 800)]


def test_a_bad_file_or_share_is_refused_in_plain_words() -> None:
    with pytest.raises(ValueError, match='not a PDF'):
        add_margin(b'hello', 'right', 33)
    with pytest.raises(ValueError, match='between 1 and 300'):
        add_margin(pdf((600, 800, 0)), 'right', 0)
