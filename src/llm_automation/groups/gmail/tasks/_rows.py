"""How a mail looks in a preview row; shared by the Gmail standard tasks."""

from zoneinfo import ZoneInfo

from llm_automation.groups.base import Row
from llm_automation.groups.gmail.client import gmail_link, received, sender

MAIL_COLUMNS = ['Received', 'From', 'Subject', 'Preview', 'Open']


def mail_row(meta: dict, tz: ZoneInfo, **row: object) -> Row:
    """One mail: when, from whom, subject, Gmail's preview text and a link to open it in Gmail."""
    snippet = meta['snippet'][:160] + ('…' if len(meta['snippet']) > 160 else '')
    return Row(id=meta['id'], links={'Open': gmail_link(meta['thread_id'])},
               cells={'Received': received(meta, tz), 'From': sender(meta), 'Subject': meta['subject'] or '(no subject)',
                      'Preview': snippet or '—', 'Open': 'Open in Gmail'}, **row)
