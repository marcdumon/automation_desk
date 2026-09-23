"""Gmail task group: the model reads only your sentence, never your mail."""

from llm_automation.groups.base import TaskGroup
from llm_automation.groups.gmail.tasks.draft_mail import DraftMail
from llm_automation.groups.gmail.tasks.find_mail import FindMail
from llm_automation.groups.gmail.tasks.label_mail import LabelMail
from llm_automation.groups.gmail.tasks.trash_mail import TrashMail

GROUP = TaskGroup(id='gmail', name='Gmail', description='Gmail',
                  tasks=[FindMail(), LabelMail(), TrashMail(), DraftMail()])
