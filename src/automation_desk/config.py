"""Settings from `automation_desk.toml` (not in git) and secrets from `.env`.

These files are in the app folder: the folder of the Python environment (the repo in development, the install folder of an
installed app). The database, the uploads, the captures and the logs are in the data folder: `data_dir` of
`automation_desk.toml`."""

import os
import sys
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

APP_DIR = Path(sys.prefix).parent
CONFIG_FILE = APP_DIR / 'automation_desk.toml'


def data_dir() -> Path:
    """The data folder: data_dir of automation_desk.toml, or the folder data in the app folder when it is not set."""
    value = tomllib.loads(CONFIG_FILE.read_text()).get('data_dir', '') if CONFIG_FILE.is_file() else ''
    return Path(value).expanduser() if value else APP_DIR / 'data'


DATA_DIR = data_dir()


@dataclass(frozen=True)
class NewsSettings:
    """Settings of the news digest."""

    cap_usd: float
    max_words: int
    batch_size: int


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

    @property
    def openrouter_key(self) -> str:
        """The OpenRouter key, read from .env at every call so a new key works without restarting the app."""
        return dotenv_values(APP_DIR / '.env').get('OPENROUTER_API_KEY') or os.environ.get('OPENROUTER_API_KEY', '')


@cache
def config() -> Config:
    """Load the config file once."""
    load_dotenv(APP_DIR / '.env')
    if not CONFIG_FILE.is_file():
        raise SystemExit(f'No {CONFIG_FILE.name} in {APP_DIR}: copy automation_desk.example.toml to {CONFIG_FILE.name}, '
                         'then set your own values in it.')
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
    )
