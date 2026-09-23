"""Standard task: add or remove labels, mark mails read or unread."""

from typing import Literal

from pydantic import Field

from automation_desk.groups.base import Context, Preview, StandardTask, UserError
from automation_desk.groups.gmail.client import labels
from automation_desk.groups.gmail.select import MAX_MAILS, SYSTEM_LABELS, MailSelectionArgs, resolve_label, select
from automation_desk.groups.gmail.tasks._rows import MAIL_COLUMNS, mail_row

BATCH = 1000


class LabelMailArgs(MailSelectionArgs):
    """Which mails, and what changes."""

    add_labels: list[str] = Field(description="Labels to put on the mails, as the user named them, e.g. ['Taxes']. Empty "
                                              'list when none.')
    remove_labels: list[str] = Field(description="Labels to take off, as the user named them; 'inbox' archives. Empty list "
                                                 'when none.')
    mark: Literal['', 'read', 'unread'] = Field(description="'read' or 'unread' when the user asks to mark mails; else ''.")


class LabelMail(StandardTask):
    """Change the labels and read state of the mails you describe."""

    id = 'label_mail'
    name = 'Label or mark mail'
    description = ('Only when the user asks to CHANGE mails: add or remove labels, archive, or mark read or unread. '
                   "Removing the label 'inbox' archives. A label that does not exist yet is created. Not for questions "
                   'or searches.')
    example = 'label all mail from the accountant as Taxes and mark it read'
    Args = LabelMailArgs
    guidance = ("The mails to change are chosen by from_contains, subject_contains, words, label_name, state, age; what "
                "changes goes in add_labels, remove_labels and mark. 'Archive' means remove_labels ['inbox'].")

    def resolve(self, args: LabelMailArgs, ctx: Context) -> tuple[Preview, dict]:
        """List the mails with what would change, creating nothing yet."""
        if not (args.add_labels or args.remove_labels or args.mark):
            raise UserError('Say what should change: a label to add or remove, or mark as read or unread.')
        existing = {' '.join(lb['name'].split()).casefold(): lb for lb in labels(ctx.google('gmail', 'v1'))}
        add, create, remove = [], [], []
        for name in (n for n in args.add_labels if n.strip()):
            key = ' '.join(name.split()).casefold()
            if key in SYSTEM_LABELS or key in existing:
                add.append(resolve_label(ctx, name) if key in SYSTEM_LABELS else existing[key])
            else:
                create.append(' '.join(name.split()))
        remove = [resolve_label(ctx, n) for n in args.remove_labels if n.strip()]
        if args.mark == 'read':
            remove.append({'id': 'UNREAD', 'name': 'unread'})
        elif args.mark == 'unread':
            add.append({'id': 'UNREAD', 'name': 'unread'})

        if 'INBOX' in {lb['id'] for lb in remove} and not args.label_name.strip():
            # CLAUDE> archiving only concerns mails still in the inbox
            args = args.model_copy(update={'label_name': 'inbox'})
        mails, search, more = select(args, ctx)
        add_ids, remove_ids = {lb['id'] for lb in add}, {lb['id'] for lb in remove}
        rows = []
        for mail in mails:
            current = set(mail['labels'])
            done = not create and add_ids <= current and not (remove_ids & current)
            rows.append(mail_row(mail, ctx.tz, selectable=not done, selected=not done, note='already so' if done else ''))
        change = [f"add {', '.join(lb['name'] for lb in add)}" if add else '',
                  f"create and add {', '.join(create)}" if create else '',
                  f"remove {', '.join(lb['name'] for lb in remove)}" if remove else '']
        notes = [f'Gmail search: {search}']
        if more:
            notes.insert(0, f'More than {MAX_MAILS} mails match; only the newest {MAX_MAILS} are listed.')
        what = '; '.join(c for c in change if c)
        summary = f'{what[:1].upper()}{what[1:]} on {sum(r.selectable for r in rows)} mail(s)'
        return Preview(summary=summary, columns=MAIL_COLUMNS, rows=rows, notes=notes), {
            'add': sorted(add_ids), 'remove': sorted(remove_ids), 'create': create}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Create missing labels, then change the selected mails in batches."""
        svc = ctx.google('gmail', 'v1')
        results = []
        add = list(payload['add'])
        for name in payload['create']:
            label = svc.users().labels().create(userId='me', body={'name': name}).execute()
            add.append(label['id'])
            results.append(f'Created label "{name}".')
        ids = sorted(selected)
        for start in range(0, len(ids), BATCH):
            chunk = ids[start:start + BATCH]
            svc.users().messages().batchModify(userId='me', body={'ids': chunk, 'addLabelIds': add,
                                                                  'removeLabelIds': payload['remove']}).execute()
        results.append(f'Changed {len(ids)} mail(s).')
        return results
