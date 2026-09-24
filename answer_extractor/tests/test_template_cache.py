"""template_cache.template_xlsx: a template's download is kept on disk and
reused while Drive's version number for it stays the same -- Drive itself
(file_version, export_xlsx) is mocked; the cache folder is each test's own
(see conftest.py)."""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from answer_extractor.template_cache import template_xlsx

_MODULE = "answer_extractor.template_cache"


def _cache_dir() -> Path:
    return Path(os.environ["ANSWER_EXTRACTOR_TEMPLATE_CACHE_DIR"])


def test_a_template_is_downloaded_once_then_reused_while_its_version_is_unchanged():
    with patch(f"{_MODULE}.file_version", return_value="7"), \
         patch(f"{_MODULE}.export_xlsx", return_value=b"xlsx v7") as export_mock:
        first = template_xlsx(MagicMock(), "TEMPLATE_ID")
        second = template_xlsx(MagicMock(), "TEMPLATE_ID")

    assert first == second == b"xlsx v7"
    export_mock.assert_called_once()
    assert (_cache_dir() / "TEMPLATE_ID" / "7.xlsx").read_bytes() == b"xlsx v7"


def test_a_changed_template_is_downloaded_again_and_its_old_copy_removed():
    with patch(f"{_MODULE}.file_version", return_value="7"), \
         patch(f"{_MODULE}.export_xlsx", return_value=b"xlsx v7"):
        template_xlsx(MagicMock(), "TEMPLATE_ID")
    with patch(f"{_MODULE}.file_version", return_value="8"), \
         patch(f"{_MODULE}.export_xlsx", return_value=b"xlsx v8") as export_mock:
        result = template_xlsx(MagicMock(), "TEMPLATE_ID")

    assert result == b"xlsx v8"
    export_mock.assert_called_once()
    assert [p.name for p in (_cache_dir() / "TEMPLATE_ID").iterdir()] == ["8.xlsx"]


def test_each_template_is_kept_separately():
    downloads = {"ACT_ID": b"act xlsx", "SAT_ID": b"sat xlsx"}
    with patch(f"{_MODULE}.file_version", return_value="3"), \
         patch(f"{_MODULE}.export_xlsx", side_effect=lambda drive, file_id: downloads[file_id]) as export_mock:
        assert template_xlsx(MagicMock(), "ACT_ID") == b"act xlsx"
        assert template_xlsx(MagicMock(), "SAT_ID") == b"sat xlsx"
        assert template_xlsx(MagicMock(), "ACT_ID") == b"act xlsx"

    assert export_mock.call_count == 2


def test_a_template_without_a_version_number_is_downloaded_every_time_and_never_kept():
    with patch(f"{_MODULE}.file_version", return_value=None), \
         patch(f"{_MODULE}.export_xlsx", return_value=b"xlsx") as export_mock:
        template_xlsx(MagicMock(), "TEMPLATE_ID")
        template_xlsx(MagicMock(), "TEMPLATE_ID")

    assert export_mock.call_count == 2
    assert not _cache_dir().exists()


def test_a_download_that_cant_be_kept_is_still_returned(tmp_path, monkeypatch):
    not_a_folder = tmp_path / "not-a-folder"
    not_a_folder.write_text("a file where the cache folder would go")
    monkeypatch.setenv("ANSWER_EXTRACTOR_TEMPLATE_CACHE_DIR", str(not_a_folder))
    with patch(f"{_MODULE}.file_version", return_value="7"), \
         patch(f"{_MODULE}.export_xlsx", return_value=b"xlsx v7"):
        assert template_xlsx(MagicMock(), "TEMPLATE_ID") == b"xlsx v7"


def test_nothing_half_written_is_left_behind_when_saving_fails():
    with patch(f"{_MODULE}.file_version", return_value="7"), \
         patch(f"{_MODULE}.export_xlsx", return_value=b"xlsx v7"), \
         patch(f"{_MODULE}.os.replace", side_effect=OSError("disk full")):
        assert template_xlsx(MagicMock(), "TEMPLATE_ID") == b"xlsx v7"

    assert list((_cache_dir() / "TEMPLATE_ID").iterdir()) == []
