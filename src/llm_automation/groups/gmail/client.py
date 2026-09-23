"""Gmail API helpers shared by the Gmail group's standard tasks.

Mail content is shown to the user; only Find a mail sends the best matching mails to the model, to answer a question.
"""

import base64
import html
import re
from datetime import datetime
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from googleapiclient.discovery import Resource

HEADERS = ['From', 'To', 'Subject', 'Date']


def labels(svc: Resource) -> list[dict]:
    """All labels, system and user."""
    return svc.users().labels().list(userId='me').execute().get('labels', [])


def message_ids(svc: Resource, query: str, label_ids: list[str], limit: int) -> tuple[list[dict], bool]:
    """Ids of messages matching a Gmail search, newest first, and whether there were more than `limit`."""
    found, token = [], None
    while len(found) < limit:
        page = svc.users().messages().list(userId='me', q=query or None, labelIds=label_ids or None, pageToken=token,
                                           maxResults=min(500, limit - len(found))).execute()
        found += page.get('messages', [])
        if not (token := page.get('nextPageToken')):
            return found, False
    return found[:limit], True


def metadata(svc: Resource, message_id: str) -> dict:
    """Sender, recipients, subject, date, labels and Gmail's preview text of one message."""
    raw = svc.users().messages().get(userId='me', id=message_id, format='metadata', metadataHeaders=HEADERS).execute()
    headers = {h['name']: h['value'] for h in raw.get('payload', {}).get('headers', [])}
    return {'id': raw['id'], 'thread_id': raw.get('threadId', ''), 'labels': raw.get('labelIds', []),
            'from': headers.get('From', ''), 'to': headers.get('To', ''), 'subject': headers.get('Subject', ''),
            'snippet': clean_text(raw.get('snippet', '')), 'received_ms': int(raw.get('internalDate', 0))}


# CLAUDE> Gmail pads snippets with invisible characters (zero-width joiners, combining grapheme joiners)
_INVISIBLE = re.compile('[\u034f\u200b-\u200f\u2060\ufeff\u00ad]')


def clean_text(text: str) -> str:
    """Readable text: HTML entities decoded, invisible padding removed, spaces collapsed."""
    return ' '.join(_INVISIBLE.sub('', html.unescape(text)).split())


def _decode(data: str) -> str:
    """A base64url body part as text."""
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8', errors='replace')


def body_text(svc: Resource, message_id: str) -> str:
    """The readable text of one mail: its plain-text part, or its HTML part without markup."""
    raw = svc.users().messages().get(userId='me', id=message_id, format='full').execute()
    plain, markup = [], []

    def walk(part: dict) -> None:
        data = part.get('body', {}).get('data')
        if data and part.get('mimeType') == 'text/plain':
            plain.append(_decode(data))
        elif data and part.get('mimeType') == 'text/html':
            markup.append(_decode(data))
        for child in part.get('parts', []):
            walk(child)

    walk(raw.get('payload', {}))
    if plain:
        return _INVISIBLE.sub('', '\n'.join(plain))
    text = re.sub(r'(?is)<(script|style).*?</\1>', ' ', '\n'.join(markup))
    text = re.sub(r'(?i)<br\s*/?>|</p>|</div>|</tr>|</li>', '\n', text)
    return _INVISIBLE.sub('', html.unescape(re.sub(r'<[^>]+>', ' ', text)))


def received(meta: dict, tz: ZoneInfo) -> str:
    """When a message arrived, for previews: '23 Sep 2026 10:12'."""
    return datetime.fromtimestamp(meta['received_ms'] / 1000, tz).strftime('%d %b %Y %H:%M') if meta['received_ms'] else '—'


def sender(meta: dict) -> str:
    """The sender's name, or address when there is no name."""
    name, address = parseaddr(meta['from'])
    return name or address or '—'


def gmail_link(thread_id: str) -> str:
    """A link that opens the conversation in Gmail."""
    return f'https://mail.google.com/mail/u/0/#all/{thread_id}'
