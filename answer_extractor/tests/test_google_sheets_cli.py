"""Tests for google_sheets_cli's `hide-gridlines` and
`repair-simplified-calculations` commands -- `list-folder` and the
OAuth-consent completion are thin, interactive/setup-only wrappers not
covered here (see the module's own docstring). repair_calculations_writes'
own transform logic is covered in test_sat_simplified_template_repair.py;
these only check this command's own wiring (download, dispatch, write,
report)."""
import io
from unittest.mock import MagicMock, patch

import openpyxl

from answer_extractor.google_sheets_cli import main
from answer_extractor.google_sheets_export import CellWrite

_MODULE = "answer_extractor.google_sheets_cli"


def _xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_hide_gridlines_command_calls_through_with_the_given_file_id():
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines") as hide_mock:
        exit_code = main(["hide-gridlines", "--file-id", "TEMPLATE_ID"])

    assert exit_code == 0
    assert hide_mock.call_args[0][1] == "TEMPLATE_ID"


def test_hide_gridlines_command_passes_the_sheets_service_not_drive():
    sheets_service = MagicMock(name="sheets-service")
    drive_service = MagicMock(name="drive-service")
    with patch(f"{_MODULE}.build_services", return_value=(drive_service, sheets_service)), \
         patch(f"{_MODULE}.hide_gridlines") as hide_mock:
        main(["hide-gridlines", "--file-id", "TEMPLATE_ID"])

    assert hide_mock.call_args[0][0] is sheets_service


def test_hide_gridlines_command_accepts_multiple_file_ids_in_one_call():
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines") as hide_mock:
        exit_code = main(["hide-gridlines", "--file-id", "ID_ONE", "ID_TWO", "ID_THREE"])

    assert exit_code == 0
    assert [call.args[1] for call in hide_mock.call_args_list] == ["ID_ONE", "ID_TWO", "ID_THREE"]


def test_hide_gridlines_command_keeps_going_past_one_files_failure_and_reports_it(capsys):
    def _fake_hide(sheets, file_id):
        if file_id == "BAD_ID":
            raise ValueError("nope")

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines", side_effect=_fake_hide) as hide_mock:
        exit_code = main(["hide-gridlines", "--file-id", "GOOD_ID", "BAD_ID"])

    assert exit_code == 1  # at least one failure -- worth a non-zero exit
    assert [call.args[1] for call in hide_mock.call_args_list] == ["GOOD_ID", "BAD_ID"]
    err = capsys.readouterr().err
    assert "BAD_ID" in err and "nope" in err


def test_hide_gridlines_command_prints_no_summary_line_for_a_single_file(capsys):
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines"):
        main(["hide-gridlines", "--file-id", "TEMPLATE_ID"])

    assert "succeeded" not in capsys.readouterr().out


def test_hide_gridlines_command_prints_a_summary_line_for_multiple_files(capsys):
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.hide_gridlines"):
        main(["hide-gridlines", "--file-id", "ID_ONE", "ID_TWO"])

    assert "2/2 succeeded." in capsys.readouterr().out


def test_repair_simplified_calculations_downloads_reference_and_writes_to_target(capsys):
    fake_writes = [
        CellWrite("Student Responses", 26, 4, "=REPAIRED_TOTAL()"),
        CellWrite("Calculations", 2, 2, "=REPAIRED_DOMAIN()"),
    ]
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()) as export_mock, \
         patch(f"{_MODULE}.repair_calculations_writes", return_value=fake_writes) as repair_mock, \
         patch(f"{_MODULE}.write_cells") as write_mock:
        exit_code = main(
            [
                "repair-simplified-calculations",
                "--reference-file-id",
                "REF_ID",
                "--target-file-id",
                "TARGET_ID",
            ]
        )

    assert exit_code == 0
    assert export_mock.call_args[0][1] == "REF_ID"  # the reference, downloaded read-only
    repair_mock.assert_called_once()  # given the workbook export_xlsx's bytes loaded into
    # One write_cells call per cell, not one big batch -- see this
    # command's own docstring for why (one protected cell shouldn't hide
    # whether the rest of the batch would have succeeded).
    assert [call.args[1] for call in write_mock.call_args_list] == ["TARGET_ID", "TARGET_ID"]
    assert [call.args[2] for call in write_mock.call_args_list] == [[fake_writes[0]], [fake_writes[1]]]
    out = capsys.readouterr().out
    assert "2/2" in out and "TARGET_ID" in out


def test_repair_simplified_calculations_passes_the_sheets_service_not_drive():
    sheets_service = MagicMock(name="sheets-service")
    drive_service = MagicMock(name="drive-service")
    with patch(f"{_MODULE}.build_services", return_value=(drive_service, sheets_service)), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()) as export_mock, \
         patch(f"{_MODULE}.repair_calculations_writes", return_value=[CellWrite("S", 1, 1, "=X()")]), \
         patch(f"{_MODULE}.write_cells") as write_mock:
        main(["repair-simplified-calculations", "--reference-file-id", "REF_ID", "--target-file-id", "TARGET_ID"])

    assert export_mock.call_args[0][0] is drive_service  # export_xlsx takes drive, not sheets
    assert write_mock.call_args[0][0] is sheets_service  # write_cells takes sheets, not drive


def test_repair_simplified_calculations_reports_and_fails_when_nothing_matches(capsys):
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()), \
         patch(f"{_MODULE}.repair_calculations_writes", return_value=[]), \
         patch(f"{_MODULE}.write_cells") as write_mock:
        exit_code = main(
            ["repair-simplified-calculations", "--reference-file-id", "REF_ID", "--target-file-id", "TARGET_ID"]
        )

    assert exit_code == 1
    write_mock.assert_not_called()  # nothing to push -- and never touch the target on an empty result


def test_repair_simplified_calculations_keeps_going_past_one_cells_failure_and_reports_it(capsys):
    """A protected cell fails its own write_cells call -- confirmed live
    this doesn't fail the whole batch any more (that used to hide
    whether anything else was also blocked): the other cells still get
    written, and the failure is reported by its own coordinate."""
    fake_writes = [
        CellWrite("Calculations", 2, 2, "=REPAIRED_ONE()"),  # B2
        CellWrite("Calculations", 2, 3, "=REPAIRED_TWO()"),  # C2 -- this one is "protected"
        CellWrite("Student Responses", 26, 4, "=REPAIRED_THREE()"),  # D26
    ]

    def _fake_write_cells(sheets, file_id, cells):
        if cells[0].sheet == "Calculations" and cells[0].column == 3:
            raise Exception("Invalid data[0]: You are trying to edit a protected cell or object.")

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()), \
         patch(f"{_MODULE}.repair_calculations_writes", return_value=fake_writes), \
         patch(f"{_MODULE}.write_cells", side_effect=_fake_write_cells) as write_mock:
        exit_code = main(
            ["repair-simplified-calculations", "--reference-file-id", "REF_ID", "--target-file-id", "TARGET_ID"]
        )

    assert exit_code == 1  # at least one failure -- worth a non-zero exit, same as hide-gridlines
    assert len(write_mock.call_args_list) == 3  # all three attempted, not stopped after the failure
    result = capsys.readouterr()
    assert "2/3" in result.out
    assert "Calculations!C2" in result.err
