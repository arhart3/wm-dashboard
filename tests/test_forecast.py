"""Tests for the LLM-driven forecast service.

The Anthropic client is mocked end-to-end. Tests run with no
``ANTHROPIC_API_KEY`` env, no network access, and no yfinance dependency
(the ``facts`` argument bypasses the gather step).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from wm_dashboard.forecast import (
    DEFAULT_MODEL,
    DISCLAIMER,
    FORECAST_TOOL,
    SYSTEM_PROMPT,
    ForecastError,
    ForecastFacts,
    ForecastUnavailable,
    _extract_tool_call,
    _validate_forecast,
    build_prompt,
    forecast,
    forecast_age,
)

# --- helpers ----------------------------------------------------------------


def _facts(ticker: str = "AAPL") -> ForecastFacts:
    return ForecastFacts(
        ticker=ticker,
        quote_price=180.0,
        quote_currency="USD",
        history_closes=[170.0, 172.0, 175.0, 178.0, 180.0],
        history_dates=["2026-04-25", "2026-04-26", "2026-04-29", "2026-04-30", "2026-05-01"],
        news_titles=["Apple beats Q2 earnings"],
        calendar_summary="",
    )


def _ok_payload() -> dict:
    return {
        "low":        -0.04,
        "base":       0.005,
        "high":       0.06,
        "catalysts":  ["A", "B", "C"],
        "action":     "HOLD",
        "confidence": "Medium",
        "premortem":  "Wrong if Q3 guide is cut by >5%.",
    }


def _mock_client_returning(payload: dict) -> MagicMock:
    """Build a mock anthropic.Anthropic that returns one tool_use block."""
    tool_block = SimpleNamespace(type="tool_use", name="submit_forecast", input=payload)
    response = SimpleNamespace(
        content=[tool_block],
        usage=SimpleNamespace(input_tokens=500, output_tokens=120, cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )
    client = MagicMock()
    client.messages.create.return_value = response
    return client


# --- schema invariants ------------------------------------------------------


def test_forecast_tool_schema_has_required_fields():
    schema = FORECAST_TOOL["input_schema"]
    assert set(schema["required"]) == {
        "low", "base", "high", "catalysts", "action", "confidence", "premortem"
    }
    assert schema["properties"]["catalysts"]["minItems"] == 3
    assert schema["properties"]["catalysts"]["maxItems"] == 3
    assert set(schema["properties"]["action"]["enum"]) == {"BUY", "HOLD", "TRIM"}
    assert set(schema["properties"]["confidence"]["enum"]) == {"Low", "Medium", "High"}


def test_system_prompt_mentions_falsifiability():
    """The 'wrong if ___' guidance is the most important rule — assert it stays."""
    assert "wrong if" in SYSTEM_PROMPT.lower()
    assert "falsifiable" in SYSTEM_PROMPT.lower()


# --- prompt assembly --------------------------------------------------------


def test_build_prompt_includes_quote_history_and_news():
    prompt = build_prompt(_facts())
    assert "AAPL" in prompt
    assert "180.00" in prompt
    assert "Apple beats Q2 earnings" in prompt
    assert "submit_forecast" in prompt


def test_build_prompt_handles_missing_data():
    facts = ForecastFacts(
        ticker="ZZZZ",
        quote_price=None,
        quote_currency="USD",
        history_closes=[],
        history_dates=[],
        news_titles=[],
        calendar_summary="",
    )
    prompt = build_prompt(facts)
    assert "unavailable" in prompt.lower()
    assert "ZZZZ" in prompt


# --- validation -------------------------------------------------------------


def test_validate_accepts_well_formed_payload():
    _validate_forecast(_ok_payload())  # does not raise


def test_validate_rejects_low_above_high():
    bad = _ok_payload()
    bad["low"], bad["high"] = 0.10, -0.05
    with pytest.raises(ForecastError, match="invariant"):
        _validate_forecast(bad)


def test_validate_rejects_wrong_catalyst_count():
    bad = _ok_payload()
    bad["catalysts"] = ["only one"]
    with pytest.raises(ForecastError, match="catalysts"):
        _validate_forecast(bad)


def test_validate_rejects_unknown_action():
    bad = _ok_payload()
    bad["action"] = "STRONG BUY"
    with pytest.raises(ForecastError, match="action"):
        _validate_forecast(bad)


def test_validate_rejects_missing_field():
    bad = _ok_payload()
    del bad["premortem"]
    with pytest.raises(ForecastError, match="missing"):
        _validate_forecast(bad)


# --- tool extraction --------------------------------------------------------


def test_extract_tool_call_finds_submit_forecast():
    blocks = [
        SimpleNamespace(type="text", text="hello"),
        SimpleNamespace(type="tool_use", name="submit_forecast", input={"x": 1}),
    ]
    assert _extract_tool_call(blocks) == {"x": 1}


def test_extract_tool_call_raises_when_missing():
    blocks = [SimpleNamespace(type="text", text="just talking")]
    with pytest.raises(ForecastError, match="did not call"):
        _extract_tool_call(blocks)


# --- end-to-end forecast() --------------------------------------------------


def test_forecast_unavailable_without_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ForecastUnavailable):
        forecast("AAPL", facts=_facts())


def test_forecast_returns_validated_result_via_mocked_client():
    client = _mock_client_returning(_ok_payload())
    result = forecast("AAPL", client=client, facts=_facts(), model="claude-haiku-4-5")
    assert result.ticker == "AAPL"
    assert result.action == "HOLD"
    assert len(result.catalysts) == 3
    assert result.disclaimer == DISCLAIMER
    assert result.model == "claude-haiku-4-5"
    assert result.raw_usage["input_tokens"] == 500


def test_forecast_uses_default_model_when_unspecified():
    client = _mock_client_returning(_ok_payload())
    result = forecast("AAPL", client=client, facts=_facts())
    assert result.model == DEFAULT_MODEL


def test_forecast_passes_tool_choice_forcing_submit_forecast():
    client = _mock_client_returning(_ok_payload())
    forecast("AAPL", client=client, facts=_facts())
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "submit_forecast"}
    assert call_kwargs["tools"][0]["name"] == "submit_forecast"


def test_forecast_includes_cache_control_on_system_prompt():
    """Forward-compat: kicks in once the system prompt grows past Haiku's 4096-token min."""
    client = _mock_client_returning(_ok_payload())
    forecast("AAPL", client=client, facts=_facts())
    system = client.messages.create.call_args.kwargs["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_forecast_propagates_api_error_as_forecast_error():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("anthropic 503")
    with pytest.raises(ForecastError, match="anthropic 503"):
        forecast("AAPL", client=client, facts=_facts())


def test_forecast_rejects_payload_violating_invariants():
    bad_payload = _ok_payload()
    bad_payload["low"], bad_payload["high"] = 0.5, -0.1
    client = _mock_client_returning(bad_payload)
    with pytest.raises(ForecastError, match="invariant"):
        forecast("AAPL", client=client, facts=_facts())


# --- helpers ----------------------------------------------------------------


def test_forecast_age_returns_zero_for_unparseable_timestamp():
    assert forecast_age("not-a-date").total_seconds() == 0
