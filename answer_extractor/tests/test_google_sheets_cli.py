"""Tests for google_sheets_cli's `hide-gridlines`,
`repair-simplified-calculations`, and `clear-notes` commands --
`list-folder` and the OAuth-consent completion are thin, interactive/
setup-only wrappers not covered here (see the module's own docstring).
repair_calculations_writes' and find_note_cells' own transform logic is
covered in test_sat_simplified_template_repair.py; these only check each
command's own wiring (download, dispatch, write, report) -- including
repair-simplified-calculations' own chunking. The rate-limit retry itself
now lives in google_sheets_export.write_cells (shared by every write
helper there, not just this command) -- see test_google_sheets_export.py
for that mechanism's own tests (_execute_with_rate_limit_retry,
is_rate_limit_error); write_cells is mocked out here (as a black box that
either succeeds or raises, the same as any other caller sees it, retries
already exhausted either way), so only this command's own reporting of a
still-rate-limited cell (distinct from a genuinely protected one) is
covered below."""
import io
from unittest.mock import MagicMock, patch

import httplib2
import openpyxl
from googleapiclient.errors import HttpError

from answer_extractor.google_sheets_cli import _WRITE_CHUNK_SIZE, main
from answer_extractor.google_sheets_export import CellWrite

_MODULE = "answer_extractor.google_sheets_cli"


def _xlsx_bytes() -> bytes:
    wb = openpyxl.Workbook()
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _http_error(status: int) -> HttpError:
    """Same construction test_google_report_export_common.py's own
    _http_error helper uses -- a real HttpError, not a generic Exception,
    since is_rate_limit_error checks `isinstance(exc, HttpError)`
    specifically (see that function's own docstring for why)."""
    return HttpError(httplib2.Response({"status": str(status)}), b'{"error": {"message": "nope"}}')


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
    # Both cells fit in one chunk (well under _WRITE_CHUNK_SIZE) -- one write_cells call
    # covering both, not two -- see the batching test below for a run large enough to
    # actually need more than one chunk.
    assert len(write_mock.call_args_list) == 1
    assert write_mock.call_args_list[0].args[1] == "TARGET_ID"
    assert write_mock.call_args_list[0].args[2] == fake_writes
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


def test_repair_simplified_calculations_batches_writes_into_chunks_not_one_call_per_cell(capsys):
    """More writes than fit in one chunk -- confirmed live this is what actually avoids
    the 60-writes-per-minute-per-user quota (see this command's own docstring): far
    fewer write_cells calls than cells, unlike the one-call-per-cell approach this
    replaced, which blew through it well before 216 cells were done."""
    fake_writes = [
        CellWrite("Calculations", 2, col, f"=REPAIRED_{col}()") for col in range(2, 2 + _WRITE_CHUNK_SIZE + 5)
    ]  # _WRITE_CHUNK_SIZE + 5 cells -- one full chunk, then a 5-cell remainder

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()), \
         patch(f"{_MODULE}.repair_calculations_writes", return_value=fake_writes), \
         patch(f"{_MODULE}.write_cells") as write_mock:
        exit_code = main(
            ["repair-simplified-calculations", "--reference-file-id", "REF_ID", "--target-file-id", "TARGET_ID"]
        )

    assert exit_code == 0
    assert len(write_mock.call_args_list) == 2  # not len(fake_writes) calls
    assert [len(call.args[2]) for call in write_mock.call_args_list] == [_WRITE_CHUNK_SIZE, 5]
    assert f"{len(fake_writes)}/{len(fake_writes)}" in capsys.readouterr().out


def test_repair_simplified_calculations_keeps_going_past_one_cells_failure_and_reports_it(capsys):
    """A protected cell fails its own chunk's write_cells call entirely -- confirmed live
    a batched call fails atomically the moment any one cell in it is blocked. That chunk
    falls back to one write per cell, and only the genuinely protected one fails --
    reported by its own coordinate, same guarantee the old one-call-per-cell approach
    existed to provide."""
    fake_writes = [
        CellWrite("Calculations", 2, 2, "=REPAIRED_ONE()"),  # B2
        CellWrite("Calculations", 2, 3, "=REPAIRED_TWO()"),  # C2 -- this one is "protected"
        CellWrite("Student Responses", 26, 4, "=REPAIRED_THREE()"),  # D26
    ]

    def _fake_write_cells(sheets, file_id, cells):
        # A batchUpdate call fails entirely if *any* cell in it is protected --
        # not just when the protected cell happens to be first in the list.
        if any(c.sheet == "Calculations" and c.column == 3 for c in cells):
            raise Exception("Invalid data[0]: You are trying to edit a protected cell or object.")

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()), \
         patch(f"{_MODULE}.repair_calculations_writes", return_value=fake_writes), \
         patch(f"{_MODULE}.write_cells", side_effect=_fake_write_cells) as write_mock:
        exit_code = main(
            ["repair-simplified-calculations", "--reference-file-id", "REF_ID", "--target-file-id", "TARGET_ID"]
        )

    assert exit_code == 1  # at least one failure -- worth a non-zero exit, same as hide-gridlines
    # 1 chunk attempt (all three cells, fails) + 3 individual fallback attempts = 4.
    assert len(write_mock.call_args_list) == 4
    result = capsys.readouterr()
    assert "2/3" in result.out
    assert "Calculations!C2" in result.err
    assert "couldn't be written -- likely a protected range" in result.err  # not the rate-limit message


