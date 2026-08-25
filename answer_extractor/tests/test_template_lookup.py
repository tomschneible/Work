"""Pure logic tests for template_lookup -- list_folder itself is mocked
(its own request-shaping is covered in test_google_sheets_export.py), so
these only exercise the name-matching/walking logic."""
from unittest.mock import MagicMock, patch

import pytest

from answer_extractor.template_lookup import find_subfolder, find_template_file, resolve_template_folder

_FOLDER = "application/vnd.google-apps.folder"
_SHEET = "application/vnd.google-apps.spreadsheet"


def test_find_subfolder_matches_by_name_case_insensitively():
    listing = [
        {"id": "f1", "name": "Enhanced", "mimeType": _FOLDER},
        {"id": "f2", "name": "Legacy", "mimeType": _FOLDER},
    ]
    with patch("answer_extractor.template_lookup.list_folder", return_value=listing):
        assert find_subfolder(MagicMock(), "ROOT", "enhanced") == "f1"


def test_find_subfolder_raises_with_available_names_when_missing():
    listing = [{"id": "f1", "name": "Enhanced", "mimeType": _FOLDER}]
    with patch("answer_extractor.template_lookup.list_folder", return_value=listing):
        with pytest.raises(ValueError, match="Enhanced"):
            find_subfolder(MagicMock(), "ROOT", "Legacy")


def test_find_subfolder_raises_when_ambiguous():
    listing = [
        {"id": "f1", "name": "Enhanced", "mimeType": _FOLDER},
        {"id": "f2", "name": "enhanced", "mimeType": _FOLDER},
    ]
    with patch("answer_extractor.template_lookup.list_folder", return_value=listing):
        with pytest.raises(ValueError, match="ambiguous"):
            find_subfolder(MagicMock(), "ROOT", "Enhanced")


def test_find_subfolder_ignores_non_folder_entries():
    listing = [
        {"id": "f1", "name": "Enhanced", "mimeType": _SHEET},  # a file, not a folder -- shouldn't match
        {"id": "f2", "name": "Enhanced", "mimeType": _FOLDER},
    ]
    with patch("answer_extractor.template_lookup.list_folder", return_value=listing):
        assert find_subfolder(MagicMock(), "ROOT", "Enhanced") == "f2"


def test_resolve_template_folder_walks_every_level_in_order():
    calls = []

    def _fake_list_folder(drive, folder_id):
        calls.append(folder_id)
        if folder_id == "ROOT":
            return [{"id": "ACT_ID", "name": "ACT", "mimeType": _FOLDER}]
        if folder_id == "ACT_ID":
            return [{"id": "ENHANCED_ID", "name": "Enhanced", "mimeType": _FOLDER}]
        raise AssertionError(f"unexpected folder id {folder_id}")

    with patch("answer_extractor.template_lookup.list_folder", side_effect=_fake_list_folder):
        result = resolve_template_folder(MagicMock(), "ROOT", ["ACT", "Enhanced"])

    assert result == "ENHANCED_ID"
    assert calls == ["ROOT", "ACT_ID"]


def test_find_template_file_matches_by_code_substring():
    listing = [
        {"id": "t1", "name": "ACT 25MC1", "mimeType": _SHEET},
        {"id": "t2", "name": "ACT 25MC2", "mimeType": _SHEET},
    ]
    with patch("answer_extractor.template_lookup.list_folder", return_value=listing):
        result = find_template_file(MagicMock(), "FOLDER", "25MC1")

    assert result == {"id": "t1", "name": "ACT 25MC1", "mimeType": _SHEET}


def test_find_template_file_ignores_non_spreadsheet_files():
    listing = [
        {"id": "t1", "name": "ACT 25MC1 notes", "mimeType": "text/plain"},
        {"id": "t2", "name": "ACT 25MC1", "mimeType": _SHEET},
    ]
    with patch("answer_extractor.template_lookup.list_folder", return_value=listing):
        result = find_template_file(MagicMock(), "FOLDER", "25MC1")

    assert result["id"] == "t2"


def test_find_template_file_raises_with_available_names_when_no_match():
    listing = [{"id": "t1", "name": "ACT 25MC1", "mimeType": _SHEET}]
    with patch("answer_extractor.template_lookup.list_folder", return_value=listing):
        with pytest.raises(ValueError, match="25MC9"):
            find_template_file(MagicMock(), "FOLDER", "25MC9")


def test_find_template_file_raises_when_ambiguous():
    listing = [
        {"id": "t1", "name": "ACT 25MC1 (old)", "mimeType": _SHEET},
        {"id": "t2", "name": "ACT 25MC1", "mimeType": _SHEET},
    ]
    with patch("answer_extractor.template_lookup.list_folder", return_value=listing):
        with pytest.raises(ValueError, match="ambiguous|More than one"):
            find_template_file(MagicMock(), "FOLDER", "25MC1")
