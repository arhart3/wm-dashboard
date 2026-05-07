"""Trigger evaluator.

Walks ARMED triggers in ``config/triggers.yaml``, compares the latest
quote (from ``data/prices.json``) against each rule, and flips status to
FIRED when the rule is satisfied. Triggers marked ``manual: true`` are
skipped (composite metrics like WTI / portfolio drawdown that aren't in
the price snapshot are evaluated by a human in the daily report).

Outputs (atomic — uses tmp + rename):
- ``config/triggers.yaml``: status updates with ``fired_at``, ``fired_at_price``
- ``data/trigger_history.yaml``: append-only audit row for each FIRED trigger

Crucially — and per IPS — it never executes trades. It only updates
status, so the operator notices in the dashboard and the next daily
report.

Usage::

    python scripts/evaluate_triggers.py            # interactive: prompt before applying
    python scripts/evaluate_triggers.py --apply    # apply changes silently
    python scripts/evaluate_triggers.py --inspect  # read-only diagnostic
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wm_dashboard.notifications import (  # noqa: E402
    fired_trigger_message,
    send_sms,
)

TRIGGERS_YAML = ROOT / "config" / "triggers.yaml"
PRICES_JSON = ROOT / "data" / "prices.json"
HISTORY_YAML = ROOT / "data" / "trigger_history.yaml"


@dataclass(frozen=True)
class Eval:
    """One trigger evaluation result."""

    id: str
    ticker: str
    operator: str
    value: float
    last_price: float | None
    fires: bool
    distance_pct: float | None  # signed % from threshold; -2.5 = 2.5% below
    reason: str


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=path.parent)
    try:
        import os
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except Exception:
        import os
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _evaluate_one(t: dict, prices: dict) -> Eval:
    op = t["operator"]
    val = float(t["value"])
    ticker = str(t.get("ticker", "")).upper()
    rec = prices.get(ticker)
    last = float(rec["price"]) if rec and rec.get("price") is not None else None
    if last is None:
        return Eval(t["id"], ticker, op, val, None, False, None, "no quote in snapshot")

    fires = (
        (op == "<=" and last <= val)
        or (op == "<" and last < val)
        or (op == ">=" and last >= val)
        or (op == ">" and last > val)
    )
    distance_pct = ((last - val) / val) * 100 if val != 0 else None
    if fires:
        reason = f"{ticker} {last:.2f} {op} {val:.2f}"
    else:
        reason = f"{ticker} {last:.2f}; threshold {op} {val:.2f} not satisfied"
    return Eval(t["id"], ticker, op, val, last, fires, distance_pct, reason)


def _is_expired(t: dict, today: date) -> bool:
    exp = t.get("expires")
    if isinstance(exp, date):
        return exp < today
    if isinstance(exp, str):
        try:
            return date.fromisoformat(exp) < today
        except ValueError:
            return False
    return False


def evaluate(triggers: list[dict], prices: dict, today: date) -> list[tuple[dict, Eval | None]]:
    """Return [(trigger, eval-or-None)] — None means trigger was skipped."""
    out: list[tuple[dict, Eval | None]] = []
    for t in triggers:
        if t.get("status") != "ARMED":
            out.append((t, None))
            continue
        if _is_expired(t, today):
            out.append((t, Eval(t["id"], t.get("ticker", ""), t.get("operator", ""), 0, None, False, None, "expired")))
            continue
        if t.get("manual"):
            out.append((t, Eval(t["id"], t.get("ticker", ""), t.get("operator", ""), 0, None, False, None, "manual — skip auto-eval")))
            continue
        out.append((t, _evaluate_one(t, prices)))
    return out


def _apply(
    triggers: list[dict],
    results: list[tuple[dict, Eval | None]],
    today: date,
    snapshot_asof: str,
    *,
    notify: bool = False,
) -> tuple[int, int, list[dict]]:
    """Mutate triggers in-place; return (fired_count, expired_count, history_rows).

    When ``notify`` is true and Twilio credentials are configured in the
    environment, sends one SMS per FIRED trigger. Notifications are
    best-effort: a Twilio failure does not abort the apply, but it is
    logged for diagnosis.
    """
    fired = 0
    expired = 0
    history_rows: list[dict] = []
    for t, ev in results:
        if ev is None:
            continue
        if ev.reason == "expired":
            t["status"] = "EXPIRED"
            expired += 1
            history_rows.append(
                {
                    "ticker": t.get("ticker"),
                    "trigger_id": t.get("id"),
                    "review_by": str(t.get("review_by") or t.get("expires") or today.isoformat()),
                    "closed_on": today.isoformat(),
                    "outcome": "EXPIRED",
                    "confidence_at_open": t.get("confidence", "—"),
                    "workbook_weight_at_close_pct": None,
                    "description": t.get("description", ""),
                    "notes": "Auto-expired by evaluator (past `expires` date).",
                }
            )
        elif ev.fires:
            t["status"] = "FIRED"
            t["fired_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            t["fired_at_price"] = ev.last_price
            t["fired_at_snapshot"] = snapshot_asof
            fired += 1
            history_rows.append(
                {
                    "ticker": t.get("ticker"),
                    "trigger_id": t.get("id"),
                    "review_by": str(t.get("review_by") or today.isoformat()),
                    "closed_on": today.isoformat(),
                    "outcome": "FIRED",
                    "confidence_at_open": t.get("confidence", "—"),
                    "workbook_weight_at_close_pct": None,
                    "description": t.get("description", ""),
                    "notes": (
                        f"Auto-fired by evaluator at {ev.last_price:.2f} "
                        f"(rule: {ev.operator} {ev.value:.2f}). "
                        f"Snapshot {snapshot_asof}."
                    ),
                }
            )
            if notify:
                send_sms(
                    fired_trigger_message(
                        ticker=str(t.get("ticker", "?")),
                        rule=str(t.get("description", "")),
                        action=str(t.get("action", "")),
                        confidence=str(t.get("confidence", "—")),
                        last_price=ev.last_price,
                    )
                )
    return fired, expired, history_rows


def _append_history(rows: list[dict]) -> None:
    if not rows:
        return
    if HISTORY_YAML.exists():
        with HISTORY_YAML.open() as fh:
            existing = yaml.safe_load(fh) or {}
    else:
        existing = {}
    history = existing.get("history") or []
    history.extend(rows)
    existing["history"] = history
    body = (
        "# Immutable trade-log of trigger lifecycle. Append-only — never edit existing rows.\n"
        + yaml.safe_dump(existing, sort_keys=False, default_flow_style=False, width=100)
    )
    _atomic_write(HISTORY_YAML, body)


def _write_triggers(triggers_doc: dict) -> None:
    body = (
        "# Active triggers — structured schema (v2). Edit cautiously; the\n"
        "# evaluator updates `status` / `fired_at` automatically.\n"
        + yaml.safe_dump(triggers_doc, sort_keys=False, default_flow_style=False, width=100)
    )
    _atomic_write(TRIGGERS_YAML, body)


def _print_eval(t: dict, ev: Eval | None) -> None:
    if ev is None:
        print(f"  {t.get('id'):24s} status={t.get('status')}  (skipped)")
        return
    if ev.fires:
        print(f"  [FIRES]   {ev.id:24s} {ev.reason}")
    elif ev.last_price is None:
        print(f"  [NO-DATA] {ev.id:24s} {ev.reason}")
    else:
        d = f"{ev.distance_pct:+.2f}%" if ev.distance_pct is not None else "—"
        print(f"  [armed]   {ev.id:24s} last {ev.last_price:.2f} ({d} from {ev.value:.2f})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--triggers", type=Path, default=TRIGGERS_YAML)
    parser.add_argument("--prices", type=Path, default=PRICES_JSON)
    parser.add_argument("--inspect", action="store_true", help="Read-only diagnostic.")
    parser.add_argument("--apply", action="store_true", help="Apply changes without prompting.")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send SMS via Twilio when a trigger fires (no-op without TWILIO_* env).",
    )
    args = parser.parse_args(argv)

    if not args.triggers.exists():
        print(f"triggers file missing: {args.triggers}", file=sys.stderr)
        return 1
    with args.triggers.open() as fh:
        doc = yaml.safe_load(fh) or {}
    triggers = doc.get("triggers") or []
    if not args.prices.exists():
        print(f"prices snapshot missing: {args.prices}", file=sys.stderr)
        return 1
    with args.prices.open() as fh:
        snap = json.load(fh)
    prices = snap.get("prices") or {}
    snapshot_asof = snap.get("generated_utc", "")

    today = date.today()
    results = evaluate(triggers, prices, today)
    armed = sum(1 for t, _ in results if t.get("status") == "ARMED")
    print(f"\n{armed} ARMED trigger(s) under snapshot {snapshot_asof}:\n")
    for t, ev in results:
        _print_eval(t, ev)

    if args.inspect:
        return 0

    fires = [(t, ev) for t, ev in results if ev and ev.fires]
    expirations = [(t, ev) for t, ev in results if ev and ev.reason == "expired"]
    if not fires and not expirations:
        print("\nNothing to apply.")
        return 0

    if not args.apply:
        try:
            answer = input(f"\nApply {len(fires)} fire(s) + {len(expirations)} expiration(s)? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\naborted")
            return 130
        if answer != "y":
            print("aborted")
            return 0

    fired_count, expired_count, history_rows = _apply(
        triggers, results, today, snapshot_asof, notify=args.notify
    )
    doc["triggers"] = triggers
    _write_triggers(doc)
    _append_history(history_rows)
    print(f"\nApplied: {fired_count} FIRED, {expired_count} EXPIRED. History updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
