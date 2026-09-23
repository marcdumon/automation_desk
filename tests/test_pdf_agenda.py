"""Agenda PDFs from a link, an attached file or a mail: code reads text and dates, the model lists events by number."""

import base64
import io
import re
from datetime import date
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter

from automation_desk.groups.base import UserError
from automation_desk.groups.calendar import web_page
from automation_desk.groups.calendar.organisers import Organiser
from automation_desk.groups.calendar.pdf import PdfError, pdf_text
from automation_desk.groups.calendar.tasks import add_events_from_web as module
from automation_desk.groups.calendar.tasks.add_events_from_web import AddEventsArgs, AddEventsFromWeb
from automation_desk.groups.calendar.web_page import MONTHS, Extraction, FoundEvent, read_events

from .conftest import TODAY, TZ, FakeGoogle

PDF = (Path(__file__).parent / 'fixtures' / 'agenda.pdf').read_bytes()
MONTH_WORDS = '|'.join(sorted(MONTHS, key=len, reverse=True))


def one_event(**kwargs: object) -> Extraction:
    """The model's answer: the open weekend on D1 to D2 at T1, unless overridden."""
    fields = {'title': 'Open atelierweekend', 'running_until': False, 'date_span': 1, 'end_date_span': 2, 'time_span': 1,
              'end_time_span': 2, 'location': 'Kanal', 'description': '', 'link_span': -1, 'matches_filter': True,
              'end_text': '', **kwargs}
    return Extraction(events=[FoundEvent(**fields)])


def blank_pdf() -> bytes:
    """A PDF with a page but no text, like a scan."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_wrapped_table_cells_are_joined_again() -> None:
    text = pdf_text(PDF, MONTH_WORDS)
    assert 'za 10 en zo 11 oktober' in text and re.search(r'10u \S 18u', text)


def test_a_scan_or_a_non_pdf_is_said_plainly() -> None:
    with pytest.raises(PdfError, match='only images'):
        pdf_text(blank_pdf(), MONTH_WORDS)


def test_a_link_that_is_a_pdf_is_read_as_one(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def answer(system: str, user: str, schema: type, **_: object) -> Extraction:
        """Stand-in for the model."""
        seen['text'] = user
        return one_event()

    monkeypatch.setattr(web_page, 'ask', answer)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=PDF, headers={'content-type': 'application/pdf'}))
    with httpx.Client(transport=transport) as http:
        events, notes, html = read_events('https://kanal.brussels/programma.pdf', TODAY, TZ, '', 1, http)
    assert '[D1, D2: 10 en zo 11 oktober]' in seen['text']
    event = events[0]
    assert (event.start, event.end) == (date(2026, 10, 10), date(2026, 10, 11))
    assert (event.start_time.hour, event.end_time.hour) == (10, 18)
    assert 'is a PDF' in notes[0] and 'Programma herfst' in html


def calendar_and_gmail(mails: dict[str, list[str]] | None = None) -> FakeGoogle:
    """A fake with calendar Exhibitions and one mail 'Programma herfst' from Kanal carrying the agenda PDF."""
    data = base64.urlsafe_b64encode(PDF).decode()
    return FakeGoogle({
        'calendarList.list': lambda **_: {'items': [{'id': 'cal-x', 'summary': 'Exhibitions'}]},
        'events.list': lambda **_: {'items': []},
        'users.messages.list': lambda **kw: {'messages': [{'id': 'm1'}] if 'from:Kanal' in (kw.get('q') or '') else []},
        'users.messages.get': lambda **kw: {
            'id': 'm1', 'threadId': 't1', 'labelIds': [], 'snippet': '', 'internalDate': '1790000000000',
            'payload': {'headers': [{'name': 'From', 'value': 'Kanal <info@kanal.brussels>'},
                                    {'name': 'Subject', 'value': 'Programma herfst'}],
                        'parts': [{'filename': 'programma.pdf', 'mimeType': 'application/pdf', 'body': {'attachmentId': 'a1'}}]}},
        'users.messages.attachments.get': lambda **_: {'data': data},
    })


def args(**kwargs: object) -> AddEventsArgs:
    """Import arguments for calendar Exhibitions, overridable."""
    base = {'status': 'ok', 'message': '', 'calendar_name': 'exhibitions', 'exclude_weekdays': [], 'date_range': '',
            'text_filter': '', 'follow_pages': False, 'pdf_mail_from': [], 'pdf_mail_subject': []}
    return AddEventsArgs(**{**base, **kwargs})


@pytest.fixture
def quiet_model(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The model and the organiser lookup as stand-ins; records which site the organiser was asked for."""
    seen: dict = {}
    monkeypatch.setattr(web_page, 'ask', lambda *a, **k: one_event())

    def organiser(url: str, html: str, http: object = None, site: str | None = None) -> Organiser:
        """Stand-in for organiser_for."""
        seen['site'] = site
        return Organiser(site or url, 'Kanal', 'Akenkaai 2, 1000 Brussel')

    monkeypatch.setattr(module, 'organiser_for', organiser)
    return seen


def test_an_attached_pdf(make_ctx, quiet_model: dict) -> None:
    ctx = make_ctx(calendar_and_gmail(), 'add these events to calendar Exhibitions')
    ctx.files = [('programma.pdf', PDF)]
    preview, payload = AddEventsFromWeb().resolve(args(), ctx)
    row = preview.rows[0]
    assert (row.cells['Title'], row.cells['Date']) == ('Kanal: Open atelierweekend', 'Sat 10 Oct 2026 → Sun 11 Oct 2026')
    assert payload['url'].startswith('file:') and quiet_model['site'] == 'file programma.pdf'
    assert 'programma.pdf: 1 event(s)' in preview.notes[0]
    ctx.files = [('notes.txt', b'hello')]
    with pytest.raises(UserError, match=r'notes\.txt is not a PDF'):
        AddEventsFromWeb().resolve(args(), ctx)


def test_a_pdf_in_a_mail(make_ctx, quiet_model: dict) -> None:
    fake = calendar_and_gmail()
    ctx = make_ctx(fake, 'add the events from the PDF in the mail from Kanal to calendar Exhibitions')
    preview, payload = AddEventsFromWeb().resolve(args(pdf_mail_from=['Kanal']), ctx)
    assert payload['url'] == 'https://mail.google.com/mail/u/0/#all/t1' and quiet_model['site'] == 'kanal.brussels'
    assert "programma.pdf from the mail 'Programma herfst' of Kanal" in preview.notes[0]
    assert preview.rows[0].cells['Source'] == 'https://mail.google.com/mail/u/0/#all/t1'
    with pytest.raises(UserError, match='No mail with a PDF matches'):
        AddEventsFromWeb().resolve(args(pdf_mail_from=['Bozar']), ctx)
