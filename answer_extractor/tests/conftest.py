"""Setup shared by every test: none of them reads or writes this machine's
own kept template downloads (template_cache.py), or sees a template-folder
listing kept from an earlier test (template_lookup.py)."""
import pytest

from answer_extractor import template_lookup


@pytest.fixture(autouse=True)
def _fresh_drive_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("ANSWER_EXTRACTOR_TEMPLATE_CACHE_DIR", str(tmp_path / "template-cache"))
    template_lookup.forget_listings()
