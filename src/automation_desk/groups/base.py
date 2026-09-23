"""Task groups and standard tasks: the extension points of the app.

A task group (Calendar, Tasks, Gmail, ...) is a page in the GUI holding a list of standard tasks. A standard task is one
class: the arguments the model fills from a sentence (`Args`), `resolve`, which reads Google and builds a frozen preview,
and `execute`, which applies exactly the previewed rows. Add an automation by adding an instance to its group's `tasks`.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Literal
from zoneinfo import ZoneInfo

from googleapiclient.discovery import Resource
from pydantic import BaseModel, Field

from automation_desk.jobs import RecordedService


class TaskArgs(BaseModel):
    """Base of every standard task's arguments: the model may also ask back or refuse instead."""

    status: Literal['ok', 'clarify', 'unsupported'] = Field(
        description="'ok' when the sentence fits this task; 'clarify' when something essential is missing or ambiguous; "
                    "'unsupported' when the sentence asks for something this task does not do.")
    message: str = Field(description="For 'clarify' the question to ask; for 'unsupported' why. Empty for 'ok'.")


class Row(BaseModel):
    """One line of a preview; bulk tasks show one per affected item."""

    id: str
    cells: dict[str, str]
    selectable: bool = True
    selected: bool = True
    note: str = ''
    # CLAUDE> column -> address: that cell is shown as a link, e.g. 'Open' -> the mail in Gmail
    links: dict[str, str] = {}


class PreviewOption(BaseModel):
    """A setting the user can change in the preview; the task then recomposes it without reading everything again."""

    name: str
    label: str
    value: str
    help: str = ''
    multiline: bool = False


class Evidence(BaseModel):
    """A sentence an answer is based on, where it comes from, and whether code found it there word for word."""

    quote: str
    source: str
    link: str = ''
    verified: bool = False


class Preview(BaseModel):
    """What will happen, shown to the user before anything runs."""

    summary: str
    columns: list[str]
    rows: list[Row]
    notes: list[str] = []
    options: list[PreviewOption] = []
    # CLAUDE> a result to look at, with nothing to apply (a search)
    read_only: bool = False
    # CLAUDE> an answer to the user's question, with the quotes it rests on
    answer: str = ''
    evidence: list[Evidence] = []


class UserError(ValueError):
    """A problem the user can fix by rephrasing: unknown list, bad date, empty page..."""


def match_name(name: str, items: list[dict], key: str, kind: str) -> dict:
    """The item whose `key` matches `name`: exact (case-insensitive), then unique prefix, then unique substring.

    Matching happens in code so the model never sees the user's list or calendar names.
    """
    wanted = ' '.join(name.casefold().strip(' "\'').split())
    names = {id(item): ' '.join(str(item.get(key, '')).casefold().split()) for item in items}
    for test in (lambda n: n == wanted, lambda n: n.startswith(wanted), lambda n: wanted in n):
        hits = [item for item in items if test(names[id(item)])]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise UserError(f"'{name}' matches several {kind}s: {', '.join(h[key] for h in hits)}")
    available = ', '.join(str(item.get(key)) for item in items)
    raise UserError(f"No {kind} named '{name}'. Available: {available}")


@dataclass
class Context:
    """What a task needs while resolving: the sentence, the current time, the timezone and Google clients."""

    sentence: str
    now: datetime
    tz: ZoneInfo
    service: Callable[[str, str], Resource]
    _cache: dict[tuple[str, str], Resource] = field(default_factory=dict)

    def google(self, name: str, version: str) -> Resource:
        """A Google client, built once per request."""
        key = (name, version)
        if key not in self._cache:
            self._cache[key] = RecordedService(self.service(name, version), name)
        return self._cache[key]

    @property
    def today(self) -> date:
        """Today in the user's timezone."""
        return self.now.date()


class StandardTask(ABC):
    """One automation the user can trigger from a sentence."""

    id: ClassVar[str]
    name: ClassVar[str]
    description: ClassVar[str]
    example: ClassVar[str]
    Args: ClassVar[type[TaskArgs]]
    # CLAUDE> the task-specific part of the system prompt: how to fill Args from a sentence
    guidance: ClassVar[str] = ''

    @abstractmethod
    def resolve(self, args: Any, ctx: Context) -> tuple[Preview, Any]:
        """Read Google, return the preview and the frozen payload that `execute` will apply."""

    @abstractmethod
    def execute(self, payload: Any, selected: set[str], ctx: Context) -> list[str]:
        """Apply the payload for the selected row ids; return one human-readable line per outcome."""

    def adjust(self, payload: Any, options: dict[str, str], ctx: Context) -> tuple[Preview, Any]:
        """Recompose the preview after the user changed its options. Only tasks that offer options implement this."""
        raise UserError(f'{self.name} has no settings to change.')


@dataclass(frozen=True)
class TaskGroup:
    """A page in the GUI with its own standard tasks."""

    id: str
    name: str
    description: str
    tasks: list[StandardTask]

    def task(self, task_id: str) -> StandardTask:
        """The standard task with this id."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise UserError(f'{self.name} has no standard task {task_id!r}')
