"""Standard task: write a draft mail from a sentence. The app never sends; the draft waits in Gmail's Drafts.

The model writes subject and text from the user's words. Recipients named by name are looked up in code in the user's
own mail, so no address book goes to the model; the preview shows the address found and lets the user correct it.
"""

import base64
import re
from collections import Counter
from email.message import EmailMessage
from email.utils import getaddresses

from pydantic import Field

from llm_automation.groups.base import Context, Preview, PreviewOption, Row, StandardTask, TaskArgs, UserError
from llm_automation.groups.gmail.client import message_ids, metadata
from llm_automation.groups.gmail.select import clean_term

ADDRESS = re.compile(r'^[^@\s,]+@[^@\s,]+\.[^@\s,]+$')
LOOKBACK = 40


class DraftMailArgs(TaskArgs):
    """Who it is for and what it says."""

    to: list[str] = Field(description="Recipients as the user named them: names or addresses, one per entry, e.g. ['Anna'].")
    subject: str = Field(description="A short subject in the language of the user's sentence.")
    body: str = Field(description="The mail text in the language of the user's sentence, saying what the user asked and "
                                  'nothing more; no invented facts, dates or promises. Plain text, with a greeting.')


def find_address(ctx: Context, name: str) -> tuple[str, list[str]]:
    """The address the user most often exchanges mail with under this name, and the other candidates."""
    if ADDRESS.match(name.strip()):
        return name.strip(), []
    wanted = ' '.join(name.split()).casefold()
    svc = ctx.google('gmail', 'v1')
    term = clean_term(wanted)
    found, _ = message_ids(svc, f'(from:{term} OR to:{term}) newer_than:2y', [], LOOKBACK)
    counts: Counter[str] = Counter()
    for message in found:
        meta = metadata(svc, message['id'])
        for display, address in getaddresses([meta['from'], meta['to']]):
            if address and (wanted in display.casefold() or wanted in address.casefold()):
                counts[address.lower()] += 1
    ranked = [address for address, _ in counts.most_common()]
    return (ranked[0], ranked[1:4]) if ranked else ('', [])


class DraftMail(StandardTask):
    """Put a ready-to-send draft in Gmail's Drafts."""

    id = 'draft_mail'
    name = 'Draft an email'
    description = ('Writes a draft from your sentence and puts it in Gmail Drafts; the app never sends. Recipients named '
                   'by name are looked up in your own mail, and you can correct them before saving.')
    example = "draft a mail to Anna: I'll be 10 minutes late tomorrow"
    Args = DraftMailArgs

    def resolve(self, args: DraftMailArgs, ctx: Context) -> tuple[Preview, dict]:
        """Look up the recipients and show the draft."""
        if not args.to:
            raise UserError('Say who the mail is for.')
        addresses, notes = [], []
        for name in args.to:
            address, others = find_address(ctx, name)
            if not address:
                notes.append(f"No address found for '{name}' in your mail of the last two years: type it in the To field.")
                continue
            addresses.append(address)
            if address.lower() != name.strip().lower():
                extra = f" Others with that name: {', '.join(others)}." if others else ''
                notes.append(f"'{name}' is {address}: the address you exchanged most mail with under that name.{extra}")
        return self._compose(', '.join(addresses), args.subject.strip(), args.body.strip(), notes)

    def adjust(self, payload: dict, options: dict[str, str], ctx: Context) -> tuple[Preview, dict]:
        """Use the recipients, subject and text as the user edited them."""
        return self._compose(options.get('to', ''), options.get('subject', ''), options.get('body', ''), [])

    @staticmethod
    def _compose(to: str, subject: str, body: str, notes: list[str]) -> tuple[Preview, dict]:
        """The preview of one draft; it can be saved only when every recipient is a valid address."""
        recipients = [a.strip() for a in to.split(',') if a.strip()]
        valid = bool(recipients) and all(ADDRESS.match(a) for a in recipients)
        if recipients and not valid:
            notes = [*notes, 'Every recipient needs a full address like name@example.org.']
        row = Row(id='draft', selectable=valid, selected=valid, note='' if valid else 'fill in the To field first',
                  cells={'To': to or '—', 'Subject': subject or '(no subject)', 'Text': body})
        options = [PreviewOption(name='to', label='To', value=to, help='Addresses, separated by commas.'),
                   PreviewOption(name='subject', label='Subject', value=subject),
                   PreviewOption(name='body', label='Text', value=body, multiline=True)]
        preview = Preview(summary='Save a draft in Gmail (nothing is sent)', columns=['To', 'Subject', 'Text'], rows=[row],
                          notes=[*notes, 'The draft goes to Gmail Drafts; you send it yourself from Gmail.'], options=options)
        return preview, {'to': recipients, 'subject': subject, 'body': body}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Create the draft."""
        if 'draft' not in selected:
            return ['No draft saved.']
        message = EmailMessage()
        message['To'] = ', '.join(payload['to'])
        message['Subject'] = payload['subject']
        message.set_content(payload['body'])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        ctx.google('gmail', 'v1').users().drafts().create(userId='me', body={'message': {'raw': raw}}).execute()
        return [f'Draft "{payload["subject"]}" saved in Gmail Drafts: https://mail.google.com/mail/u/0/#drafts']
