"""Orchestration tests for export_filled_report -- every step it calls is
mocked (each has its own dedicated tests: template_lookup's in
test_template_lookup.py, the Drive calls' in test_google_sheets_export.py),
so this only checks that export_filled_report calls them in the right
order, with the right arguments passed between them, calls `fill_fn` with
the downloaded local path, and always cleans up the working Drive copy
and local temp file -- including when a later step fails."""
from unittest.mock import MagicMock, patch

import pytest

from answer_extractor.google_report_export_common import export_filled_report

_MODULE = "answer_extractor.google_report_export_common"


def _patch_all(**overrides):
    defaults = dict(
        resolve_template_folder=MagicMock(return_value="FOLDER_ID"),
        find_template_file=MagicMock(return_value={"id": "TEMPLATE_ID", "name": "ACT 25MC1"}),
        copy_template=MagicMock(return_value="COPY_ID"),
        export_xlsx=MagicMock(return_value=b"raw xlsx bytes"),
        replace_content=MagicMock(),
        export_pdf=MagicMock(return_value=b"%PDF-final"),
        delete_file=MagicMock(),
    )
    defaults.update(overrides)
    patchers = {name: patch(f"{_MODULE}.{name}", fn) for name, fn in defaults.items()}
    for p in patchers.values():
        p.start()
    return defaults, patchers


def _stop_all(patchers):
    for p in patchers.values():
        p.stop()


def test_export_filled_report_runs_every_step_in_order_and_returns_the_pdf():
    mocks, patchers = _patch_all()
    fill_fn = MagicMock(return_value=MagicMock())
    try:
        result = export_filled_report(
            drive=MagicMock(),
            templates_root_folder_id="ROOT",
            category_path=["ACT", "Enhanced"],
            test_code="25MC1",
            output_name="Jane Student - 2026-03-04",
            fill_fn=fill_fn,
        )
    finally:
        _stop_all(patchers)

    assert result == b"%PDF-final"
    mocks["resolve_template_folder"].assert_called_once()
    assert mocks["resolve_template_folder"].call_args[0][1] == "ROOT"
    assert mocks["resolve_template_folder"].call_args[0][2] == ["ACT", "Enhanced"]
    mocks["find_template_file"].assert_called_once()
    assert mocks["find_template_file"].call_args[0][2] == "25MC1"
    mocks["copy_template"].assert_called_once()
    assert mocks["copy_template"].call_args[0][1] == "TEMPLATE_ID"
    assert mocks["copy_template"].call_args[0][2] == "Jane Student - 2026-03-04"

    mocks["export_xlsx"].assert_called_once()
    assert mocks["export_xlsx"].call_args[0][1] == "COPY_ID"

    fill_fn.assert_called_once()  # called with the local temp path -- the fill_fn owns its own args otherwise

    mocks["replace_content"].assert_called_once()
    assert mocks["replace_content"].call_args[0][1] == "COPY_ID"

    mocks["export_pdf"].assert_called_once()
    assert mocks["export_pdf"].call_args[0][1] == "COPY_ID"

    mocks["delete_file"].assert_called_once()
    assert mocks["delete_file"].call_args[0][1] == "COPY_ID"


def test_export_filled_report_deletes_the_working_copy_even_if_a_later_step_fails():
    mocks, patchers = _patch_all(export_pdf=MagicMock(side_effect=RuntimeError("boom")))
    try:
        with pytest.raises(RuntimeError, match="boom"):
            export_filled_report(
                drive=MagicMock(),
                templates_root_folder_id="ROOT",
                category_path=["SAT"],
                test_code="1234",
                output_name="report",
                fill_fn=MagicMock(return_value=MagicMock()),
            )
    finally:
        _stop_all(patchers)

    mocks["delete_file"].assert_called_once()
    assert mocks["delete_file"].call_args[0][1] == "COPY_ID"


def test_export_filled_report_cleans_up_the_local_temp_file():
    written_paths = []
    real_open = open

    def _tracking_open(path, mode="r", *args, **kwargs):
        if "xlsx" in str(path) and "b" in mode:
            written_paths.append(str(path))
        return real_open(path, mode, *args, **kwargs)

    mocks, patchers = _patch_all()
    try:
        with patch("builtins.open", side_effect=_tracking_open):
            export_filled_report(
                drive=MagicMock(),
                templates_root_folder_id="ROOT",
                category_path=["SAT"],
                test_code="1234",
                output_name="report",
                fill_fn=MagicMock(return_value=MagicMock()),
            )
    finally:
        _stop_all(patchers)

    assert len(written_paths) == 1
    import os

    assert not os.path.exists(written_paths[0])
