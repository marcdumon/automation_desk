"""News task group: a daily digest of the user's news sites and blogs."""

from automation_desk.groups.base import TaskGroup
from automation_desk.groups.news.tasks.make import MakeDigest
from automation_desk.groups.news.tasks.sites import ManageSites
from automation_desk.groups.news.tasks.subjects import ManageSubjects

GROUP = TaskGroup(id='news', name='News', description='Daily news digest',
                  tasks=[ManageSites(), ManageSubjects(), MakeDigest()])
