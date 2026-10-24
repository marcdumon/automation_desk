"""Gmail task group: the model reads your sentence, and mail content is only shown to you. The one exception: Find a mail
sends the best matching mails to the model, to answer a question."""

from automation_desk.groups.base import TaskGroup
from automation_desk.groups.gmail.tasks.draft_mail import DraftMail
from automation_desk.groups.gmail.tasks.find_mail import FindMail
from automation_desk.groups.gmail.tasks.label_mail import LabelMail
from automation_desk.groups.gmail.tasks.trash_mail import TrashMail

GROUP = TaskGroup(id='gmail', name='Gmail', description='Gmail',
                  tasks=[FindMail(), LabelMail(), TrashMail(), DraftMail()])
