"""Pre-market SMS digest of armed triggers.

Runs at 08:50 ET (12:50/13:50 UTC depending on DST) via the
``daily-summary.yml`` workflow. Reads ``config/triggers.yaml``, lists
ARMED triggers + their rule + action, and SMSes a one-message digest.

If Twilio env vars are missing, prints the digest to stdout and exits 0.

Usage::

    python scripts/daily_summary.py            # send via Twilio if configured
    python scripts/daily_summary.py --dry-run  # print only, never send
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wm_dashboard.notifications import (  # noqa: E402
    daily_digest_message,
    send_sms,
)

TRIGGERS_YAML = ROOT / "config" / "triggers.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--triggers", type=Path, default=TRIGGERS_YAML)
    parser.add_argument("--dry-run", action="store_true", help="Print only; never send.")
    parser.add_argument("--label", default="08:50 ET", help="Time label in the SMS body.")
    args = parser.parse_args(argv)

    with args.triggers.open() as fh:
        doc = yaml.safe_load(fh) or {}
    triggers = doc.get("triggers") or []
    today = date.today()

    armed: list[dict] = []
    for t in triggers:
        if t.get("status") != "ARMED":
            continue
        # Skip auto-expired in case the evaluator hasn't run yet today.
        exp = t.get("expires")
        if isinstance(exp, date) and exp < today:
            continue
        if isinstance(exp, str):
            try:
                if date.fromisoformat(exp) < today:
                    continue
            except ValueError:
                pass
        armed.append(t)

    body = daily_digest_message(armed, asof_label=args.label)
    print(body)
    if args.dry_run:
        return 0
    sent = send_sms(body)
    print(f"\n(sms sent: {sent})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
