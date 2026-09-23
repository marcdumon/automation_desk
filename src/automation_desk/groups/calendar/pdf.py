"""Text of an agenda PDF, repaired where table cells wrapped, ready for the same date marking as web pages."""

import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PDF_BYTES = 20_000_000


class PdfError(ValueError):
    """A PDF the app cannot read as text."""


def is_pdf(content: bytes) -> bool:
    """Whether bytes are a PDF, by its signature rather than a file name."""
    return content[:5] == b'%PDF-'


def pdf_text(content: bytes, month_words: str) -> str:
    """The text of every page, with dates and time ranges that wrapped over two lines joined again.

    `month_words` is the regex alternation of month names the date reader knows, so the repair and the reader agree.
    """
    if len(content) > MAX_PDF_BYTES:
        raise PdfError(f'The PDF is larger than {MAX_PDF_BYTES // 1_000_000} MB.')
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(''):
            raise PdfError('The PDF is protected with a password.')
        pages = [page.extract_text() or '' for page in reader.pages]
    except PdfReadError as error:
        raise PdfError(f'The PDF could not be read: {error}') from error
    text = '\n\n'.join(pages)
    if len(re.sub(r'\s', '', text)) < 20:
        raise PdfError('The PDF holds only images (a scan), no text to read.')
    # CLAUDE> table cells wrap: 'za 10 en zo 11' / 'oktober' and '10u -' / '18u' belong on one line
    text = re.sub(rf'(\d{{1,2}}\.?)[ \t]*\n[ \t]*({month_words})\b', r'\1 \2', text, flags=re.IGNORECASE)
    text = re.sub(r"([-\u2013\u2014]|\btot|\bt/m|\bau|\bto)[ \t]*\n[ \t]*", r'\1 ', text, flags=re.IGNORECASE)
    return text
