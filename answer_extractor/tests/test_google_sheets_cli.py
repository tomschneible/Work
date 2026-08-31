"""Tests for google_sheets_cli's `hide-gridlines` and
`repair-simplified-calculations` commands -- `list-folder` and the
OAuth-consent completion are thin, interactive/setup-only wrappers not
covered here (see the module's own docstring). repair_calculations_writes'
own transform logic is covered in test_sat_simplified_template_repair.py;
these only check this command's own wiring (download, dispatch, write,
report) -- including its own chunking and rate-limit retry around
write_cells (_write_with_rate_limit_retry, _is_rate_limit_error)."""
import io
from unittest.mock import MagicMock, patch

import httplib2
import openpyxl
from googleapiclient.errors import HttpError

from answer_extractor.google_sheets_cli import _RATE_LIMIT_RETRY_SECONDS, _WRITE_CHUNK_SIZE, main
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
    since _is_rate_limit_error checks `isinstance(exc, HttpError)`
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


def test_repair_simplified_calculations_retries_a_rate_limited_chunk_and_then_succeeds(capsys):
    """A transient 429 shouldn't fail the run at all -- retried automatically, and once
    it clears, reported as a plain success like any other cell."""
    fake_writes = [CellWrite("Calculations", 2, 2, "=REPAIRED_ONE()")]
    attempts = {"n": 0}

    def _fake_write_cells(sheets, file_id, cells):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _http_error(429)
        # Second attempt succeeds.

    with patch(f"{_MODULE}.build_services", return_value=(MagicMock(), MagicMock())), \
         patch(f"{_MODULE}.export_xlsx", return_value=_xlsx_bytes()), \
         patch(f"{_MODULE}.repair_calculations_writes", return_value=fake_writes), \
         patch(f"{_MODULE}.write_cells", side_effect=_fake_write_cells) as write_mock, \
         patch(f"{_MODULE}.time.sleep") as sleep_mock:
        exit_code = main(
            ["repair-simplified-calculations", "--reference-file-id", "REF_ID", "--target-file-id", "TARGET_ID"]
        )

    assert exit_code == 0  # the retry succeeded -- no failure reported anywhere
    assert len(write_mock.call_args_list) == 2  # the 429'd attempt, then the retry that succeeded
    sleep_mock.assert_called_once_with(_RATE_LIMIT_RETRY_SECONDS)
    assert "1/1" in capsys.readouterr().out


def test_repair_simplified_calculations_reports_a_still_rate_limited_cell_separately_from_a_protected_one(capsys):
    """Two different permanent-vs-transient failures in the same run, kept distinct in
    the final report -- a still-rate-limited cell just needs a re-run of the same
    command; a protected one needs a human to change its protection in Sheets first.
    Conflating them (the pre-chunking message did, since it had only ever seen the
    protected-cell case) would send someone to Sheets' protection settings to fix
    something re-running the command would have resolved on its own."""
    fake_writes = [
        CellWrite("Calculations", 2, 2, "=REPAIRED_RATE_LIMITED()"),  # B2 -- 429s every attempt
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
         patch(f"{_MODULE}.write_cells", side_effect=_fake_write_cells), \
         patch(f"{_MODULE}.time.sleep"):
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


def test_is_rate_limit_error_is_true_only_for_a_real_429_http_error():
    from answer_extractor.google_sheets_cli import _is_rate_limit_error

    assert _is_rate_limit_error(_http_error(429)) is True
    assert _is_rate_limit_error(_http_error(400)) is False  # a real HttpError, wrong status
    assert _is_rate_limit_error(Exception("Invalid data[0]: protected cell")) is False  # not an HttpError at all
