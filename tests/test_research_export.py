"""Saving a research as a Markdown note in an Obsidian vault (always a temporary vault here, never the user's)."""

import json

import pytest

from automation_desk.research import export

ROW = {
    'id': 7, 'title': 'Lift pit pump', 'limits': {'searches': 20, 'pages': 30, 'cost': 1.0},
    'request': 'A pump | for the lift pit: when it rains\n   │   more text', 'kind': 'product', 'budget': 500.0,
    'countries': ['BE', 'NL'], 'cost_usd': 0.1612, 'created': '2026-10-02T21:14:00+02:00', 'state': 'done',
    'questions': [{'id': 'q1', 'text': 'Where does the water go?', 'choices': []}], 'answers': {'q1': 'Drain, 3 m higher'},
    'followups': [], 'followup_answers': {},
    'requirements': [{'id': 'r1', 'text': 'Pumps dirty water', 'weight': 'must'}, {'id': 'r2', 'text': 'Quiet', 'weight': 'nice'}],
    'result': {
        'summary': 'The Gardena fits.', 'reasons': 'It pumps dirt.', 'risks': ['Ask the syndic.'], 'stopped_by': '',
        'unread': ['https://blocked.be/p'],
        'best': {'title': 'Gardena 20000', 'url': 'https://shop.be/g', 'shop': 'shop.be', 'total': 142.79, 'score': 92,
                 'checks': {'r1': 'yes', 'r2': 'unknown'}, 'pros': ['Strong'], 'cons': ['Loud'], 'tag': 'recommended',
                 'notes': {'r1': 'Passes 35 mm'}, 'points': {'r1': 3.0, 'r2': 0.25}, 'points_total': 3.25, 'points_max': 4},
        'ranking': [
            {'title': 'Gardena 20000', 'url': 'https://shop.be/g', 'shop': 'shop.be', 'total': 142.79, 'score': 92,
             'checks': {'r1': 'yes', 'r2': 'unknown'}, 'pros': ['Strong'], 'cons': ['Loud'], 'tag': 'recommended', 'why_not': '',
             'points': {'r1': 3.0, 'r2': 0.25}, 'points_total': 3.25, 'points_max': 4},
            {'title': 'Pomp | cheap', 'url': 'https://b.nl/p', 'shop': 'b.nl', 'total': 60.0, 'score': 40,
             'checks': {'r1': 'no', 'r2': 'yes'}, 'pros': [], 'cons': [], 'tag': '', 'why_not': 'Fails a must: Pumps dirty water',
             'points': {'r1': 0.0, 'r2': 1.0}, 'points_total': 1.0, 'points_max': 4}],
        'comparison': {'products': [], 'over_budget': [],
                       'no_price': [{'title': 'Mystery pump', 'offers': [{'url': 'https://m.de/p', 'shop': 'm.de'}]}]}}}


def test_the_note_holds_the_whole_research() -> None:
    note = export.markdown(ROW)
    # CLAUDE> the user's project note template: type, creation_date, finished; related_to and sources; then '# Title'
    assert note.startswith('---\ntype: project note\ncreation_date: 2026-10-02 21:14:00\nauthor:\nfinished: true\nresearch_id: 7\n')
    assert 'tags: [research]' in note and 'budget: 500' in note
    assert '---\nrelated_to:\nsources:\n- <https://shop.be/g>\n\n---\n# Lift pit pump\n' in note
    assert '- Where does the water go?: Drain, 3 m higher' in note
    assert '| R1 | Pumps dirty water | must | 3 |' in note and '| | Total | | 4 |' in note
    assert '| R1 | Pumps dirty water | Yes | Passes 35 mm | 3 of 3 |' in note and '3.25 of 4 = score 92/100' in note
    assert '**[Gardena 20000](<https://shop.be/g>)**' in note and '€ 142.79' in note and 'The Gardena fits.' in note
    assert 'The Gardena fits.\n\nIt pumps dirt.' in note and '> more text' in note
    assert '| [Pomp \\| cheap](<https://b.nl/p>) | € 60.00 | 0 | 1 | 1 of 4 | 40 |' in note
    assert 'Fails a must: Pumps dirty water' in note
    assert '[Mystery pump](<https://m.de/p>)' in note and '- Ask the syndic.' in note and 'https://blocked.be/p' in note


