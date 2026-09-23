"""The Gmail group against a fake Gmail: searches built in code, mail never sent to the model."""

import base64

import pytest

from llm_automation.groups.base import UserError
from llm_automation.groups.gmail.select import MailSelectionArgs, build_query
from llm_automation.groups.gmail.tasks.draft_mail import DraftMail, DraftMailArgs
from llm_automation.groups.gmail.tasks.find_mail import FindMail, FindMailArgs
from llm_automation.groups.gmail.tasks.label_mail import LabelMail, LabelMailArgs
from llm_automation.groups.gmail.tasks.trash_mail import TrashMail

from .conftest import FakeGoogle

LABELS = [{'id': 'INBOX', 'name': 'INBOX', 'type': 'system'}, {'id': 'L_health', 'name': 'Health', 'type': 'user'}]
MAILS = {
    'm1': {'From': 'Lab <results@lab.example>', 'To': 'me@x.be', 'Subject': 'Your blood test results are ready',
           'labels': ['INBOX', 'L_health'], 'ms': 1790000000000,
           'body': 'Beste Marc,\n\n> De laboresultaten ontvangen we doorgaans binnen ongeveer 5 werkdagen na de bloedafname.'},
    'm2': {'From': 'Anna Peeters <anna@peeters.be>', 'To': 'me@x.be', 'Subject': 'Etentje', 'labels': ['INBOX'], 'ms': 1789000000000},
    'm3': {'From': 'me@x.be', 'To': 'Anna <anna.old@work.be>', 'Subject': 'Re: planning', 'labels': ['SENT'], 'ms': 1788000000000},
}


def gmail(results: dict[str, list[str]] | None = None) -> FakeGoogle:
    """A fake Gmail: `results` maps a search (exact query text) to message ids; anything else finds nothing."""
    def listing(userId: str, q: str | None, labelIds: list | None, pageToken: object, maxResults: int) -> dict:
        """messages.list by exact query."""
        return {'messages': [{'id': i} for i in (results or {}).get(q or '', [])]}

    def get(userId: str, id: str, format: str, metadataHeaders: list | None = None) -> dict:
        """messages.get with headers, or with the body for format='full'."""
        m = MAILS[id]
        body = base64.urlsafe_b64encode(m.get('body', '').encode()).decode()
        return {'id': id, 'threadId': f't{id}', 'labelIds': m['labels'], 'snippet': m['Subject'], 'internalDate': str(m['ms']),
                'payload': {'headers': [{'name': k, 'value': m[k]} for k in ('From', 'To', 'Subject')],
                            'mimeType': 'text/plain', 'body': {'data': body}}}

    return FakeGoogle({
        'users.labels.list': lambda **_: {'labels': LABELS},
        'users.labels.create': lambda userId, body: {'id': 'L_new', 'name': body['name']},
        'users.messages.list': listing, 'users.messages.get': get,
        'users.messages.trash': lambda **_: {}, 'users.messages.batchModify': lambda **_: {},
        'users.drafts.create': lambda **_: {'id': 'd1'},
    })


def selection(**kwargs: object) -> dict:
    """Selection fields with nothing chosen unless given."""
    return {'status': 'ok', 'message': '', 'from_contains': [], 'subject_contains': [], 'words': [], 'label_name': '',
            'kind': '', 'state': 'any', 'has_attachment': False, 'older_than': '', 'newer_than': '', **kwargs}


def test_search_is_built_in_code_and_operators_cannot_be_injected() -> None:
    args = MailSelectionArgs(**selection(from_contains=['Zalando', 'noreply@bol.com'], subject_contains=['order status'],
                                         words=['in:trash', 'label:secret'], state='unread', older_than='2 weeks'))
    assert build_query(args) == ('(from:Zalando OR from:noreply@bol.com) subject:"order status" "in trash" "label secret" '
                                 'is:unread older_than:14d')


def test_newsletters_are_mails_with_an_unsubscribe_link() -> None:
    assert build_query(MailSelectionArgs(**selection(kind='newsletters', older_than='2 weeks'))) == 'unsubscribe older_than:14d'


def test_trash_lists_mails_and_trashes_only_the_ticked(make_ctx) -> None:
    fake = gmail({'from:Lab': ['m1'], 'from:Anna': ['m2']})
    ctx = make_ctx(fake, '')
    preview, payload = TrashMail().resolve(MailSelectionArgs(**selection(from_contains=['Lab'])), ctx)
    assert [(r.cells['From'], r.links['Open']) for r in preview.rows] == [('Lab', 'https://mail.google.com/mail/u/0/#all/tm1')]
    TrashMail().execute(payload, {'m1'}, ctx)
    assert [kw['id'] for name, kw in fake.calls if name == 'users.messages.trash'] == ['m1']
    with pytest.raises(UserError, match='Say which mails'):
        TrashMail().resolve(MailSelectionArgs(**selection()), ctx)


