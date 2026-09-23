"""Standard task: block or unblock topics the digest leaves out."""

from pydantic import Field

from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs, UserError
from automation_desk.groups.news import store


class BlockedArgs(TaskArgs):
    """Topics to block and to unblock, as the user named them."""

    add: list[str] = Field(description="Topics to block, as short English names, e.g. ['Sports', 'Showbiz']. Else [].")
    remove: list[str] = Field(description="Topics to unblock, e.g. ['Culture']. Else [].")


class ManageBlocked(StandardTask):
    """Keep topics out of the digest."""

    id = 'manage_blocked'
    name = 'Block or unblock topics'
    description = 'Changes the topics the daily digest leaves out, e.g. sports or showbiz; their articles are only listed as left out.'
    example = 'block topics sports, showbiz, culture and tv programs'
    Args = BlockedArgs

    def resolve(self, args: BlockedArgs, ctx: Context) -> tuple[Preview, dict]:
        """The blocked topics before and after."""
        current = store.blocked()
        unblock = {' '.join(r.split()).casefold() for r in args.remove}
        after = [t for t in current if t.casefold() not in unblock]
        after += [a for a in (' '.join(x.split()) for x in args.add) if a and a.casefold() not in {t.casefold() for t in after}]
        if after == current:
            raise UserError('Nothing to change: name topics to block or unblock, e.g. "block topics sports and showbiz".')
        row = Row(id='blocked', cells={'Before': ', '.join(current) or '—', 'After': ', '.join(after) or '—'})
        return Preview(summary='Change the blocked topics', columns=['Before', 'After'], rows=[row]), {'after': after}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Store the new list."""
        if 'blocked' not in selected:
            return ['Nothing changed.']
        store.set_blocked(payload['after'])
        return [f"Blocked topics: {', '.join(payload['after']) or 'none'}."]
