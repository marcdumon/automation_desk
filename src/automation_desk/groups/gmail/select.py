"""Which mails a sentence means: one shared selection for the Gmail group's standard tasks.

The model copies the user's words into these fields; code builds the Gmail search from them, resolves the label and
reads the matching mails' headers. Mail never goes to the model.
"""

from typing import Literal

from pydantic import Field

from automation_desk.dates import gmail_age
from automation_desk.groups.base import Context, TaskArgs, UserError, match_name
from automation_desk.groups.gmail.client import labels, message_ids, metadata

MAX_MAILS = 200
# CLAUDE> system labels people name in plain words; Gmail's own ids differ from what the user types
KINDS = {'newsletters': 'unsubscribe', 'promotions': 'category:promotions', 'social': 'category:social',
         'updates': 'category:updates', 'forums': 'category:forums'}
SYSTEM_LABELS = {'inbox': 'INBOX', 'starred': 'STARRED', 'important': 'IMPORTANT', 'unread': 'UNREAD', 'sent': 'SENT'}


class MailSelectionArgs(TaskArgs):
    """Which mails: sender, subject, words, label, state and age, all as the user said them."""

    from_contains: list[str] = Field(description="Senders as the user named them (names, addresses or domains), one per "
                                                 "entry: ['Zalando'], ['anna@example.org', 'Bob']. Empty list when not said.")
    subject_contains: list[str] = Field(description='Words or phrases the subject must contain, one per entry; a mail '
                                                    'matches if its subject contains any. Empty list when not said.')
    words: list[str] = Field(description="Other words the mail must contain, copied from the sentence, e.g. ['invoice']. "
                                         'Empty list when not said.')
    label_name: str = Field(description="A label the mails carry, as the user named it, e.g. 'Health', 'Receipts', 'inbox'. "
                                        'Empty when not said.')
    kind: Literal['', 'newsletters', 'promotions', 'social', 'updates', 'forums'] = Field(
        description="A kind of mail the user names: 'newsletters' (anything with an unsubscribe link), 'promotions', "
                    "'social', 'updates', 'forums'. Empty when the user names no kind.")
    state: Literal['any', 'unread', 'read'] = Field(description="'unread' or 'read' only when the user says so; else 'any'.")
    has_attachment: bool = Field(description='True only when the user asks for mails with attachments.')
    older_than: str = Field(description="Age the mails must exceed, as the user said it: '2 weeks', 'a month', '30 days'. "
                                        'Empty when not said. Never a date.')
    newer_than: str = Field(description="Maximum age, as the user said it: '3 days', 'a week'. Empty when not said. "
                                        'Never a date.')


def clean_term(term: str) -> str:
    """A search term without Gmail operators or quotes, quoted when it has spaces; the model cannot inject operators."""
    term = ' '.join(term.replace('"', ' ').replace(':', ' ').replace('(', ' ').replace(')', ' ').split())
    return f'"{term}"' if ' ' in term else term


def any_of(field: str, terms: list[str]) -> str:
    """'(from:a OR from:b)' for several terms, 'from:a' for one."""
    parts = [f'{field}:{t}' if field else t for t in (clean_term(t) for t in terms if t.strip()) if t]
    return parts[0] if len(parts) == 1 else f"({' OR '.join(parts)})" if parts else ''


def resolve_label(ctx: Context, name: str) -> dict:
    """The label the user named: a system label by its plain name, otherwise one of theirs, matched in code."""
    wanted = ' '.join(name.split()).casefold()
    if wanted in SYSTEM_LABELS:
        return {'id': SYSTEM_LABELS[wanted], 'name': name.strip()}
    user_labels = [lb for lb in labels(ctx.google('gmail', 'v1')) if lb.get('type') == 'user']
    return match_name(name, user_labels, 'name', 'label')


def build_query(args: MailSelectionArgs) -> str:
    """The Gmail search for the selection, built in code."""
    parts = [any_of('from', args.from_contains), any_of('subject', args.subject_contains),
             ' '.join(clean_term(w) for w in args.words if w.strip())]
    if args.kind:
        # CLAUDE> Gmail's own categories; a newsletter is recognised by its unsubscribe link
        parts.append(KINDS[args.kind])
    if args.state != 'any':
        parts.append(f'is:{args.state}')
    if args.has_attachment:
        parts.append('has:attachment')
    if args.older_than.strip():
        parts.append(f'older_than:{gmail_age(args.older_than)}')
    if args.newer_than.strip():
        parts.append(f'newer_than:{gmail_age(args.newer_than)}')
    return ' '.join(p for p in parts if p)


def select(args: MailSelectionArgs, ctx: Context) -> tuple[list[dict], str, bool]:
    """The matching mails' headers, the search that was run (for the preview) and whether there were more than shown."""
    query = build_query(args)
    label = resolve_label(ctx, args.label_name) if args.label_name.strip() else None
    if not query and not label:
        raise UserError('Say which mails: a sender, subject, words, a label, unread, or an age like "older than 2 weeks".')
    svc = ctx.google('gmail', 'v1')
    found, more = message_ids(svc, query, [label['id']] if label else [], MAX_MAILS)
    shown = ' '.join(filter(None, (f"label:{label['name']}" if label else '', query)))
    if not found:
        raise UserError(f'No mail matches that. Gmail search: {shown}')
    return [metadata(svc, m['id']) for m in found], shown, more
