"""Standard task: answer a question from your mail, across languages.

1. The model splits the question into concepts with search words in several languages; it sees only the question.
2. Code runs Gmail searches from them. The first (most important) concept is always required; when the strict search
   finds little, only the other concepts are dropped. Mails are ranked by how many concepts they match.
3. The best matching mails (at most ANSWER_MAILS) go to the model with the question. It answers in the question's
   language and quotes the sentences it used; code checks every quote is literally in that mail.
Nothing in the mailbox changes.
"""

import re
from itertools import combinations

from pydantic import BaseModel, Field

from automation_desk.dates import gmail_age
from automation_desk.groups.base import Context, Evidence, Preview, StandardTask, TaskArgs, UserError
from automation_desk.groups.gmail.client import body_text, gmail_link, message_ids, metadata, received, sender
from automation_desk.groups.gmail.select import any_of, resolve_label
from automation_desk.groups.gmail.tasks._rows import MAIL_COLUMNS, mail_row
from automation_desk.llm import ask

ENOUGH = 5
MAX_RESULTS = 30
ANSWER_MAILS = 8
MAIL_CHARS = 6_000


class FindMailArgs(TaskArgs):
    """The question as search concepts, plus the limits the user gave."""

    concepts: list[list[str]] = Field(description="The things the wanted mail must be about, MOST IMPORTANT FIRST (the "
                                                  "first one is always required). For each concept: search words in the "
                                                  "user's language AND in English, Dutch and French, including common "
                                                  "synonyms, e.g. for 'wanneer resultaat bloedtest beschikbaar': "
                                                  "[['bloedtest', 'blood test', 'bloodwork', 'prise de sang', "
                                                  "'bloedonderzoek', 'bloedafname'], ['resultaat', 'results', "
                                                  "'résultats', 'uitslag']]. Leave out question words such as 'when'.")
    label_name: str = Field(description="A label the user named as the place to look, e.g. 'Health'. Empty when not said.")
    from_contains: list[str] = Field(description='Senders the user named. Empty list when not said.')
    newer_than: str = Field(description="Only mails younger than this, as the user said it: 'a month', '2 weeks'. Empty "
                                        'when not said. Never a date.')


class Quote(BaseModel):
    """A sentence copied from one of the mails."""

    mail: int = Field(description='The number of the mail the sentence is in.')
    quote: str = Field(description='The sentence, copied character for character from that mail.')


class MailAnswer(BaseModel):
    """The model's answer, with the sentences it rests on."""

    answered: bool = Field(description='False when the mails do not answer the question.')
    answer: str = Field(description='The answer in the language of the question, short and direct. You may reason from '
                                    'what the mails say (add up durations, compare), but never write a calendar date '
                                    'that is not written in a mail. When not answered: say what the mails do say.')
    quotes: list[Quote] = Field(description='The exact sentences the answer is based on.')


ANSWER_SYSTEM = """You answer the user's question using only the numbered mails below.
Answer in the language of the question. Be short and concrete; show a simple calculation when you combine numbers.
Quote the exact sentences you used, copied character for character, with the number of their mail.
Never invent facts. Never write a calendar date that is not literally in a mail."""


def _flat(text: str) -> str:
    """Text reduced for comparing quotes: no reply markers, uniform quotes, collapsed spaces, lowercase."""
    text = re.sub(r'(?m)^\s*>+', ' ', text).replace('\u2019', "'").replace('\u2018', "'").replace('\u201c', '"')
    return ' '.join(text.replace('\u201d', '"').split()).casefold()