def test_the_note_is_saved_in_the_subfolder_and_again_in_the_same_file(tmp_path) -> None:
    vault = tmp_path / 'vault'
    vault.mkdir()
    saved = export.save(ROW, str(vault), 'Research/Pumps')
    path = vault / 'Research' / 'Pumps' / '2026-10-02_lift_pit_pump.md'
    assert saved['path'] == str(path) and 'research_id: 7' in path.read_text()
    assert saved['obsidian_url'] == 'obsidian://open?vault=vault&file=Research%2FPumps%2F2026-10-02_lift_pit_pump'
    assert export.save(ROW, str(vault), 'Research/Pumps')['path'] == str(path), 'the same research writes the same file'
    other = export.save(ROW | {'id': 8}, str(vault), 'Research/Pumps')
    assert other['path'].endswith('2026-10-02_lift_pit_pump_2.md'), 'another research with the same title gets its own file'


@pytest.mark.parametrize('subdir', ['../outside', '/etc', 'a/../../b'])
def test_a_subfolder_outside_the_vault_is_refused(tmp_path, subdir: str) -> None:
    with pytest.raises(export.ExportError, match='inside the vault'):
        export.save(ROW, str(tmp_path), subdir)


def test_a_missing_vault_is_said_plainly(tmp_path) -> None:
    with pytest.raises(export.ExportError, match='Settings'):
        export.save(ROW, '', 'Research')
    with pytest.raises(export.ExportError, match='does not exist'):
        export.save(ROW, str(tmp_path / 'gone'), 'Research')


def test_the_vaults_obsidian_knows_are_offered(tmp_path) -> None:
    vault = tmp_path / 'clippings_vault'
    vault.mkdir()
    config = tmp_path / 'obsidian.json'
    config.write_text(json.dumps({'vaults': {'c869': {'path': str(vault)}, 'gone': {'path': str(tmp_path / 'gone')}}}))
    assert export.vaults(config) == [{'name': 'clippings_vault', 'path': str(vault)}]
    assert export.vaults(tmp_path / 'missing.json') == []


def test_my_notes_in_obsidian_survive_a_new_save(tmp_path) -> None:
    saved = export.save(ROW, str(tmp_path), 'Research')
    path = tmp_path / 'Research' / saved['path'].rsplit('/', 1)[1]
    path.write_text(path.read_text() + 'Bought it at the local shop.\n')
    export.save(ROW, str(tmp_path), 'Research')
    text = path.read_text()
    assert text.count('## My notes') == 1 and text.rstrip().endswith('Bought it at the local shop.')


def test_links_with_brackets_stay_links() -> None:
    assert export._link('Pump [550W]', 'https://s.be/p (2)') == '[Pump \\[550W\\]](<https://s.be/p (2)>)'


def test_a_tagged_row_keeps_its_reason() -> None:
    tagged = ROW['result']['ranking'][1] | {'tag': 'cheapest good choice', 'why_not': 'Weaker on: Quiet'}
    row = ROW | {'result': ROW['result'] | {'ranking': [tagged]}}
    assert 'cheapest good choice — Weaker on: Quiet' in export.markdown(row)


def test_an_obsidian_file_of_another_shape_offers_no_vaults(tmp_path) -> None:
    config = tmp_path / 'obsidian.json'
    config.write_text('[1, 2]')
    assert export.vaults(config) == []


def test_the_note_names_the_limit_that_stopped_the_research() -> None:
    row = ROW | {'result': ROW['result'] | {'stopped_by': 'the page limit'}}
    assert 'after reading 30 shop pages' in export.markdown(row)


def test_vaults_next_to_a_known_vault_are_offered_too(tmp_path) -> None:
    """Obsidian's own list held only clippings_vault; work_vault and investment_vault sit next to it."""
    for name in ('clippings_vault', 'work_vault', 'investment_vault'):
        (tmp_path / name / '.obsidian').mkdir(parents=True)
    (tmp_path / 'not_a_vault').mkdir()
    config = tmp_path / 'obsidian.json'
    config.write_text(json.dumps({'vaults': {'c869': {'path': str(tmp_path / 'clippings_vault')}}}))
    assert [v['name'] for v in export.vaults(config)] == ['clippings_vault', 'investment_vault', 'work_vault']


def test_without_a_title_the_request_names_the_note(tmp_path) -> None:
    saved = export.save(ROW | {'title': ''}, str(tmp_path), 'Research')
    assert saved['path'].endswith('2026-10-02_a_pump_for_the_lift_pit_when_it_rains.md')
