"""A chat saved in Obsidian: chat blocks next to summary blocks in a note, the note of a chat, and its Markdown (always a
temporary vault)."""

from automation_desk.chat import note as chat_note
from automation_desk.research import export, note_text

# CLAUDE> lists of lines: Markdown headings at the start of a source line read as comments to the comment hook
NOTE = '\n'.join(['---', 'type: project note', '---', '# Roof project', 'Intro.', '', '## Costs', 'Cost text.', '',
                  '## Planning', 'Plan text.', '', '## My notes', 'Called the roofer.', ''])
BLOCK = '\n'.join(['%% {key} %%', '## {title}', '{text}', '', '%% end of {key} %%'])


def block(key: str, title: str = 'Pump chat', text: str = 'Chat text.') -> str:
    """A block between the marks of `key` ('chat 3', 'summary 3')."""
    return BLOCK.format(key=key, title=title, text=text)


def test_a_chat_block_is_put_in_replaced_and_taken_out_like_a_summary_block() -> None:
    once, message = note_text.insert(NOTE, block('chat 3'), 'chat 3', 2, 'end')
    assert message == '' and once.index('## Planning') < once.index('%% chat 3 %%') < once.index('## My notes')
    again, _ = note_text.insert(once, block('chat 3', text='New text.'), 'chat 3', 2, 'end')
    assert again.count('%% chat 3 %%') == 1 and 'New text.' in again and 'Chat text.' not in again
    assert note_text.remove(again, 'chat 3') == NOTE


def test_a_chat_and_a_summary_with_the_same_number_are_two_blocks() -> None:
    with_summary, _ = note_text.insert(NOTE, block('summary 3', 'Pump guide'), 3, 2, 'end')
    both, _ = note_text.insert(with_summary, block('chat 3'), 'chat 3', 2, 'end')
    assert both.count('%% summary 3 %%') == 1 and both.count('%% chat 3 %%') == 1
    assert note_text.remove(both, 'chat 3') == with_summary
    assert note_text.remove(both, 3).count('%% chat 3 %%') == 1, 'a number alone still means a summary'


def test_a_chat_block_never_lands_inside_a_summary_block() -> None:
    with_summary, _ = note_text.insert(NOTE, block('summary 5', 'Pump guide'), 5, 2, 'after', {'level': 2, 'text': 'Costs'})
    both, _ = note_text.insert(with_summary, block('chat 3'), 'chat 3', 2, 'after', {'level': 2, 'text': 'Pump guide'})
    assert both.index('%% end of summary 5 %%') < both.index('%% chat 3 %%')


def test_the_place_list_leaves_out_the_headings_of_the_chat_itself() -> None:
    note, _ = note_text.insert(NOTE, block('chat 3', 'Pump chat'), 'chat 3', 2, 'end')
    assert 'Pump chat' not in [h['text'] for h in note_text.headings(note, skip='chat 3')]
    assert 'Pump chat' in [h['text'] for h in note_text.headings(note, skip=3)], 'summary 3 is another block'


def test_a_research_note_saved_again_keeps_a_chat_block(tmp_path) -> None:
    row = {'id': 7, 'kind': 'product', 'title': 'Lift pit pump', 'request': 'A pump', 'state': 'done', 'budget': 200.0,
           'countries': ['BE'], 'created': '2026-10-02T10:00:00+02:00', 'cost_usd': 0.1, 'result': {}}
    saved = export.save(row, str(tmp_path), 'Research')
    path = tmp_path / 'Research' / saved['path'].rsplit('/', 1)[1]
    text, _ = note_text.insert(path.read_text(), block('chat 3'), 'chat 3', 2, 'end')
    path.write_text(text)
    export.save(row, str(tmp_path), 'Research')
    again = path.read_text()
    assert again.count('%% chat 3 %%') == 1 and again.index('## Pump chat') < again.index('## My notes')


def test_a_new_chat_note_never_overwrites_a_research_note_of_the_same_number(tmp_path) -> None:
    row = {'id': 3, 'title': 'Pump', 'request': 'Pump', 'created': '2026-10-05T10:00:00+02:00'}
    first = export.save(row, str(tmp_path), 'Research', text='---\nresearch_id: 3\n---\n# Pump\n')
    chat = export.save(row, str(tmp_path), 'Research', text='---\nchat_id: 3\n---\n# Pump\n', own='chat_id: 3')
    assert chat['path'] != first['path'] and chat['path'].endswith('2026-10-05_pump_2.md')
    again = export.save(row, str(tmp_path), 'Research', text='---\nchat_id: 3\n---\n# Pump again\n', own='chat_id: 3')
    assert again['path'] == chat['path'], 'the chat finds its own note again'


# CLAUDE> an answer with its own outline, a heading-like line in code, and a rule
ANSWER = '\n'.join(['# Supplement regimen', 'Intro.', '', '## Tier 1', '| a | b |', '|---|---|', '', '### Deeper', 'x', '',
                    '```python', '# not a heading', '```', '', '---', '', 'End.'])


