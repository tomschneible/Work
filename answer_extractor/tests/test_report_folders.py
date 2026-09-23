"""Tests for report_folders -- Drive is replaced by a small in-memory
folder tree (FakeDrive) patched in for list_folder/get_file/create_folder/
upload_bytes, whose own request-shaping is covered in
test_google_sheets_export.py."""
import datetime as dt
import itertools
from pathlib import Path

import pytest

from answer_extractor import report_folders
from answer_extractor.report_folders import (
    ReportFolders,
    date_folder_names,
    dropped_file_copy_name,
    find_ancestor_folder,
    find_or_create_subfolder,
)

_FOLDER = "application/vnd.google-apps.folder"
_SHEET = "application/vnd.google-apps.spreadsheet"


class FakeDrive:
    """Files by id, each with a name, mimeType, and parent id (None for a
    root, or for a folder whose parent this account can't see)."""

    def __init__(self):
        self.files = {}
        self.created = []  # (parent id, name) of each folder create_folder made, in order
        self.uploads = []  # (folder id, name, content, mime type) of each file upload_bytes stored
        self.failing_uploads = set()  # names upload_bytes should fail on
        self.calls = 0
        # Drive's own listing can lag a moment behind a brand-new folder.
        self.new_folders_listed = True
        self._ids = itertools.count(1)

    def add(self, name, parent=None, mime_type=_FOLDER, listed=True):
        file_id = f"id{next(self._ids)}"
        self.files[file_id] = {"name": name, "mimeType": mime_type, "parent": parent, "listed": listed}
        return file_id

    def path(self, file_id):
        names = []
        while file_id is not None:
            names.append(self.files[file_id]["name"])
            file_id = self.files[file_id]["parent"]
        return list(reversed(names))

    # Stand-ins for the google_sheets_export functions report_folders calls.
    def list_folder(self, drive, folder_id):
        self.calls += 1
        return [
            {"id": file_id, "name": f["name"], "mimeType": f["mimeType"]}
            for file_id, f in self.files.items()
            if f["parent"] == folder_id and f["listed"]
        ]

    def get_file(self, drive, file_id):
        self.calls += 1
        f = self.files[file_id]
        result = {"id": file_id, "name": f["name"]}
        if f["parent"] is not None:
            result["parents"] = [f["parent"]]
        return result

    def create_folder(self, drive, parent_folder_id, name):
        self.calls += 1
        self.created.append((parent_folder_id, name))
        return self.add(name, parent=parent_folder_id, listed=self.new_folders_listed)

    def upload_bytes(self, drive, parent_folder_id, name, content, mime_type):
        self.calls += 1
        if name in self.failing_uploads:
            raise RuntimeError("upload failed")
        self.uploads.append((parent_folder_id, name, content, mime_type))
        return self.add(name, parent=parent_folder_id, mime_type=mime_type)


@pytest.fixture
def drive(monkeypatch):
    fake = FakeDrive()
    for name in ("list_folder", "get_file", "create_folder", "upload_bytes"):
        monkeypatch.setattr(report_folders, name, getattr(fake, name))
    return fake


def _org_tree(drive, tracking_visible=True):
    """Shared drive/Student Tracking/Answer Extractor/{Temporary Files,
    Testmastergrids}, as the org has it -- returns (tracking id, temp id)."""
    root = drive.add("Shared drive")
    tracking = drive.add("Student Tracking", parent=root)
    extractor = drive.add("Answer Extractor", parent=tracking if tracking_visible else None)
    drive.add("Testmastergrids", parent=extractor)
    temp = drive.add("Temporary Files", parent=extractor)
    return tracking, temp


@pytest.mark.parametrize(
    "date, names",
    [
        (dt.date(2026, 9, 12), ["Practice Tests 2026", "09 September", "12 September"]),
        (dt.date(2026, 9, 9), ["Practice Tests 2026", "09 September", "09 September"]),
        (dt.date(2027, 1, 5), ["Practice Tests 2027", "01 January", "05 January"]),
        (dt.date(2026, 12, 20), ["Practice Tests 2026", "12 December", "20 December"]),
    ],
)
def test_date_folder_names_zero_pad_the_month_and_day(date, names):
    assert date_folder_names(date) == names


