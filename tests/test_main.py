# =============================================================
# tests/test_main.py — basic tests for the Phase 0 entry point
#
# Run with: pytest
# =============================================================

import os
import sys

# Make sure Python can find src/ when running pytest from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import check_environment


def test_check_environment_missing_key(monkeypatch):
    """
    check_environment() should return False when ANTHROPIC_API_KEY is not set.
    monkeypatch temporarily removes the env var just for this test.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = check_environment()
    assert result is False


def test_check_environment_key_present(monkeypatch):
    """
    check_environment() should return True when ANTHROPIC_API_KEY is set.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")
    result = check_environment()
    assert result is True