def chat_row(**fields: object) -> dict:
    """A chat of two turns: the first with a chosen answer and a second opinion, the second stopped halfway."""
    def message(mid: int, turn: int, role: str, content: str, **more: object) -> dict:
        return {'id': mid, 'chat_id': 3, 'turn': turn, 'role': role, 'content': content, 'model': '', 'web': False,
                'sources': [], 'chosen': False, 'state': 'done', 'error': '', 'cost_usd': None} | more

    return {'id': 3, 'title': 'Supplements at 65', 'model': 'openai/gpt-6.1-sol', 'created': '2026-10-05T16:35:40.123+02:00',
            'target': None, 'note': None, 'cost_usd': 0.0123, 'messages': [
                message(1, 1, 'user', 'Propose a supplement regime\nfor a 65 year old male.', web=True),
                message(2, 1, 'assistant', ANSWER, model='openai/gpt-6.1-sol', chosen=True, cost_usd=0.007,
                        sources=[{'url': 'https://nih.gov/d', 'title': 'Vitamin D [fact sheet]'}]),
                message(4, 1, 'assistant', 'Other view.', model='anthropic/claude-opus-5.5', cost_usd=0.005),
                message(5, 1, 'assistant', '', model='m9', state='failed', error='OpenRouter returned 500'),
                message(6, 2, 'user', 'I take a statin.'),
                message(7, 2, 'assistant', 'Avoid red yeast rice.', model='openai/gpt-6.1-sol', chosen=True, state='stopped',
                        cost_usd=0.0003)]} | fields


def test_a_new_chat_note_has_the_questions_the_answers_and_my_notes_last() -> None:
    note = chat_note.markdown(chat_row())
    assert note.startswith('---\ntype: project note\ncreation_date: 2026-10-05 16:35:40\nauthor:\nfinished: true\nchat_id: 3\n'
                           'cost_usd: 0.0123\nmodels: "openai/gpt-6.1-sol, anthropic/claude-opus-5.5"\ntags: [chat]\n---\n'
                           'related_to:\nsources:\n- <https://nih.gov/d>\n\n---\n')
    lines = note.split('\n')
    assert [f'{"#" * level} {title}' for _, level, title in note_text.heading_lines(lines)] == [
        '# Supplements at 65', '## Chat', '### Question 1', '#### Answer — openai/gpt-6.1-sol', '##### Supplement regimen',
        '###### Tier 1', '###### Deeper', '#### Second opinion — anthropic/claude-opus-5.5', '### Question 2',
        '#### Answer — openai/gpt-6.1-sol', '## My notes'], 'the answers sit under their question; code keeps its # line'
    assert '### Question 1\n> Propose a supplement regime\n> for a 65 year old male.\n' in note
    assert 'Sources:\n- [Vitamin D \\[fact sheet\\]](<https://nih.gov/d>)\n' in note
    assert '```python\n# not a heading\n```' in note and 'm9' not in note, 'a failed answer without text is left out'
    assert 'Avoid red yeast rice.\n\n*Stopped before the end.*' in note and note.endswith('## My notes\n')


def test_a_chat_as_a_chapter_is_one_level_higher() -> None:
    text = chat_note.block(chat_row(), 1)
    assert text.startswith('%% chat 3 %%\n# Supplements at 65\n') and text.endswith('%% end of chat 3 %%')
    assert '\n## Question 1\n' in text and '\n### Answer — openai/gpt-6.1-sol\n' in text and '\n#### Supplement regimen\n' in text


def test_a_chat_saved_into_a_note_is_replaced_by_the_next_save(tmp_path) -> None:
    vault = tmp_path / 'vault'
    vault.mkdir()
    (vault / 'health.md').write_text(NOTE)
    target = {'vault': str(vault), 'note': 'health.md', 'place': 'after', 'heading': {'level': 2, 'text': 'Costs'}, 'level': 2}
    saved = chat_note.save(chat_row(target=target), '', '')
    assert saved['path'] == str(vault / 'health.md') and saved['message'] == ''
    chat_note.save(chat_row(target=target, title='Statins'), '', '')
    text = (vault / 'health.md').read_text()
    assert text.count('%% chat 3 %%') == 1 and '## Statins' in text and text.index('Cost text.') < text.index('## Statins')
    assert text.index('## Statins') < text.index('## Planning')


def test_a_chat_without_a_chosen_note_is_a_note_of_its_own(tmp_path) -> None:
    saved = chat_note.save(chat_row(), str(tmp_path), 'Research')
    path = tmp_path / 'Research' / '2026-10-05_supplements_at_65.md'
    assert saved['path'] == str(path)
    path.write_text(path.read_text() + 'Asked my doctor.\n')
    chat_note.save(chat_row(), str(tmp_path), 'Research')
    assert path.read_text().rstrip().endswith('## My notes\n\nAsked my doctor.')
