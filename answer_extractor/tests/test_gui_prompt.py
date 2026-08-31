"""subprocess is mocked throughout -- no real dialog can pop up in a test
run (and osascript itself doesn't even exist off macOS). prompt_for_date
takes prompt_fn as a plain injected callable (the same shape every
pipeline caller uses it with), so its own tests use a bare MagicMock
rather than mocking subprocess -- osascript's own request-shaping is
prompt_for_text's concern, already covered above."""
import datetime as dt
from unittest.mock import MagicMock, patch

from answer_extractor.gui_prompt import prompt_for_date, prompt_for_text


def test_prompt_for_text_returns_what_was_typed():
    completed = MagicMock(returncode=0, stdout="button returned:OK, text returned:590\n")
    with patch("answer_extractor.gui_prompt.subprocess.run", return_value=completed) as run_mock:
        result = prompt_for_text("Reading & Writing score for Jane Student?", default_answer="600")

    assert result == "590"
    args = run_mock.call_args[0][0]
    assert args[0] == "osascript"
    script = args[2]
    assert "Reading & Writing score for Jane Student?" in script
    assert 'default answer "600"' in script


def test_prompt_for_text_returns_none_on_cancel():
    completed = MagicMock(returncode=1, stdout="")
    with patch("answer_extractor.gui_prompt.subprocess.run", return_value=completed):
        assert prompt_for_text("Anything?") is None


def test_prompt_for_text_returns_none_when_osascript_is_missing():
    with patch("answer_extractor.gui_prompt.subprocess.run", side_effect=FileNotFoundError):
        assert prompt_for_text("Anything?") is None


def test_prompt_for_text_escapes_quotes_and_backslashes_in_the_message():
    completed = MagicMock(returncode=0, stdout='button returned:OK, text returned:ok\n')
    with patch("answer_extractor.gui_prompt.subprocess.run", return_value=completed) as run_mock:
        prompt_for_text('Say "hi" \\ bye')

    script = run_mock.call_args[0][0][2]
    assert 'Say \\"hi\\" \\\\ bye' in script


def test_prompt_for_date_parses_a_valid_m_d_yyyy_answer():
    prompt_fn = MagicMock(return_value="3/8/2026")

    result = prompt_for_date(prompt_fn, "Jane Student's test date (M/D/YYYY)?")

    assert result == dt.date(2026, 3, 8)
    prompt_fn.assert_called_once_with("Jane Student's test date (M/D/YYYY)?", "")


def test_prompt_for_date_accepts_zero_padded_month_and_day_too():
    prompt_fn = MagicMock(return_value="03/08/2026")

    assert prompt_for_date(prompt_fn, "Test date?") == dt.date(2026, 3, 8)


def test_prompt_for_date_reprompts_on_a_malformed_answer_before_succeeding():
    prompt_fn = MagicMock(side_effect=["not a date", "13/40/2026", "3/8/2026"])

    result = prompt_for_date(prompt_fn, "Jane Student's test date (M/D/YYYY)?")

    assert result == dt.date(2026, 3, 8)
    assert prompt_fn.call_count == 3
    # Each retry's own message is built fresh from the original question,
    # not by prefixing the *previous* retry's own message -- confirmed by
    # the second retry's message still being a bounded length, not one
    # that grew from stacking both bad answers' own error text onto it.
    second_message = prompt_fn.call_args_list[2][0][0]
    assert second_message.count("isn't a date") == 1
    assert "Jane Student's test date (M/D/YYYY)?" in second_message


def test_prompt_for_date_returns_none_on_cancel():
    prompt_fn = MagicMock(return_value=None)

    assert prompt_for_date(prompt_fn, "Test date?") is None


def test_prompt_for_date_returns_none_immediately_without_retrying_a_cancel():
    """A cancelled dialog (or osascript missing off-macOS -- prompt_for_text's
    own None either way) should stop right away, not be treated as
    "malformed input" worth retrying."""
    prompt_fn = MagicMock(return_value=None)

    prompt_for_date(prompt_fn, "Test date?")

    assert prompt_fn.call_count == 1