def test_repair_simplified_calculations_reports_a_still_rate_limited_cell_separately_from_a_protected_one(capsys):
    """Two different permanent-vs-transient failures in the same run, kept distinct in
    the final report -- a still-rate-limited cell just needs a re-run of the same
    command; a protected one needs a human to change its protection in Sheets first.
    Conflating them (the pre-chunking message did, since it had only ever seen the
    protected-cell case) would send someone to Sheets' protection settings to fix
    something re-running the command would have resolved on its own.

    write_cells is mocked here as a black box, the same as every other test in this
    file -- by the time *this* command sees either exception, write_cells' own
    internal retry (google_sheets_export._execute_with_rate_limit_retry, tested on
    its own terms in test_google_sheets_export.py) has already given up, so there's
    no attempt-counting or sleep to simulate here at all; this only needs to check
    that main() still reports a real 429 differently from a permanent failure."""
    fake_writes = [
        CellWrite("Calculations", 2, 2, "=REPAIRED_RATE_LIMITED()"),  # B2 -- still 429ing
        CellWrite("Calculations", 2, 3, "=REPAIRED_PROTECTED()"),  # C2 -- genuinely protected
    ]

    def _fake_write_cells(sheets, file_id, cells):
        if any(c.column == 2 for c in cells):
            raise _http_error(429)
        if any(c.column == 3 for c in cells):
            raise Exception("Invalid data[0]: You are trying to edit a protected cell or object.")

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()), \
         patch(f"{_MODULE}.repair_calculations_writes", return_value=fake_writes), \
         patch(f"{_MODULE}.write_cells", side_effect=_fake_write_cells):
        exit_code = main(
            ["repair-simplified-calculations", "--reference-file-id", "REF_ID", "--target-file-id", "TARGET_ID"]
        )

    assert exit_code == 1
    result = capsys.readouterr()
    assert "0/2" in result.out
    assert "Calculations!B2 is still rate-limited after retrying." in result.err
    assert "still rate-limited" in result.err
    assert "couldn't be written -- likely a protected range" in result.err
    assert "Calculations!C2" in result.err


def test_clear_notes_command_downloads_the_target_and_clears_its_notes(capsys):
    fake_cells = [("Student Responses", 45, 8), ("Student Responses", 45, 9)]
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()) as export_mock, \
         patch(f"{_MODULE}.find_note_cells", return_value=fake_cells) as find_mock, \
         patch(f"{_MODULE}.clear_notes") as clear_mock:
        exit_code = main(["clear-notes", "--file-id", "TEMPLATE_ID"])

    assert exit_code == 0
    assert export_mock.call_args[0][1] == "TEMPLATE_ID"  # downloaded read-only, to locate the notes
    find_mock.assert_called_once()
    assert clear_mock.call_args[0][1] == "TEMPLATE_ID"
    assert clear_mock.call_args[0][2] == fake_cells
    assert "Cleared 2 note(s) from TEMPLATE_ID." in capsys.readouterr().out


def test_clear_notes_command_passes_the_sheets_service_not_drive():
    sheets_service = MagicMock(name="sheets-service")
    drive_service = MagicMock(name="drive-service")
    with patch(f"{_MODULE}.build_services", return_value=(drive_service, sheets_service)), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()) as export_mock, \
         patch(f"{_MODULE}.find_note_cells", return_value=[("Student Responses", 0, 0)]), \
         patch(f"{_MODULE}.clear_notes") as clear_mock:
        main(["clear-notes", "--file-id", "TEMPLATE_ID"])

    assert export_mock.call_args[0][0] is drive_service  # export_xlsx takes drive, not sheets
    assert clear_mock.call_args[0][0] is sheets_service  # clear_notes takes sheets, not drive


def test_clear_notes_command_reports_when_nothing_to_clear_without_failing(capsys):
    """Unlike repair-simplified-calculations' empty result (almost
    certainly the wrong file), a template with no notes left is a
    perfectly normal outcome -- e.g. this command already ran against it
    once -- so this doesn't fail the run."""
    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()), \
         patch(f"{_MODULE}.find_note_cells", return_value=[]), \
         patch(f"{_MODULE}.clear_notes") as clear_mock:
        exit_code = main(["clear-notes", "--file-id", "TEMPLATE_ID"])

    assert exit_code == 0
    clear_mock.assert_not_called()
    assert "No notes found on TEMPLATE_ID" in capsys.readouterr().out


def test_clear_notes_command_keeps_going_past_one_files_failure_and_reports_it(capsys):
    def _fake_export_xlsx(drive, file_id):
        if file_id == "BAD_ID":
            raise ValueError("nope")
        return _xlsx_bytes()

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", side_effect=_fake_export_xlsx), \
         patch(f"{_MODULE}.find_note_cells", return_value=[("Student Responses", 0, 0)]), \
         patch(f"{_MODULE}.clear_notes"):
        exit_code = main(["clear-notes", "--file-id", "GOOD_ID", "BAD_ID"])

    assert exit_code == 1  # at least one failure -- worth a non-zero exit, same as hide-gridlines
    result = capsys.readouterr()
    assert "Cleared 1 note(s) from GOOD_ID." in result.out
    assert "BAD_ID" in result.err and "nope" in result.err
    assert "1/2 succeeded." in result.out
