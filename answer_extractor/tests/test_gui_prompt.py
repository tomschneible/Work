"""subprocess is mocked throughout -- no real dialog can pop up in a test
run (and osascript itself doesn't even exist off macOS)."""
from unittest.mock import MagicMock, patch

from answer_extractor.gui_prompt import prompt_for_text


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
