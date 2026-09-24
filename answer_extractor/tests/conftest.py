"""Setup shared by every test: none of them reads or writes this machine's
own kept template downloads (template_cache.py) or remembered test date
(gui_prompt.prompt_for_test_date), or sees a template-folder listing kept
from an earlier test (template_lookup.py)."""
import pytest

from answer_extractor import template_lookup


@pytest.fixture(autouse=True)
def _fresh_caches(tmp_path_factory, monkeypatch):
    # A folder of its own, not the test's tmp_path, so nothing shows up
    # among a test's own output files.
    cache = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv("ANSWER_EXTRACTOR_TEMPLATE_CACHE_DIR", str(cache / "templates"))
    monkeypatch.setenv("ANSWER_EXTRACTOR_LAST_TEST_DATE_FILE", str(cache / "last_test_date.json"))
    template_lookup.forget_listings()
