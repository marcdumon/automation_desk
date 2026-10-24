"""The app's Google login (Calendar, Gmail) through the google_api library.

The OAuth client secret and the token both live in the project's secrets/ folder (gitignored).
Run `uv run python -m automation_desk.google_auth` once to log in.
"""

from google.oauth2.credentials import Credentials
from google_api import auth
from googleapiclient.discovery import Resource

from automation_desk.config import ROOT

SECRETS_DIR = ROOT / 'secrets'
CLIENT_FILE = SECRETS_DIR / 'client_secret.json'
TOKEN_FILE = SECRETS_DIR / 'token.json'
SCOPES = [
    'https://www.googleapis.com/auth/tasks',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.modify',
]


def login() -> Credentials:
    """Run the browser consent flow and store the token."""
    return auth.login(CLIENT_FILE, TOKEN_FILE, SCOPES)


def creds() -> Credentials:
    """Load the stored token, refreshing it when expired. Never opens a browser."""
    return auth.creds(TOKEN_FILE, SCOPES)


def status() -> tuple[bool, str]:
    """Whether a usable token exists, and why not if it doesn't."""
    return auth.status(TOKEN_FILE, SCOPES)


def service(name: str, version: str) -> Resource:
    """A Google API client, e.g. service('calendar', 'v3')."""
    return auth.service(name, version, creds())


if __name__ == '__main__':
    login()
    print(f'Logged in. Token saved to {TOKEN_FILE}')
