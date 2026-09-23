"""Authenticated Google clients, adapted from the former ~/.config/google_api/gapi.py.

The OAuth client secret and the token both live in the project's secrets/ folder (gitignored).
Run `uv run python -m automation_desk.google_auth` once to log in.
"""

import os

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from automation_desk.config import ROOT

SECRETS_DIR = ROOT / 'secrets'
CLIENT_FILE = SECRETS_DIR / 'client_secret.json'
TOKEN_FILE = SECRETS_DIR / 'token.json'
SCOPES = [
    'https://www.googleapis.com/auth/tasks',
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.modify',
]

# CLAUDE> accept a token with fewer scopes so login() can name the unticked box instead of crashing
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'


class AuthError(RuntimeError):
    """No usable Google token; the user has to log in (again)."""


def login() -> Credentials:
    """Run the browser consent flow and store the token."""
    if not CLIENT_FILE.exists():
        raise AuthError(f'No OAuth client secret at {CLIENT_FILE}. Download it from Google Cloud Console '
                        '(APIs & Services, Credentials, OAuth client of type Desktop) and save it there.')
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    missing = [s.rsplit('/', 1)[-1] for s in SCOPES if s not in set(creds.granted_scopes or SCOPES)]
    if missing:
        raise AuthError(f"Not granted: {', '.join(missing)}. Log in again and tick every box.")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    TOKEN_FILE.chmod(0o600)
    return creds


def creds() -> Credentials:
    """Load the stored token, refreshing it when expired. Never opens a browser."""
    if not TOKEN_FILE.exists():
        raise AuthError('Not logged in to Google yet.')
    c = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not c.valid:
        try:
            c.refresh(Request())
        except RefreshError as error:
            # CLAUDE> Testing-mode consent screens expire refresh tokens after about a week
            raise AuthError(f'Google login expired: {error}') from error
        TOKEN_FILE.write_text(c.to_json())
    return c


def status() -> tuple[bool, str]:
    """Whether a usable token exists, and why not if it doesn't."""
    try:
        c = creds()
    except AuthError as error:
        return False, str(error)
    missing = [s.rsplit('/', 1)[-1] for s in SCOPES if c.scopes and s not in c.scopes]
    if missing:
        return False, f"Token lacks scope: {', '.join(missing)}. Log in again."
    return True, 'Logged in'


def service(name: str, version: str) -> Resource:
    """A Google API client, e.g. service('tasks', 'v1')."""
    return build(name, version, credentials=creds(), cache_discovery=False)


if __name__ == '__main__':
    login()
    print(f'Logged in. Token saved to {TOKEN_FILE}')
