"""Standard task: make a digest now."""

from automation_desk.groups.base import Context, Preview, Row, StandardTask, TaskArgs, UserError
from automation_desk.groups.news import store
from automation_desk.groups.news.digest import make_digest


class MakeArgs(TaskArgs):
    """No arguments: making a digest needs none."""


class MakeDigest(StandardTask):
    """Make a digest of everything new."""

    id = 'make_digest'
    name = 'Make a digest now'
    description = 'Makes a digest of everything new since the previous one, right now.'
    example = 'make a digest now'
    Args = MakeArgs

    def resolve(self, args: MakeArgs, ctx: Context) -> tuple[Preview, dict]:
        """What the digest will cover."""
        sources = store.sources()
        if not sources:
            raise UserError('Add news sites first, e.g. "add sites lemonde.fr and standaard.be".')
        last = store.latest_made_at()
        row = Row(id='digest', cells={'Sites': str(len(sources)), 'Since': last.strftime('%d %b %H:%M') if last else 'last 24 hours',
                                      'Cost cap': f'${store.cap():.2f} a day'})
        return Preview(summary='Make a digest now', columns=['Sites', 'Since', 'Cost cap'], rows=[row]), {}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Make it, with the browser allowed because the user is at the page."""
        if 'digest' not in selected:
            return ['No digest made.']
        if make_digest('button', True) is None:
            return ['Nothing new since the previous digest.']
        return ['Digest made: open it on the News page.']
