"""A subject summary saved in Obsidian: as a new note, or as a block in a note the user chose (always a temporary vault)."""

import os

import pytest

from automation_desk.research import export, note_text, summary_note

ROW = {
    'id': 12, 'kind': 'summary', 'title': 'Flat roofs', 'request': 'Rules for flat roofs\nin Flanders', 'state': 'done',
    'created': '2026-10-03T14:05:00+02:00', 'cost_usd': 0.02134, 'limits': {'searches': 20, 'pages': 30, 'cost': 1.0},
    'plan': [{'kind': 'site', 'site': 'vrt.be'}, {'kind': 'page', 'url': 'https://hln.be/b'}], 'target': {},
    'result': {
        'title': 'Flat roofs', 'paragraphs': ['First | part.', 'Second.'], 'stopped_by': '', 'pages_read': 4,
        'groups': [{'heading': 'Rules', 'items': [{'text': 'A permit is needed.', 'sources': [1, 2]}]},
                   {'heading': 'Costs', 'items': [{'text': 'About € 90 per m².', 'sources': [2]}]}],
        'sources': [{'n': 1, 'url': 'https://vrt.be/a', 'title': 'Roofs [new]', 'site': 'vrt.be'},
                    {'n': 2, 'url': 'https://hln.be/b', 'title': '', 'site': 'hln.be'}],
        'unread': ['https://x.be/file.pdf'], 'off_subject': ['https://vrt.be/sport']}}

# CLAUDE> a list of lines: Markdown headings at the start of a source line read as comments to the comment hook
NOTE = '\n'.join([
    '---', 'type: project note', 'tags: [x]', '---', 'related_to:', '', '---',
    '# Roof project', '#research #flat', 'Intro.', '',
    '## Costs', 'Cost text.', '### Details', 'Detail text.', '',
    '## Planning', 'Plan text.', '```python', '## Not a heading', '```', ''])
WITH_MY_NOTES = NOTE + '\n'.join(['## My notes', 'Called the roofer.', ''])


def put(text: str, level: int, place: str = 'end', heading: dict | None = None, row: dict = ROW) -> tuple[str, str]:
    """The note after the block of the summary in `row` is put in."""
    return note_text.insert(text, summary_note.block(row, level), row['id'], level, place, heading)


def test_a_new_note_has_one_title_with_the_summary_and_my_notes_below_it() -> None:
    """The user's outline: the title is the one '#', each part a '##' (the summary, My notes), their parts '###'."""
    note = summary_note.markdown(ROW)
    assert note.startswith('---\ntype: project note\ncreation_date: 2026-10-03 14:05:00\nauthor:\nfinished: true\nresearch_id: 12\n')
    assert 'tags: [research, summary]\n---\nrelated_to:\nsources:\n- <https://vrt.be/a>\n- <https://hln.be/b>\n\n---\n' \
           '# Flat roofs\n\n## Summary\n> Rules for flat roofs\n> in Flanders\n\nFirst | part.\n\nSecond.\n' in note
    assert '### Key points\n#### Rules\n- A permit is needed. [1](<https://vrt.be/a>) [2](<https://hln.be/b>)\n' in note
    assert '### Sources\n1. [Roofs \\[new\\]](<https://vrt.be/a>) — vrt.be\n' \
           '2. [https://hln.be/b](<https://hln.be/b>) — hln.be\n' in note
    assert '### Pages not used\n- Not read: <https://x.be/file.pdf>\n- Not about the subject: <https://vrt.be/sport>' in note
    assert [line for line in note.splitlines() if line.startswith('# ')] == ['# Flat roofs'] and note.endswith('## My notes\n')


def test_a_summary_without_points_says_so() -> None:
    row = ROW | {'result': {'title': '', 'paragraphs': [], 'groups': [], 'sources': [], 'stopped_by': '', 'unread': [],
                            'off_subject': ['https://vrt.be/sport']}}
    assert 'None of the pages read is about the subject, so there is no summary.' in summary_note.markdown(row)


def test_the_headings_of_a_note_leave_out_frontmatter_tags_and_code() -> None:
    assert note_text.headings(NOTE) == [{'level': 1, 'text': 'Roof project'}, {'level': 2, 'text': 'Costs'},
                                        {'level': 3, 'text': 'Details'}, {'level': 2, 'text': 'Planning'}]