def test_find_ancestor_folder_walks_up_past_answer_extractor_to_student_tracking(drive):
    tracking, temp = _org_tree(drive)
    assert find_ancestor_folder(object(), temp, "Student Tracking") == tracking


def test_find_ancestor_folder_explains_a_student_tracking_this_account_cant_see(drive):
    _, temp = _org_tree(drive, tracking_visible=False)
    with pytest.raises(ValueError, match=r"'Answer Extractor'.*access to 'Student Tracking' itself"):
        find_ancestor_folder(object(), temp, "Student Tracking")


def test_find_or_create_subfolder_reuses_a_folder_despite_case_spacing_or_leading_zeros(drive):
    month = drive.add("09 September")
    existing = drive.add("9  september", parent=month)
    assert find_or_create_subfolder(object(), month, "09 September") == existing
    assert drive.created == []


def test_find_or_create_subfolder_creates_a_missing_folder(drive):
    month = drive.add("09 September")
    drive.add("09 September", parent=month)
    new = find_or_create_subfolder(object(), month, "12 September")
    assert drive.created == [(month, "12 September")]
    assert drive.path(new) == ["09 September", "12 September"]


def test_find_or_create_subfolder_ignores_a_file_with_the_folders_name(drive):
    month = drive.add("09 September")
    drive.add("12 September", parent=month, mime_type=_SHEET)
    find_or_create_subfolder(object(), month, "12 September")
    assert drive.created == [(month, "12 September")]


def test_find_or_create_subfolder_prefers_the_exact_spelling_among_near_duplicates(drive):
    month = drive.add("09 September")
    drive.add("9 September", parent=month)
    exact = drive.add("09 September", parent=month)
    assert find_or_create_subfolder(object(), month, "09 September") == exact


def test_find_or_create_subfolder_raises_on_true_duplicates(drive):
    month = drive.add("09 September")
    drive.add("12 September", parent=month)
    drive.add("12 September", parent=month)
    with pytest.raises(ValueError, match="ambiguous"):
        find_or_create_subfolder(object(), month, "12 September")


def test_folder_for_makes_every_missing_level_under_student_tracking(drive):
    tracking, temp = _org_tree(drive)
    folders = ReportFolders(object(), fallback_folder_id=temp)

    day = folders.folder_for(dt.date(2027, 1, 5), "Jane Student")

    assert drive.path(day) == ["Shared drive", "Student Tracking", "Practice Tests 2027", "01 January", "05 January"]
    assert [name for _, name in drive.created] == ["Practice Tests 2027", "01 January", "05 January"]


def test_folder_for_reuses_the_year_and_month_folders_already_there(drive):
    tracking, temp = _org_tree(drive)
    year = drive.add("Practice Tests 2026", parent=tracking)
    month = drive.add("09 September", parent=year)
    existing_day = drive.add("09 September", parent=month)
    folders = ReportFolders(object(), fallback_folder_id=temp)

    assert folders.folder_for(dt.date(2026, 9, 9), "Jane Student") == existing_day
    new_day = folders.folder_for(dt.date(2026, 9, 12), "John Student")

    assert drive.created == [(month, "12 September")]
    assert drive.path(new_day)[-3:] == ["Practice Tests 2026", "09 September", "12 September"]


def test_folder_for_remembers_folders_it_made_before_drive_lists_them(drive):
    # Two students from one testing day: if the second looked its folders up
    # again before Drive's listing caught up, it would make duplicates.
    tracking, temp = _org_tree(drive)
    drive.new_folders_listed = False
    folders = ReportFolders(object(), fallback_folder_id=temp)

    first = folders.folder_for(dt.date(2027, 1, 5), "Jane Student")
    second = folders.folder_for(dt.date(2027, 1, 5), "John Student")
    next_day = folders.folder_for(dt.date(2027, 1, 6), "Jim Student")

    assert first == second
    assert [name for _, name in drive.created] == ["Practice Tests 2027", "01 January", "05 January", "06 January"]
    assert drive.path(next_day)[-2:] == ["01 January", "06 January"]


