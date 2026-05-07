"""SMS notifications via Twilio.

Two entry points:

* :func:`send_sms` — single-message send. Used by ``evaluate_triggers.py``
  to alert when a rule fires, and by ``daily_summary.py`` for the
  pre-market digest.
* :func:`fired_trigger_message` / :func:`daily_digest_message` — message
  formatters that produce SMS-shaped strings (≤ 1600 chars, single body).

Both formatters are pure (no side effects), so they're easy to unit-test.

The Twilio call gracefully **no-ops** when credentials are missing:
``send_sms`` returns ``False`` and logs a debug line. This keeps the
local dev path frictionless and makes Twilio strictly opt-in. To enable
SMS in the hosted deployment, set these GitHub Actions secrets:

* ``TWILIO_ACCOUNT_SID``
* ``TWILIO_AUTH_TOKEN``
* ``TWILIO_FROM`` — your Twilio phone number, E.164 (e.g. ``+15551234567``)
* ``WM_NOTIFY_PHONE`` — your destination phone, E.164

Then expose them as env vars in the workflow ``env:`` block.
"""

from __future__ import annotations

import logging
import os
from typing import Any

LOG = logging.getLogger(__name__)


def _twilio_config() -> tuple[str, str, str, str] | None:
    """Return (sid, token, from_number, to_number) or None if any are missing."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM")
    to_number = os.environ.get("WM_NOTIFY_PHONE") or os.environ.get("AARON_PHONE")
    if not (sid and token and from_number and to_number):
        return None
    return sid, token, from_number, to_number


def send_sms(body: str, *, client: Any | None = None) -> bool:
    """Send ``body`` as an SMS via Twilio.

    Returns ``True`` if the send was attempted (even if Twilio later
    returned a delivery error — those land asynchronously in Twilio's
    logs), ``False`` if the send was skipped because credentials are
    missing or the import failed.

    The optional ``client`` argument is for tests: pass any object with a
    ``messages.create(to=, from_=, body=)`` method. In production we
    instantiate a real Twilio client from env vars.
    """
    cfg = _twilio_config()
    if cfg is None:
        LOG.info("Twilio credentials missing; SMS no-op. body=%s", body[:80])
        return False
    sid, token, from_number, to_number = cfg
    try:
        if client is None:
            from twilio.rest import Client
            client = Client(sid, token)
        client.messages.create(to=to_number, from_=from_number, body=body)
        LOG.info("Sent SMS to %s (%d chars).", to_number, len(body))
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Twilio send failed: %s", exc)
        return False


def fired_trigger_message(
    *,
    ticker: str,
    rule: str,
    action: str,
    confidence: str,
    last_price: float | None,
) -> str:
    """One-line SMS body for a fired trigger."""
    px = f"{last_price:.2f}" if last_price is not None else "?"
    return f"[WM Growth] {ticker} TRIGGER FIRED at {px}. Rule: {rule}. Action: {action}. Conf: {confidence}."


def daily_digest_message(armed_triggers: list[dict], *, asof_label: str = "08:50 ET") -> str:
    """Multi-line SMS body listing ARMED triggers for the pre-market digest."""
    if not armed_triggers:
        return f"[WM Growth] Pre-market {asof_label}. 0 armed triggers."
    lines = []
    for t in armed_triggers:
        ticker = t.get("ticker", "?")
        rule = t.get("description") or t.get("action") or ""
        action = t.get("action") or ""
        # If description already contains the action, don't duplicate.
        if action and action not in rule:
            line = f"• {ticker} {rule} → {action}"
        else:
            line = f"• {ticker} {rule}"
        lines.append(line)
    header = f"[WM Growth] Pre-market {asof_label}. {len(armed_triggers)} armed:"
    return header + "\n" + "\n".join(lines)
