"""Standard task: add, rename or remove digest subjects."""

from pydantic import Field

from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs, UserError
from automation_desk.groups.news import store


class SubjectsArgs(TaskArgs):
    """Subject changes as the user named them."""

    add: list[str] = Field(description="Subjects to add, e.g. ['AI']. Else [].")
    remove: list[str] = Field(description='Subjects to remove. Else [].')
    rename_from: list[str] = Field(description="Subjects to rename, e.g. ['Tech']. Else [].")
    rename_to: list[str] = Field(description="Their new names, same order, e.g. ['Technology']. Else [].")


class ManageSubjects(StandardTask):
    """Change the subjects the digest is grouped by."""

    id = 'manage_subjects'
    name = 'Add, rename or remove subjects'
    description = 'Changes the list of subjects the daily digest groups headlines by.'
    example = 'add subject AI and rename Tech to Technology'
    Args = SubjectsArgs

    def resolve(self, args: SubjectsArgs, ctx: Context) -> tuple[Preview, dict]:
        """The subject list before and after."""
        if len(args.rename_from) != len(args.rename_to):
            raise UserError('Say each rename as "rename X to Y".')
        current = store.subjects()
        renames = {' '.join(a.split()).casefold(): ' '.join(b.split()) for a, b in zip(args.rename_from, args.rename_to, strict=True)}
        removed = {' '.join(r.split()).casefold() for r in args.remove}
        after = [renames.get(s.casefold(), s) for s in current if s.casefold() not in removed]
        after += [a for a in (' '.join(x.split()) for x in args.add) if a and a not in after]
        row = Row(id='subjects', cells={'Before': ', '.join(current) or '—', 'After': ', '.join(after) or '—'})
        return Preview(summary='Change the subject list', columns=['Before', 'After'], rows=[row]), {'after': after}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Store the new list."""
        if 'subjects' not in selected:
            return ['Nothing changed.']
        store.set_subjects(payload['after'])
        return [f"Subjects: {', '.join(payload['after']) or 'none'}."]
