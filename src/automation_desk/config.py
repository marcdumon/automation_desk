"""Settings from `automation_desk.toml`, the user's own values from `person.toml` (not in git) and secrets from `.env`."""

import os
import tomllib
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / 'automation_desk.toml'
# the user's own values: git ignores this file, person.example.toml shows its part
PERSON_FILE = ROOT / 'person.toml'


@dataclass(frozen=True)
class NewsSettings:
    """Settings of the news digest."""

    cap_usd: float
    max_words: int
    batch_size: int


@dataclass(frozen=True)
class PolarSettings:
    """The Polar H10 settings: the folders of the phone app's files and the logger's sessions, the logger's project folder
    (where `uv run polar-h10 record` runs), the address of its API, and the folder of the phone app's files on the phone
    (ftp://user@host:port/folder/, through WiFi FTP Server); '' when not set."""

    phone_dir: str = ''
    logger_dir: str = ''
    logger_project: str = ''
    logger_api: str = 'http://127.0.0.1:8770'
    phone_ftp: str = ''


@dataclass(frozen=True)
class SleepSettings:
    """The sleep settings: the Sleep as Android backup that Dropbox syncs to the PC (a zip); '' when not set."""

    backup_file: str = ''


@dataclass(frozen=True)
class PersonSettings:
    """The user's own values, from the [person] part of person.toml: the birth date (YYYY-MM-DD, or a TOML date), the sex
    ('male' or 'female'), the HR from which the energy expenditure follows the HR, the personal correction factor of the
    energy expenditure, and the weight (kg) and height (cm) for when the Withings scale has none; '' or None when not
    set."""

    birth_date: str | date = ''
    sex: str = ''
    energy_from_hr: int = 90
    energy_factor: float = 1.0
    weight_kg: float | None = None
    height_cm: float | None = None


@dataclass(frozen=True)
class Config:
    """Everything configurable, in one place."""

    llm_base_url: str
    llm_model: str
    llm_requests_per_minute: int
    fallback_timezone: str
    max_pages: int
    port: int
    price_in_per_m: float
    price_out_per_m: float
    news: NewsSettings
    polar: PolarSettings = PolarSettings()
    sleep: SleepSettings = SleepSettings()
    person: PersonSettings = PersonSettings()

    @property
    def openrouter_key(self) -> str:
        """The OpenRouter key, read from .env at every call so a new key works without restarting the app."""
        return dotenv_values(ROOT / '.env').get('OPENROUTER_API_KEY') or os.environ.get('OPENROUTER_API_KEY', '')


@cache
def config() -> Config:
    """Load the config file once."""
    load_dotenv(ROOT / '.env')
    raw = tomllib.loads(CONFIG_FILE.read_text())
    return Config(
        llm_base_url=raw['llm']['base_url'],
        llm_model=raw['llm']['model'],
        llm_requests_per_minute=int(raw['llm']['requests_per_minute']),
        fallback_timezone=raw['time']['fallback_timezone'],
        max_pages=int(raw['web']['max_pages']),
        port=int(raw['server']['port']),
        price_in_per_m=float(raw['prices']['input_per_m']),
        price_out_per_m=float(raw['prices']['output_per_m']),
        news=NewsSettings(**raw['news']),
        polar=PolarSettings(**raw.get('polar', {})),
        sleep=SleepSettings(**raw.get('sleep', {})),
        person=PersonSettings(**(tomllib.loads(PERSON_FILE.read_text()).get('person', {}) if PERSON_FILE.is_file() else {})),
    )
