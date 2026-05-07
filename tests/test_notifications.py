"""Tests for the SMS notifications module.

The Twilio client is mocked end-to-end; tests run without a network
connection and without Twilio credentials in the environment.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from wm_dashboard.notifications import (
    daily_digest_message,
    fired_trigger_message,
    send_sms,
)


def test_send_sms_no_op_without_credentials(monkeypatch):
    """No env -> returns False, attempts no network call."""
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "WM_NOTIFY_PHONE", "AARON_PHONE"):
        monkeypatch.delenv(k, raising=False)
    assert send_sms("hello") is False


def test_send_sms_calls_twilio_when_configured(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM", "+15551234567")
    monkeypatch.setenv("WM_NOTIFY_PHONE", "+15559876543")
    fake_client = MagicMock()
    sent = send_sms("hello", client=fake_client)
    assert sent is True
    fake_client.messages.create.assert_called_once()
    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["to"] == "+15559876543"
    assert kwargs["from_"] == "+15551234567"
    assert kwargs["body"] == "hello"


def test_send_sms_returns_false_on_twilio_error(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM", "+15551234567")
    monkeypatch.setenv("WM_NOTIFY_PHONE", "+15559876543")
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("twilio is on fire")
    assert send_sms("hello", client=fake_client) is False


def test_fired_trigger_message_format():
    msg = fired_trigger_message(
        ticker="LLY",
        rule="Sustained close <= 920",
        action="ADD 4.0% -> 4.5%",
        confidence="MED",
        last_price=918.45,
    )
    assert "LLY" in msg
    assert "918.45" in msg
    assert "ADD 4.0% -> 4.5%" in msg
    assert "MED" in msg


def test_fired_trigger_message_handles_missing_price():
    msg = fired_trigger_message(
        ticker="LLY", rule="x", action="y", confidence="MED", last_price=None
    )
    assert "?" in msg


def test_daily_digest_no_armed():
    msg = daily_digest_message([])
    assert "0 armed" in msg


def test_daily_digest_lists_armed_one_per_line():
    triggers = [
        {"ticker": "LLY", "description": "close <= 920", "action": "ADD 4.0% -> 4.5%"},
        {"ticker": "WTI", "description": "close < 90", "action": "TRIM XOM/EOG"},
    ]
    msg = daily_digest_message(triggers, asof_label="08:50 ET")
    assert "08:50 ET" in msg
    assert "2 armed" in msg
    assert "LLY" in msg
    assert "WTI" in msg
    assert msg.count("\n") >= 2


def test_daily_digest_does_not_duplicate_action_inside_description():
    triggers = [{"ticker": "LLY", "description": "close <= 920 -> ADD 0.5%", "action": "ADD 0.5%"}]
    msg = daily_digest_message(triggers)
    # Description already contains the action -> no '→ ADD 0.5%' suffix.
    assert msg.count("ADD 0.5%") == 1