def test_label_creates_missing_label_and_archive_and_mark_read(make_ctx) -> None:
    fake = gmail({'': ['m1', 'm2']})
    ctx = make_ctx(fake, '')
    args = LabelMailArgs(**selection(label_name='Health'), add_labels=['Taxes'], remove_labels=['inbox'], mark='read')
    preview, payload = LabelMail().resolve(args, ctx)
    assert payload == {'add': [], 'remove': ['INBOX', 'UNREAD'], 'create': ['Taxes']}
    assert preview.summary.startswith('Create and add Taxes; remove inbox, unread')
    LabelMail().execute(payload, {'m1'}, ctx)
    modify = next(kw for name, kw in fake.calls if name == 'users.messages.batchModify')
    assert modify['body'] == {'ids': ['m1'], 'addLabelIds': ['L_new'], 'removeLabelIds': ['INBOX', 'UNREAD']}


def test_find_keeps_the_core_concept_and_answers_with_checked_quotes(make_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_automation.groups.gmail.tasks import find_mail

    both = '(bloedtest OR "blood test") (resultaat OR results)'
    fake = gmail({both: ['m1'], '(bloedtest OR "blood test")': ['m1'], '(resultaat OR results)': ['m2']})
    seen: dict = {}

    def answer(system: str, user: str, schema: type, **_: object) -> find_mail.MailAnswer:
        """Stand-in for the model: one real quote, one invented."""
        seen['user'] = user
        return find_mail.MailAnswer(answered=True, answer='Na ongeveer 5 werkdagen.', quotes=[
            find_mail.Quote(mail=1, quote='De laboresultaten ontvangen we doorgaans binnen ongeveer 5 werkdagen na de bloedafname.'),
            find_mail.Quote(mail=1, quote='Uw resultaten zijn binnen 24 uur klaar.')])

    monkeypatch.setattr(find_mail, 'ask', answer)
    args = FindMailArgs(status='ok', message='', concepts=[['bloedtest', 'blood test'], ['resultaat', 'results']],
                        label_name='', from_contains=[], newer_than='')
    preview, _ = FindMail().resolve(args, make_ctx(fake, 'wanneer resultaat bloedtest'))
    assert [r.id for r in preview.rows] == ['m1'], "the mail matching only 'results' is left out"
    assert 'Question: wanneer resultaat bloedtest' in seen['user'] and 'Mail 1' in seen['user']
    assert preview.answer == 'Na ongeveer 5 werkdagen.'
    assert [e.verified for e in preview.evidence] == [True, False], 'the invented quote is flagged'
    assert preview.notes[0].startswith('Not every quote was found word for word')
    assert preview.rows[0].note.endswith('the answer quotes it')


def test_draft_finds_the_address_in_your_mail_and_saves_a_draft(make_ctx) -> None:
    fake = gmail({'(from:anna OR to:anna) newer_than:2y': ['m2', 'm3']})
    ctx = make_ctx(fake, '')
    args = DraftMailArgs(status='ok', message='', to=['Anna'], subject='Te laat', body="Hoi Anna,\n\nIk ben 10 minuten te laat.")
    preview, payload = DraftMail().resolve(args, ctx)
    assert payload['to'] == ['anna@peeters.be'] and 'anna.old@work.be' in ' '.join(preview.notes)
    preview, payload = DraftMail().adjust(payload, {'to': 'anna.old@work.be', 'subject': 'Te laat', 'body': 'Hoi'}, ctx)
    DraftMail().execute(payload, {'draft'}, ctx)
    raw = next(kw for name, kw in fake.calls if name == 'users.drafts.create')['body']['message']['raw']
    text = base64.urlsafe_b64decode(raw).decode()
    assert 'To: anna.old@work.be' in text and 'Subject: Te laat' in text
    preview, _ = DraftMail().adjust(payload, {'to': 'Anna', 'subject': 'x', 'body': 'y'}, ctx)
    assert not preview.rows[0].selectable, 'a name without an address cannot be saved'


def test_archiving_looks_only_in_the_inbox(make_ctx) -> None:
    fake = gmail({'unsubscribe': ['m2']})
    args = LabelMailArgs(**selection(kind='newsletters'), add_labels=[], remove_labels=['inbox'], mark='')
    LabelMail().resolve(args, make_ctx(fake, ''))
    listed = next(kw for name, kw in fake.calls if name == 'users.messages.list')
    assert listed['labelIds'] == ['INBOX']
