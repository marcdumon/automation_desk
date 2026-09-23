"""Settings from `automation_desk.toml` and secrets from `.env`."""

import os
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = ROOT / 'automation_desk.toml'


@dataclass(frozen=True)
class NewsSettings:
    """Settings of the daily news digest."""

    digest_time: str
    cap_usd: float
    max_words: int
    batch_size: int


@dataclass(frozen=True)
class Config:
    """Everything configurable, in one place."""

    llm_base_url: str
    llm_model: str
    fallback_timezone: str
    max_pages: int
    port: int
    price_in_per_m: float
    price_out_per_m: float
    news: NewsSettings

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
        fallback_timezone=raw['time']['fallback_timezone'],
        max_pages=int(raw['web']['max_pages']),
        port=int(raw['server']['port']),
        price_in_per_m=float(raw['prices']['input_per_m']),
        price_out_per_m=float(raw['prices']['output_per_m']),
        news=NewsSettings(**raw['news']),
    )