class FindMail(StandardTask):
    """Answer a question from the mails that best match it."""

    id = 'find_mail'
    name = 'Find a mail'
    description = ('Answers a QUESTION from your mailbox, in any language (the question can be Dutch while the mail is '
                   "English): 'wanneer…', 'when…', 'where is…', 'which mail…', or a request to find, search or look "
                   'up mail. A label named in a question is where to look. The best matching mails are read to answer '
                   'it, with quotes; nothing in the mailbox changes.')
    example = 'wanneer is het resultaat van de bloedtest beschikbaar'
    Args = FindMailArgs

    def resolve(self, args: FindMailArgs, ctx: Context) -> tuple[Preview, dict]:
        """Search, rank, then have the model answer from the best mails; quotes are checked in code."""
        concepts = [c for c in ([t for t in terms if t.strip()] for terms in args.concepts) if c]
        if not concepts:
            raise UserError('Say what the mail is about.')
        label = resolve_label(ctx, args.label_name) if args.label_name.strip() else None
        limits = [any_of('from', args.from_contains)]
        if args.newer_than.strip():
            limits.append(f'newer_than:{gmail_age(args.newer_than)}')
        svc = ctx.google('gmail', 'v1')

        scores: dict[str, int] = {}
        searches = []
        for size in range(len(concepts), 0, -1):
            # CLAUDE> the first concept is what the question is about; loosening may drop the others, never that one
            for others in combinations(range(1, len(concepts)), size - 1):
                chosen = (0, *others)
                query = ' '.join(filter(None, [*limits, *(any_of('', concepts[i]) for i in chosen)]))
                found, _ = message_ids(svc, query, [label['id']] if label else [], MAX_RESULTS)
                searches.append(f'{query} → {len(found)}')
                for m in found:
                    scores[m['id']] = max(scores.get(m['id'], 0), size)
            if len(scores) >= ENOUGH:
                break
        if not scores:
            raise UserError('No mail found, also not with a looser search. Searches tried: ' + '; '.join(searches))

        ranked = sorted(scores, key=lambda i: -scores[i])[:MAX_RESULTS]
        mails = sorted((metadata(svc, i) for i in ranked), key=lambda m: (-scores[m['id']], -m['received_ms']))
        read = mails[:ANSWER_MAILS]
        texts = {m['id']: body_text(svc, m['id']) for m in read}
        numbered = '\n\n'.join(
            f"Mail {n}\nFrom: {m['from']}\nSubject: {m['subject']}\nReceived: {received(m, ctx.tz)}\n\n"
            f"{texts[m['id']][:MAIL_CHARS]}" for n, m in enumerate(read, 1))
        reply = ask(ANSWER_SYSTEM, f'Question: {ctx.sentence}\n\n{numbered}', MailAnswer, purpose='answer from mails')

        evidence = []
        for q in reply.quotes:
            if 1 <= q.mail <= len(read):
                mail = read[q.mail - 1]
                evidence.append(Evidence(quote=q.quote.strip(), source=f"{sender(mail)}: {mail['subject']}",
                                         link=gmail_link(mail['thread_id']),
                                         verified=_flat(q.quote) in _flat(texts[mail['id']])))
        used = {e.link for e in evidence}
        rows = []
        for m in mails:
            note = f'matches {scores[m["id"]]} of {len(concepts)} things you asked about'
            if gmail_link(m['thread_id']) in used:
                note += '; the answer quotes it'
            elif m['id'] in texts:
                note += '; read to answer'
            rows.append(mail_row(m, ctx.tz, selectable=False, selected=False, note=note))

        notes = [f'The model read the best {len(read)} mail(s) to answer; the others were only listed.']
        if evidence and not all(e.verified for e in evidence):
            notes.insert(0, 'Not every quote was found word for word in its mail: check the answer against the mails.')
        if not evidence:
            notes.insert(0, 'The answer comes without quotes: check it against the mails.')
        notes.append(('In label ' + label['name'] + '. ' if label else '') + 'Gmail searches run: ' + '; '.join(searches))
        answer = reply.answer.strip() if reply.answered else f'Not answered by these mails. {reply.answer.strip()}'
        return Preview(summary=f'{len(rows)} mail(s) found, best matches first', columns=MAIL_COLUMNS, rows=rows,
                       notes=notes, read_only=True, answer=answer, evidence=evidence), {}

    def execute(self, payload: dict, selected: set[str], ctx: Context) -> list[str]:
        """Nothing to apply: a search changes nothing."""
        return ['A search changes nothing.']
