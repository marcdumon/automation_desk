"""White space beside the pages of a PDF, for notes: the pages are widened, their content stays as it is."""

import io

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import RectangleObject

SIDES = ('left', 'right', 'both')


def add_margin(content: bytes, side: str, percent: float) -> bytes:
    """The PDF with white space added on `side` ('left', 'right' or 'both') of every page, `percent` of the width the reader
    sees each; on a page turned a quarter the space goes where the reader sees left and right."""
    if side not in SIDES:
        raise ValueError(f"The side must be left, right or both, not '{side}'.")
    if not 1 <= percent <= 300:
        raise ValueError('The white space must be between 1 and 300 percent of the page width.')
    try:
        reader = PdfReader(io.BytesIO(content))
        if not len(reader.pages):
            raise ValueError('no pages')
    except (PdfReadError, ValueError, OSError) as error:
        raise ValueError('That file is not a PDF, or it is damaged.') from error
    writer = PdfWriter(clone_from=reader)
    for page in writer.pages:
        left, bottom, right, top = (float(v) for v in page.cropbox)
        turn = (page.rotation or 0) % 360
        seen_width = (top - bottom) if turn in (90, 270) else (right - left)
        extra = seen_width * percent / 100
        # CLAUDE> where the reader's left and right edges are in the page's own coordinates, per quarter turn clockwise
        seen_left, seen_right = {0: ('left', 'right'), 90: ('bottom', 'top'), 180: ('right', 'left'), 270: ('top', 'bottom')}[turn]
        edges = {'left': left, 'bottom': bottom, 'right': right, 'top': top}
        for seen in (seen_left, seen_right):
            if side in ('both', 'left' if seen == seen_left else 'right'):
                edges[seen] += -extra if seen in ('left', 'bottom') else extra
        box = RectangleObject([edges['left'], edges['bottom'], edges['right'], edges['top']])
        page.mediabox = box
        page.cropbox = box
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
