"""Sentence -> standard task arguments. The model sees only the sentence and a schema, never Google data."""

from typing import Literal

import httpx
from pydantic import BaseModel, Field, create_model

from llm_automation.groups.base import StandardTask, TaskArgs, TaskGroup
from llm_automation.llm import ask

RULES = """Rules:
- Never write a calendar date, a numeric date (like 2026-10-03 or 3/10) or a year. For dates and periods copy the user's
  own relative words: 'tomorrow', 'today+2', 'friday', 'next week', 'in 3 days'. The app turns them into dates.
- Copy names of lists and calendars, titles and free text exactly as the user wrote them, in the user's language
  (English, Dutch or French); do not translate.
- status 'unsupported' when the sentence asks for something this task does not do; 'clarify' with a question when
  something essential is missing; otherwise 'ok' with an empty message."""


def fill_args(group: TaskGroup, task: StandardTask, sentence: str, http: httpx.Client | None = None) -> TaskArgs:
    """Have the model fill one standard task's arguments from the sentence."""
    system = (f"You translate one sentence into the arguments of the standard task '{task.name}' in the "
              f"{group.name} section of a personal automation app.\nWhat the task does: {task.description}\n"
              f'{task.guidance}\n{RULES}')
    return ask(system, sentence, task.Args, http=http, purpose=f'fill arguments: {task.name}')


class Route(BaseModel):
    """Which standard task a sentence is for."""

    task: str
    message: str


def route(group: TaskGroup, sentence: str, http: httpx.Client | None = None) -> Route:
    """Pick the group's standard task that fits the sentence, or 'none' with the reason."""
    ids = (*(task.id for task in group.tasks), 'none')
    schema = create_model(
        'Route',
        __base__=Route,
        task=(Literal[ids], Field(description="The id of the matching standard task, or 'none'.")),
        message=(str, Field(description="When 'none': a short explanation for the user. Else empty.")),
    )
    catalogue = '\n'.join(f'- {task.id}: {task.name}. {task.description}' for task in group.tasks)
    system = (f'You pick which standard task of the {group.name} section a sentence asks for.\nStandard tasks:\n{catalogue}\n'
              "Answer 'none' when the sentence fits none of them, including requests meant for another app section.")
    return ask(system, sentence, schema, http=http, purpose='choose standard task')
