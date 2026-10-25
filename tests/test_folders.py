"""The app folder (the settings, .env and secrets, next to the Python environment) and the data folder (the database, the
uploads, the captures and the logs; data_dir of automation_desk.toml)."""

import re
import sys
from pathlib import Path

import pytest

from automation_desk import config as config_module


def _settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    """Point the app to a settings file with this text."""
    settings = tmp_path / 'automation_desk.toml'
    settings.write_text(text)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', settings)


def test_the_app_folder_holds_the_python_environment() -> None:
    assert Path(sys.prefix).parent == config_module.APP_DIR


def test_the_data_folder_is_data_dir_of_the_settings(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch, f"data_dir = '{tmp_path / 'backed_up'}'\n\n[server]\nport = 8765\n")
    assert config_module.data_dir() == tmp_path / 'backed_up'


def test_a_data_dir_that_starts_with_a_tilde_is_in_the_home_folder(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch, "data_dir = '~/desk'\n")
    assert config_module.data_dir() == Path.home() / 'desk'


def test_without_data_dir_the_data_folder_is_in_the_app_folder(tmp_path, monkeypatch) -> None:
    _settings(tmp_path, monkeypatch, "data_dir = ''\n\n[server]\nport = 8765\n")
    assert config_module.data_dir() == config_module.APP_DIR / 'data'


def test_without_a_settings_file_the_data_folder_is_in_the_app_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'automation_desk.toml')
    assert config_module.data_dir() == config_module.APP_DIR / 'data'


def test_a_missing_settings_file_says_how_to_make_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'automation_desk.toml')
    config_module.config.cache_clear()
    with pytest.raises(SystemExit, match=re.escape('copy automation_desk.example.toml to automation_desk.toml')):
        config_module.config()


def test_the_example_settings_have_an_empty_data_dir() -> None:
    assert "\ndata_dir = ''\n" in (Path(__file__).parents[1] / 'automation_desk.example.toml').read_text()