def test_a_section_block_is_one_level_below_a_chapter_block() -> None:
    section, chapter = summary_note.block(ROW, 2), summary_note.block(ROW, 1)
    assert section.startswith('%% summary 12 %%\n## Flat roofs\n> Rules for flat roofs\n')
    assert section.endswith('%% end of summary 12 %%')
    assert '\n### Key points\n#### Rules\n- A permit is needed.' in section and '\n### Sources\n1. ' in section
    assert '\n# Flat roofs\n' in chapter and '\n## Key points\n### Rules\n' in chapter and '\n## Sources\n' in chapter


def test_after_a_heading_the_block_follows_all_text_under_it() -> None:
    note, message = put(NOTE, 2, 'after', {'level': 2, 'text': 'Costs'})
    assert note.index('Detail text.') < note.index('%% summary 12 %%') < note.index('%% end of summary 12 %%') < \
        note.index('## Planning')
    assert message == '' and '\n\n%% summary 12 %%' in note and '%% end of summary 12 %%\n\n## Planning' in note


def test_a_chapter_after_a_section_does_not_take_the_next_sections_away() -> None:
    """A chapter (#) before '## Planning' would put Planning under the summary, out of 'Roof project'."""
    note, _ = put(NOTE, 1, 'after', {'level': 2, 'text': 'Costs'})
    assert note.index('## Planning') < note.index('%% summary 12 %%') and note.rstrip().endswith('%% end of summary 12 %%')


def test_a_section_after_a_sub_section_goes_before_the_next_section() -> None:
    note, _ = put(NOTE, 2, 'after', {'level': 3, 'text': 'Details'})
    assert note.index('Detail text.') < note.index('%% summary 12 %%') < note.index('## Planning')


def test_on_top_a_section_goes_under_the_title_and_its_intro() -> None:
    note, _ = put(NOTE, 2, 'top')
    # CLAUDE> '\n## Costs\n': the block's own group '#### Costs' holds the text '## Costs' too
    assert note.index('Intro.') < note.index('%% summary 12 %%') < note.index('%% end of summary 12 %%') < note.index('\n## Costs\n')


def test_on_top_a_chapter_goes_before_the_title() -> None:
    """Any later place would make the note's parts children of the summary."""
    note, _ = put(NOTE, 1, 'top')
    assert note.index('related_to:') < note.index('%% summary 12 %%') < note.index('%% end of summary 12 %%') < \
        note.index('# Roof project')


def test_at_the_end_a_section_goes_before_my_notes_and_a_chapter_after_them() -> None:
    section, _ = put(WITH_MY_NOTES, 2)
    assert section.index('## Planning') < section.index('%% end of summary 12 %%') < section.index('## My notes')
    assert section.rstrip().endswith('Called the roofer.')
    chapter, _ = put(WITH_MY_NOTES, 1)
    assert chapter.index('Called the roofer.') < chapter.index('%% summary 12 %%')


def test_at_the_end_and_when_the_heading_is_gone() -> None:
    end, message = put(NOTE, 2)
    assert end.startswith(NOTE.rstrip('\n')) and end.endswith('%% end of summary 12 %%\n') and message == ''
    gone, message = put(NOTE, 2, 'after', {'level': 2, 'text': 'Budget'})
    assert gone == end and message == 'The heading "Budget" is no longer in the note, so the summary went to the end.'


def test_saving_again_replaces_the_block_and_another_summary_keeps_its_own() -> None:
    once, _ = put(NOTE, 2, 'after', {'level': 2, 'text': 'Costs'})
    changed = ROW | {'result': ROW['result'] | {'paragraphs': ['Changed.']}}
    twice, _ = put(once, 2, row=changed)
    assert twice.count('%% summary 12 %%') == 1 and 'Changed.' in twice and 'First | part.' not in twice
    assert twice.index('%% summary 12 %%') < twice.index('## Planning'), 'the block stays where it was'
    other, _ = put(twice, 2, row=ROW | {'id': 1})
    assert other.count('%% summary 12 %%') == 1 and other.count('%% summary 1 %%') == 1, 'summary 1 does not find 12'


