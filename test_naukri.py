"""
Unit tests for Naukri self-healing automation.
Tests selector cache, PDF modification, and config loading.
Does NOT require a browser or Naukri credentials.
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Selector cache tests ──────────────────────────────────────────────────────

def test_load_selector_cache_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from naukri import load_selector_cache
    cache = load_selector_cache()
    assert isinstance(cache, dict)
    assert len(cache) == 0


def test_save_and_load_selector_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from naukri import save_selector_cache, load_selector_cache
    data = {"login_email": {"by": "ID", "value": "usernameField"}}
    save_selector_cache(data)
    loaded = load_selector_cache()
    assert loaded == data


def test_selector_cache_file_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from naukri import save_selector_cache
    save_selector_cache({"test_key": {"by": "CSS_SELECTOR", "value": ".test"}})
    assert (tmp_path / "selector_cache.json").exists()


# ── PDF modification tests ────────────────────────────────────────────────────

def test_update_resume_pdf_creates_file(tmp_path):
    """Test that PDF modification creates output file (requires a valid PDF)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas as rl_canvas
    from naukri import update_resume_pdf

    # Create a minimal valid PDF
    original = str(tmp_path / "original.pdf")
    modified = str(tmp_path / "modified.pdf")
    c = rl_canvas.Canvas(original, pagesize=letter)
    c.drawString(100, 700, "Test Resume Content")
    c.save()

    result = update_resume_pdf(original, modified)
    assert os.path.exists(result)
    assert os.path.getsize(result) > 0


def test_update_resume_pdf_fallback_on_missing(tmp_path):
    """When original PDF doesn't exist, should return original path gracefully."""
    from naukri import update_resume_pdf
    original = str(tmp_path / "nonexistent.pdf")
    modified = str(tmp_path / "modified.pdf")
    result = update_resume_pdf(original, modified)
    # Should return original path (absolute) without crashing
    assert "nonexistent" in result or result == os.path.abspath(original)


# ── Config / constants tests ──────────────────────────────────────────────────

def test_constants_env_override(monkeypatch):
    monkeypatch.setenv("NAUKRI_USERNAME", "test@example.com")
    monkeypatch.setenv("HEADLESS", "false")
    # Re-import constants with new env
    import importlib
    import constants
    importlib.reload(constants)
    assert constants.USERNAME == "test@example.com"
    assert constants.HEADLESS is False


def test_constants_defaults():
    import constants
    assert hasattr(constants, "NAUKRI_LOGIN_URL")
    assert "naukri.com" in constants.NAUKRI_LOGIN_URL
    assert hasattr(constants, "NAUKRI_PROFILE_URL")
    assert hasattr(constants, "UPDATE_PDF")
    assert hasattr(constants, "HEADLESS")


# ── Strategy chain tests ──────────────────────────────────────────────────────

def test_strategy_chains_defined():
    from naukri import STRATEGY_CHAINS
    required_keys = ["login_email", "login_password", "login_submit",
                     "resume_upload", "close_popup", "profile_updated_marker"]
    for key in required_keys:
        assert key in STRATEGY_CHAINS, f"Missing strategy chain for: {key}"
        assert len(STRATEGY_CHAINS[key]) >= 3, f"Too few fallbacks for: {key}"


def test_random_text_length():
    from naukri import random_text
    for length in [4, 8, 12, 16]:
        txt = random_text(length)
        assert len(txt) == length
        assert txt.isalnum()


# ── Log event tests ───────────────────────────────────────────────────────────

def test_log_event_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Patch JSON_LOG_FILE to use tmp_path
    import naukri
    original = naukri.JSON_LOG_FILE
    naukri.JSON_LOG_FILE = str(tmp_path / "test_events.jsonl")
    try:
        naukri.log_event("TEST_EVENT", "test detail", {"extra": "value"})
        lines = Path(naukri.JSON_LOG_FILE).read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "TEST_EVENT"
        assert record["detail"] == "test detail"
        assert record["extra"] == "value"
        assert "ts" in record
    finally:
        naukri.JSON_LOG_FILE = original
