# =============================================================
# tests/test_llm.py — tests for the LLM client (cost, cache, limits)
#
# These tests never call the real API. They use a small fake
# client that pretends to be Anthropic and counts its calls.
# =============================================================

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.generation.llm import CallLimitReached, LLMClient, calculate_cost, make_cache_key

PRICES = {"test-model": {"input_per_million": 1.0, "output_per_million": 5.0}}


class FakeMessages:
    """Pretends to be client.messages, returning a fixed answer and counting calls."""

    def __init__(self):
        """Start with zero calls."""
        self.call_count = 0

    def create(self, **request):
        """Return a fake response shaped like the real Anthropic response."""
        self.call_count += 1
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text="fake answer")],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                                  cache_creation_input_tokens=0,
                                  cache_read_input_tokens=0),
        )


class FakeClient:
    """Pretends to be anthropic.Anthropic()."""

    def __init__(self):
        """Attach the fake messages object."""
        self.messages = FakeMessages()


@pytest.fixture
def in_temp_folder(tmp_path, monkeypatch):
    """Run the test inside an empty temporary folder so cache files don't leak."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_client(**options):
    """Create an LLMClient using the test price list."""
    client = LLMClient(**options)
    client.pricing = PRICES
    return client


def test_calculate_cost_simple():
    """1M input tokens at $1/M plus 1M output tokens at $5/M is $6."""
    assert calculate_cost(PRICES, "test-model", 1_000_000, 1_000_000) == pytest.approx(6.0)


def test_calculate_cost_cache_tokens():
    """Cache writes cost 1.25x input price; cache reads cost 0.1x."""
    cost = calculate_cost(PRICES, "test-model", 0, 0,
                          cache_write_tokens=1_000_000, cache_read_tokens=1_000_000)
    assert cost == pytest.approx(1.25 + 0.10)


def test_calculate_cost_unknown_model_is_zero():
    """A model missing from the price list is logged as $0 instead of crashing."""
    assert calculate_cost(PRICES, "mystery-model", 500, 500) == 0.0


def test_cache_key_changes_with_input():
    """Different requests must get different fingerprints."""
    key_a = make_cache_key("m", "sys", "hello", 100, None)
    key_b = make_cache_key("m", "sys", "goodbye", 100, None)
    assert key_a != key_b
    assert key_a == make_cache_key("m", "sys", "hello", 100, None)


def test_real_call_tracks_cost(in_temp_folder):
    """A (fake) real call returns the text and adds up its cost."""
    llm = make_client(api_client=FakeClient())
    answer = llm.ask("test-model", "sys", "question", 500, purpose="test")
    assert answer == "fake answer"
    assert llm.calls_made == 1
    # 1000 in x $1/M + 200 out x $5/M = $0.001 + $0.001
    assert llm.total_cost == pytest.approx(0.002)


def test_second_identical_call_uses_cache(in_temp_folder):
    """Asking the same thing twice should only pay once."""
    fake = FakeClient()
    llm = make_client(api_client=fake)
    llm.ask("test-model", "sys", "question", 500, purpose="test")
    llm.ask("test-model", "sys", "question", 500, purpose="test")
    assert fake.messages.call_count == 1
    assert llm.cache_hits == 1


def test_dry_run_makes_no_api_call(in_temp_folder):
    """--dry-run returns a placeholder and never touches the API."""
    fake = FakeClient()
    llm = make_client(dry_run=True, api_client=fake)
    answer = llm.ask("test-model", "sys", "question", 500, purpose="test")
    assert "DRY RUN" in answer
    assert fake.messages.call_count == 0
    assert llm.total_cost > 0   # it still estimates what it WOULD cost


def test_call_limit_stops_run(in_temp_folder):
    """Going over max_calls raises CallLimitReached."""
    llm = make_client(max_calls=1, api_client=FakeClient())
    llm.ask("test-model", "sys", "first", 500, purpose="test")
    with pytest.raises(CallLimitReached):
        llm.ask("test-model", "sys", "second", 500, purpose="test")


def test_cost_log_is_written(in_temp_folder):
    """write_cost_log creates output/cost_log.csv with a header and one row."""
    llm = make_client(dry_run=True)
    llm.write_cost_log("news", note="test run")
    with open(os.path.join("output", "cost_log.csv")) as f:
        lines = f.read().strip().splitlines()
    assert lines[0].startswith("timestamp,channel")
    assert "test run" in lines[1]