def test_a_new_block_never_lands_inside_another_summary() -> None:
    """Summary 12 sits after 'Details'; summary 7 after 'Costs' would go right before 12's title, inside 12's marks, and
    saving 12 again would then delete summary 7."""
    with_12, _ = put(NOTE, 2, 'after', {'level': 3, 'text': 'Details'})
    with_7, _ = put(with_12, 2, 'after', {'level': 2, 'text': 'Costs'}, row=ROW | {'id': 7})
    assert with_7.index('%% end of summary 7 %%') < with_7.index('%% summary 12 %%')
    again, _ = put(with_7, 2)
    assert again.count('%% summary 7 %%') == 1 and again.count('%% end of summary 7 %%') == 1
    on_top, _ = put(put(NOTE, 2, 'top')[0], 2, 'top', row=ROW | {'id': 7})
    assert on_top.index('%% end of summary 7 %%') < on_top.index('%% summary 12 %%'), 'the newest goes on top, outside 12'


def test_a_heading_chosen_inside_another_summary_puts_the_block_after_it() -> None:
    chapter, _ = put(NOTE, 1)
    note, _ = put(chapter, 2, 'after', {'level': 2, 'text': 'Key points'}, row=ROW | {'id': 7})
    assert note.index('%% end of summary 12 %%') < note.index('%% summary 7 %%')


def test_a_lone_start_mark_never_takes_the_users_text_away() -> None:
    """The user deleted the end mark of summary 12 and wrote below the block: later saves keep that text."""
    once, _ = put(NOTE, 2)
    broken = once.replace('%% end of summary 12 %%', 'My own words.')
    twice, _ = put(broken, 2)
    thrice, _ = put(twice, 2)
    assert 'My own words.' in thrice and thrice.count('%% end of summary 12 %%') == 1 and thrice == twice


@pytest.fixture
def vault(tmp_path):
    """A vault with a project note, an older note, Obsidian's own folders and a picture."""
    root = tmp_path / 'work_vault'
    (root / 'Projects').mkdir(parents=True)
    (root / 'Projects' / 'roof.md').write_text(NOTE)
    (root / 'old note.md').write_text('# Old\n')
    for hidden in ('.obsidian', '.trash'):
        (root / hidden).mkdir()
        (root / hidden / 'x.md').write_text('# x\n')
    (root / 'picture.png').write_bytes(b'png')
    os.utime(root / 'old note.md', (1_000_000, 1_000_000))
    return root


def test_the_notes_of_the_vault_newest_first_and_found_by_words(vault) -> None:
    assert summary_note.notes(str(vault)) == [{'path': 'Projects/roof.md', 'name': 'roof'},
                                              {'path': 'old note.md', 'name': 'old note'}]
    assert summary_note.notes(str(vault), 'projects ROOF') == [{'path': 'Projects/roof.md', 'name': 'roof'}]


@pytest.mark.parametrize('path, says', [('../outside.md', 'inside the vault'), ('/etc/hosts', 'inside the vault'),
                                         ('picture.png', 'Markdown note'), ('gone.md', 'does not exist')])
def test_a_note_outside_the_vault_or_not_a_note_is_refused(vault, path: str, says: str) -> None:
    with pytest.raises(export.ExportError, match=says):
        summary_note.note_path(str(vault), path)


def test_a_summary_goes_into_the_chosen_note_of_the_vault_it_started_with(vault, tmp_path) -> None:
    row = ROW | {'target': {'vault': str(vault), 'note': 'Projects/roof.md', 'place': 'after',
                            'heading': {'level': 2, 'text': 'Costs'}, 'level': 2}}
    other = tmp_path / 'other_vault'
    other.mkdir()
    saved = summary_note.save(row, str(other), 'Research')
    assert saved == {'path': str(vault / 'Projects' / 'roof.md'), 'message': '',
                     'obsidian_url': 'obsidian://open?vault=work_vault&file=Projects%2Froof'}
    summary_note.save(row, str(other), 'Research')
    text = (vault / 'Projects' / 'roof.md').read_text()
    assert text.count('%% summary 12 %%') == 1 and text.startswith(NOTE.split('## Costs')[0]), 'the rest of the note is the same'
    assert text.index('Detail text.') < text.index('%% summary 12 %%') < text.index('## Planning')


