"""Pure logic tests for google_auth.get_credentials's caching/refresh/
interactive-flow branching -- mocked throughout, since none of the real
things this talks to (a cached token file, Google's token endpoint, a
browser) are available in a test run."""
from unittest.mock import MagicMock, patch

import pytest

from answer_extractor.google_auth import SCOPES, get_credentials


def _paths(tmp_path):
    return tmp_path / "client_secret.json", tmp_path / "token.json"


def test_get_credentials_returns_a_cached_valid_token_without_any_flow(tmp_path):
    client_secret_path, token_cache_path = _paths(tmp_path)
    token_cache_path.write_text("{}")  # content is irrelevant -- from_authorized_user_file is mocked below

    cached = MagicMock(valid=True)
    with patch("answer_extractor.google_auth.Credentials.from_authorized_user_file", return_value=cached) as loader, \
         patch("answer_extractor.google_auth.InstalledAppFlow") as flow_cls:
        result = get_credentials(client_secret_path, token_cache_path)

    loader.assert_called_once()
    flow_cls.from_client_secrets_file.assert_not_called()
    assert result is cached


def test_get_credentials_refreshes_an_expired_token_and_rewrites_the_cache(tmp_path):
    client_secret_path, token_cache_path = _paths(tmp_path)
    token_cache_path.write_text("{}")

    cached = MagicMock(valid=False, expired=True, refresh_token="r")

    def _refresh(request):
        cached.valid = True  # a real Credentials.refresh() flips .valid on success

    cached.refresh.side_effect = _refresh
    cached.to_json.return_value = '{"refreshed": true}'

    with patch("answer_extractor.google_auth.Credentials.from_authorized_user_file", return_value=cached), \
         patch("answer_extractor.google_auth.InstalledAppFlow") as flow_cls:
        result = get_credentials(client_secret_path, token_cache_path)

    cached.refresh.assert_called_once()
    flow_cls.from_client_secrets_file.assert_not_called()
    assert result is cached


def test_get_credentials_falls_back_to_interactive_flow_when_refresh_fails(tmp_path):
    client_secret_path, token_cache_path = _paths(tmp_path)
    client_secret_path.parent.mkdir(parents=True, exist_ok=True)
    client_secret_path.write_text("{}")
    token_cache_path.write_text("{}")

    cached = MagicMock(valid=False, expired=True, refresh_token="r")
    cached.refresh.side_effect = Exception("refresh token revoked")

    fresh = MagicMock()
    fresh.to_json.return_value = '{"fresh": true}'
    flow = MagicMock()
    flow.run_local_server.return_value = fresh

    with patch("answer_extractor.google_auth.Credentials.from_authorized_user_file", return_value=cached), \
         patch("answer_extractor.google_auth.InstalledAppFlow") as flow_cls:
        flow_cls.from_client_secrets_file.return_value = flow
        result = get_credentials(client_secret_path, token_cache_path)

    flow_cls.from_client_secrets_file.assert_called_once()
    flow.run_local_server.assert_called_once()
    assert result is fresh
    assert token_cache_path.read_text() == '{"fresh": true}'


def test_get_credentials_runs_the_interactive_flow_when_nothing_is_cached_yet(tmp_path):
    client_secret_path, token_cache_path = _paths(tmp_path)
    client_secret_path.parent.mkdir(parents=True, exist_ok=True)
    client_secret_path.write_text("{}")
    assert not token_cache_path.exists()

    fresh = MagicMock()
    fresh.to_json.return_value = '{"fresh": true}'
    flow = MagicMock()
    flow.run_local_server.return_value = fresh

    with patch("answer_extractor.google_auth.InstalledAppFlow") as flow_cls:
        flow_cls.from_client_secrets_file.return_value = flow
        result = get_credentials(client_secret_path, token_cache_path)

    flow_cls.from_client_secrets_file.assert_called_once_with(str(client_secret_path), SCOPES)
    assert result is fresh
    # The cache directory is created on demand, not assumed to pre-exist.
    assert token_cache_path.read_text() == '{"fresh": true}'


def test_get_credentials_raises_a_clear_error_when_the_client_secret_is_missing(tmp_path):
    client_secret_path, token_cache_path = _paths(tmp_path)
    assert not client_secret_path.exists()
    assert not token_cache_path.exists()

    with pytest.raises(FileNotFoundError, match=str(client_secret_path)):
        get_credentials(client_secret_path, token_cache_path)


def test_get_credentials_honors_environment_variable_overrides(tmp_path, monkeypatch):
    client_secret_path, token_cache_path = _paths(tmp_path)
    client_secret_path.parent.mkdir(parents=True, exist_ok=True)
    client_secret_path.write_text("{}")
    monkeypatch.setenv("ANSWER_EXTRACTOR_GOOGLE_CLIENT_SECRET", str(client_secret_path))
    monkeypatch.setenv("ANSWER_EXTRACTOR_GOOGLE_TOKEN_CACHE", str(token_cache_path))

    fresh = MagicMock()
    fresh.to_json.return_value = "{}"
    flow = MagicMock()
    flow.run_local_server.return_value = fresh

    with patch("answer_extractor.google_auth.InstalledAppFlow") as flow_cls:
        flow_cls.from_client_secrets_file.return_value = flow
        get_credentials()  # no explicit paths -- must pick up the env vars

    flow_cls.from_client_secrets_file.assert_called_once_with(str(client_secret_path), SCOPES)
    assert token_cache_path.exists()