def test_folder_for_test_mode_uses_the_fallback_folder_without_touching_drive(drive):
    _, temp = _org_tree(drive)
    folders = ReportFolders(object(), fallback_folder_id=temp)

    assert folders.folder_for(None, "Jane Student") == temp
    assert drive.calls == 0


def test_folder_for_falls_back_to_temporary_files_with_a_warning_when_filing_fails(drive, capsys):
    _, temp = _org_tree(drive, tracking_visible=False)
    folders = ReportFolders(object(), fallback_folder_id=temp)

    assert folders.folder_for(dt.date(2026, 9, 12), "Jane Student") == temp

    warning = capsys.readouterr().err
    assert "Jane Student's Google Sheet" in warning
    assert "Temporary Files instead" in warning
    assert drive.created == []


def test_folder_for_uses_a_given_student_tracking_folder_without_searching(drive):
    tracking = drive.add("Student Tracking")
    folders = ReportFolders(object(), fallback_folder_id="NOT_IN_THIS_DRIVE", student_tracking_folder_id=tracking)

    day = folders.folder_for(dt.date(2026, 9, 12), "Jane Student")

    assert drive.path(day) == ["Student Tracking", "Practice Tests 2026", "09 September", "12 September"]



_REPORT_PDF = "Student, Jane 2027 ACT 25MC1 January 17 2026.pdf"


@pytest.mark.parametrize(
    "dropped, copy_name",
    [
        (
            "Student, Jane 2027 ACT 25MC1 January 17 2026 Test Scan & Bubble.pdf",
            "Student, Jane 2027 ACT 25MC1 January 17 2026 Test Scan & Bubble.pdf",
        ),
        ("Student, Jane 2027 ACT 25MC1 January 17 2026.png", "Student, Jane 2027 ACT 25MC1 January 17 2026.png"),
        # Named exactly like the report PDF -- the org names a report after its input.
        (_REPORT_PDF, "Student, Jane 2027 ACT 25MC1 January 17 2026 (original).pdf"),
    ],
)
def test_dropped_file_copy_name_keeps_its_own_name_unless_the_report_has_it_too(dropped, copy_name):
    assert dropped_file_copy_name(Path("/scans") / dropped, _REPORT_PDF) == copy_name


def test_save_copies_uploads_the_dropped_file_and_the_report_pdf_as_they_are(drive, tmp_path):
    scan = tmp_path / "Student, Jane 2027 ACT 25MC1 January 17 2026 Test Scan & Bubble.png"
    scan.write_bytes(b"scan bytes")
    folders = ReportFolders(object(), fallback_folder_id="TEMP")

    folders.save_copies("DAY", scan, _REPORT_PDF, b"%PDF-report", "Jane Student")

    assert drive.uploads == [
        ("DAY", scan.name, b"scan bytes", "image/png"),
        ("DAY", _REPORT_PDF, b"%PDF-report", "application/pdf"),
    ]


def test_save_copies_without_a_dropped_file_uploads_just_the_report(drive):
    ReportFolders(object(), fallback_folder_id="TEMP").save_copies("DAY", None, _REPORT_PDF, b"%PDF", "Jane Student")
    assert [name for _, name, _, _ in drive.uploads] == [_REPORT_PDF]


def test_save_copies_still_uploads_the_report_when_the_dropped_file_is_gone(drive, tmp_path, capsys):
    missing = tmp_path / "moved away mid-run.pdf"
    ReportFolders(object(), fallback_folder_id="TEMP").save_copies("DAY", missing, _REPORT_PDF, b"%PDF", "Jane Student")

    assert [name for _, name, _, _ in drive.uploads] == [_REPORT_PDF]
    assert "couldn't save a copy of 'moved away mid-run.pdf' to Jane Student's Drive folder" in capsys.readouterr().err


def test_save_copies_still_uploads_the_dropped_file_when_the_report_upload_fails(drive, tmp_path, capsys):
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(b"scan bytes")
    drive.failing_uploads.add(_REPORT_PDF)

    ReportFolders(object(), fallback_folder_id="TEMP").save_copies("DAY", scan, _REPORT_PDF, b"%PDF", "Jane Student")

    assert [name for _, name, _, _ in drive.uploads] == ["scan.pdf"]
    assert f"couldn't save a copy of {_REPORT_PDF!r}" in capsys.readouterr().err
