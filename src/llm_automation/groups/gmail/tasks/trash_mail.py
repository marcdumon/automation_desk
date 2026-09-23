"""Standard task: move mails to the bin."""

from llm_automation.groups.base import Context, Preview, StandardTask
from llm_automation.groups.gmail.select import MAX_MAILS, MailSelectionArgs, select
from llm_automation.groups.gmail.tasks._rows import MAIL_COLUMNS, mail_row


class TrashMail(StandardTask):
    """Move the mails you describe to the bin."""

    id = 'trash_mail'
    name = 'Trash mail'
    description = 'Moves the mails you describe to the bin: by sender, subject, words, label, unread, attachments or age.'
    example = 'trash all mail from Zalando older than a month'
    Args = MailSelectionArgs
    guidance = "'Delete' and 'remove' mail also mean trash."

    def resolve(self, args: MailSelectionArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the matching mails."""
        mails, search, more = select(args, ctx)
        notes = [f'Gmail search: {search}', 'Gmail empties the bin after 30 days; until then you can move mails back.']
        if more:
            notes.insert(0, f'More than {MAX_MAILS} mails match; only the newest {MAX_MAILS} are listed. Run it again for the rest.')
        rows = [mail_row(m, ctx.tz) for m in mails]
        subjects = {m['id']: m['subject'] or '(no subject)' for m in mails}
        return Preview(summary=f'Move {len(rows)} mail(s) to the bin', columns=MAIL_COLUMNS, rows=rows, notes=notes), {
            'subjects': subjects}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Trash each selected mail."""
        svc = ctx.google('gmail', 'v1')
        results = []
        for message_id, subject in payload['subjects'].items():
            if message_id in selected:
                svc.users().messages().trash(userId='me', id=message_id).execute()
                results.append(f'Moved "{subject}" to the bin.')
        return results
