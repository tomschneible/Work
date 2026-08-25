"""OAuth credentials for the Google Sheets/Drive score-report export path
(see README's "Google Sheets score reports" section).

Deliberately keeps two pieces of state on disk, in two different places,
neither inside this repo checkout:

  - The OAuth *client* secret (`client_secret.json`, downloaded once from
    Google Cloud Console -- see the README) describes the *app*, not any
    one Google account. It's genuinely secret (whoever holds it can
    impersonate this app to Google), so it lives under ~/.config, next to
    other real local credentials, and is never read from or written into
    this repo -- committing it, even to a private repo, would leak it
    into git history permanently.
  - The *token* cache (an OAuth refresh token for whichever Google account
    last completed the interactive consent screen) lives under ~/.cache,
    since -- unlike the client secret -- it's disposable: deleting it just
    means the next run re-prompts for consent. That disposability is the
    whole point, not an afterthought: this only ever needs completing
    once per machine, logged in as the org's dedicated account for this
    program (not a person's own login) -- from then on every run reuses
    the cached refresh token silently, with no browser needed.

Both paths are overridable via environment variables so a caller (tests,
or a future second identity) isn't stuck with the single default either.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Both scopes are required, not just "spreadsheets": duplicating a
# template (Drive's files.copy), exporting the filled-in copy as a PDF
# (Drive's files.export), and deleting the working copy afterward are all
# Drive operations, not Sheets ones -- the Sheets API alone only covers
# reading/writing cell data in a file that already exists.
#
# Full "drive" scope, not the narrower "drive.file": drive.file only ever
# grants access to files this app itself created (or that the user
# explicitly hands it through a Picker UI) -- it cannot see or copy a
# pre-existing template someone else put in Drive, which is exactly what
# this needs to do. That does make this a "sensitive" scope in Google's
# classification, but since the OAuth consent screen is Internal (see the
# README) rather than a public External app, that classification never
# triggers Google's app-review process here.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_DEFAULT_CLIENT_SECRET_PATH = Path.home() / ".config" / "answer_extractor" / "client_secret.json"
_DEFAULT_TOKEN_CACHE_PATH = Path.home() / ".cache" / "answer_extractor" / "google_token.json"


def get_credentials(
    client_secret_path: Optional[Path] = None,
    token_cache_path: Optional[Path] = None,
) -> Credentials:
    """Return valid, ready-to-use Google API credentials -- refreshing a
    cached token silently if possible, or running an interactive
    (browser-based) consent flow if not.

    `client_secret_path`/`token_cache_path` default to
    ANSWER_EXTRACTOR_GOOGLE_CLIENT_SECRET / ANSWER_EXTRACTOR_GOOGLE_TOKEN_CACHE
    if those env vars are set, then to the fixed paths under ~/.config and
    ~/.cache described in this module's own docstring.

    The interactive flow (google_auth_oauthlib's `run_local_server`) opens
    a real browser and listens on a local port for the redirect -- it can
    only complete on a machine where that's possible, i.e. wherever you
    actually run this the first time, not an unattended lab laptop.
    Delete `token_cache_path` to force it to run again (e.g. after it's
    been revoked, or to switch which account this points at).
    """
    client_secret_path = client_secret_path or Path(
        os.environ.get("ANSWER_EXTRACTOR_GOOGLE_CLIENT_SECRET", _DEFAULT_CLIENT_SECRET_PATH)
    )
    token_cache_path = token_cache_path or Path(
        os.environ.get("ANSWER_EXTRACTOR_GOOGLE_TOKEN_CACHE", _DEFAULT_TOKEN_CACHE_PATH)
    )

    creds: Optional[Credentials] = None
    if token_cache_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_cache_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            # Refresh token itself revoked/expired -- shouldn't happen in
            # normal operation on an Internal-audience consent screen (no
            # 7-day Testing-mode expiry), but fall through to a fresh
            # interactive consent below rather than propagate a
            # refresh-specific error upward, in case it was revoked by
            # hand (e.g. a security event, or the account's password
            # changed).
            creds = None

    if not creds or not creds.valid:
        if not client_secret_path.exists():
            raise FileNotFoundError(
                f"Google OAuth client secret not found at {client_secret_path}. "
                "Download it from Google Cloud Console (APIs & Services -> "
                "Credentials -> your OAuth client -> Download JSON) and place "
                "it there, or point ANSWER_EXTRACTOR_GOOGLE_CLIENT_SECRET at "
                "wherever you keep it -- see the README's \"Google Sheets "
                "score reports\" section."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
        creds = flow.run_local_server(port=0)
        token_cache_path.parent.mkdir(parents=True, exist_ok=True)
        token_cache_path.write_text(creds.to_json())

    return creds
