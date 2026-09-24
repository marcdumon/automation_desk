"""News task group: a daily digest of the user's news sites and blogs."""

from automation_desk.groups.base import TaskGroup

GROUP = TaskGroup(id='news', name='News', description='Daily news digest',
                  tasks=[])  # CLAUDE> News is run from its own page controls, not from sentences