@pytest.mark.parametrize('target, before', [({'place': 'top'}, '\n## Costs\n'),
                                            ({'heading': {'level': 2, 'text': 'Costs'}}, '## Planning')])
def test_the_place_chosen_is_kept_and_an_older_summary_still_goes_after_its_heading(vault, target: dict, before: str) -> None:
    """Summaries started before 'top' existed have no place: with a heading they go after it."""
    row = ROW | {'target': {'vault': str(vault), 'note': 'Projects/roof.md', 'heading': None, 'level': 2} | target}
    summary_note.save(row, '', '')
    text = (vault / 'Projects' / 'roof.md').read_text()
    assert text.index('%% end of summary 12 %%') < text.index(before)


def test_a_summary_without_a_chosen_note_is_a_new_note_that_keeps_my_notes(vault) -> None:
    saved = summary_note.save(ROW, str(vault), 'Research')
    path = vault / 'Research' / '2026-10-03_flat_roofs.md'
    assert saved['path'] == str(path) and saved['message'] == ''
    path.write_text(path.read_text() + 'Called the roofer.\n')
    summary_note.save(ROW, str(vault), 'Research')
    assert path.read_text().rstrip().endswith('## My notes\n\nCalled the roofer.') and 'research_id: 12' in path.read_text()


def test_after_the_title_a_section_still_goes_before_my_notes() -> None:
    """Summary 7 went after the title of a research note: the title's part runs to the end of the note, so the summary
    ended up under My notes."""
    note, _ = put(WITH_MY_NOTES, 2, 'after', {'level': 1, 'text': 'Roof project'})
    assert note.index('## Planning') < note.index('%% summary 12 %%') < note.index('## My notes')


def test_a_heading_chosen_under_my_notes_keeps_the_block_under_it() -> None:
    text = WITH_MY_NOTES + '\n'.join(['### Shopping', 'Buy hose.', ''])
    note, _ = put(text, 2, 'after', {'level': 3, 'text': 'Shopping'})
    assert note.index('Buy hose.') < note.index('%% summary 12 %%')


def test_a_block_taken_out_leaves_the_note_as_it_was() -> None:
    placed, _ = put(NOTE, 2, 'after', {'level': 2, 'text': 'Costs'})
    assert note_text.remove(placed, 12) == NOTE
    broken = placed.replace('%% end of summary 12 %%', '')
    assert note_text.remove(broken, 12) == broken, 'a block without its end mark is not complete: nothing is taken out'


def test_the_sources_of_a_point_are_in_number_order() -> None:
    """Summary 7 showed "[3] [2]" after a point."""
    row = ROW | {'result': ROW['result'] | {'groups': [{'heading': 'Rules', 'items': [{'text': 'A permit.', 'sources': [2, 1]}]}]}}
    assert '- A permit. [1](<https://vrt.be/a>) [2](<https://hln.be/b>)' in summary_note.markdown(row)


def test_a_save_clears_its_own_lone_marks() -> None:
    """Obsidian hides the marks: deleting the summary's text there left '%% summary 7 %%' alone at the end of the note."""
    once, _ = put(WITH_MY_NOTES, 2)
    cut = once[:once.index('%% summary 12 %%') + len('%% summary 12 %%')] + '\n'
    again, _ = put(cut, 2)
    assert again.count('%% summary 12 %%') == 1 and again.count('%% end of summary 12 %%') == 1
    other, _ = put(cut.replace('summary 12', 'summary 9'), 2)
    assert other.count('%% summary 9 %%') == 1, "another summary's lone mark is left alone"


def test_the_place_list_leaves_out_the_headings_of_the_summary_itself() -> None:
    """A save puts the summary in afresh: "after Key points" of its own block was offered and made no sense."""
    note, _ = put(NOTE, 2, 'after', {'level': 2, 'text': 'Costs'})
    texts = [h['text'] for h in note_text.headings(note, skip=12)]
    assert texts == ['Roof project', 'Costs', 'Details', 'Planning']
    assert 'Key points' in [h['text'] for h in note_text.headings(note)], 'without skip every heading shows'
