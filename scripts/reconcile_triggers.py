"""Trigger reconciliation — the professional PM workflow.

Past-due triggers (review_by < today) are not just visual flags; they
represent unresolved decisions that an actual portfolio manager would
clear at the start of every session. This script:

1. Reads ``config/triggers.yaml`` and finds triggers whose ``review_by``
   has passed.
2. For each one, compares the trigger's expected outcome against the
   current workbook position to *suggest* a likely resolution.
3. In ``--inspect`` mode, prints the analysis and exits — read-only.
4. In default (interactive) mode, prompts the operator for a verdict on
   each past-due trigger:
      [r] resolved (trade executed / outcome achieved)
      [c] cancelled (conditions not met, no trade)
      [v] reversed (trade executed then reversed within the window)
      [x] expired (no action taken; revisit later at a new date)
      [e] extended (push review_by forward; keep trigger active)
      [s] skip (don't change anything this session)
5. Appends a row to ``data/trigger_history.yaml`` for every closed
   trigger (anything that isn't ``skip`` or ``extended``); removes
   the closed trigger from ``triggers.yaml``.

It NEVER modifies the workbook or executes a trade. The IPS treats the
dashboard as a checker, not a broker — humans make trade decisions and
update the workbook themselves.

Usage::

    python scripts/reconcile_triggers.py --inspect   # read-only diagnostic
    python scripts/reconcile_triggers.py             # interactive
    python scripts/reconcile_triggers.py --apply '{"INTC":"r","UNH":"v"}' \\
        --notes '{"INTC":"Q1 beat; starter executed at 1.5%."}'
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wm_dashboard.tracker import load_portfolio  # noqa: E402

TRIGGERS_YAML = ROOT / "config" / "triggers.yaml"
HISTORY_YAML = ROOT / "data" / "trigger_history.yaml"

Outcome = Literal["EXECUTED", "REVERSED", "CANCELLED", "EXPIRED", "EXTENDED"]
VERDICT_MAP: dict[str, Outcome | None] = {
    "r": "EXECUTED",
    "v": "REVERSED",
    "c": "CANCELLED",
    "x": "EXPIRED",
    "e": "EXTENDED",
    "s": None,
}


@dataclass(frozen=True)
class PastDueAnalysis:
    ticker: str
    description: str
    confidence: str
    review_by: str
    days_overdue: int
    workbook_weight_pct: float | None
    suggested_outcome: Outcome
    rationale: str


def _today() -> date:
    return date.today()


def _parse_review_by(raw: object) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _suggest(ticker: str, description: str, weight: float | None) -> tuple[Outcome, str]:
    desc = description.lower()
    if weight is None:
        return "EXPIRED", "Ticker not in workbook; cannot infer state."
    arrow = re.search(r"(\d+\.?\d*)\s*->\s*(\d+\.?\d*)\s*%", description)
    if arrow:
        before = float(arrow.group(1))
        after = float(arrow.group(2))
        if abs(weight - after) < 0.05:
            return "EXECUTED", f"Workbook weight {weight:.2f}% matches post-trigger target {after:.2f}%."
        if abs(weight - before) < 0.05:
            return "CANCELLED", (
                f"Workbook weight {weight:.2f}% still at pre-trigger level {before:.2f}%; "
                f"trade did not execute."
            )
    if "starter" in desc or "entry" in desc:
        if weight > 0:
            return "EXECUTED", f"Workbook shows position open at {weight:.2f}% — entry filled."
        return "CANCELLED", "Workbook shows no position; entry conditions likely not met."
    return "EXPIRED", "No clear pre/post weights in description; manual review."


def find_past_due(triggers_path: Path = TRIGGERS_YAML) -> list[PastDueAnalysis]:
    if not triggers_path.exists():
        return []
    with triggers_path.open() as fh:
        raw = yaml.safe_load(fh) or {}
    portfolio = load_portfolio()
    today = _today()
    out: list[PastDueAnalysis] = []
    for t in raw.get("triggers") or []:
        rb = _parse_review_by(t.get("review_by"))
        if rb is None or rb >= today:
            continue
        ticker = str(t.get("ticker") or "?").upper()
        if "/" in ticker:
            legs = [portfolio.by_ticker(s) for s in ticker.split("/")]
            weight: float | None = sum((p.current_weight_pct for p in legs if p), 0.0) if any(legs) else None
        else:
            position = portfolio.by_ticker(ticker)
            weight = position.current_weight_pct if position else None
        suggestion, rationale = _suggest(ticker, str(t.get("description", "")), weight)
        out.append(
            PastDueAnalysis(
                ticker=ticker,
                description=str(t.get("description", "")),
                confidence=str(t.get("confidence", "—")),
                review_by=rb.isoformat(),
                days_overdue=(today - rb).days,
                workbook_weight_pct=weight,
                suggested_outcome=suggestion,
                rationale=rationale,
            )
        )
    return out


def _print_analysis(a: PastDueAnalysis) -> None:
    print(f"  {a.ticker}  ({a.confidence}, review by {a.review_by}, {a.days_overdue}d overdue)")
    print(f"    description: {a.description}")
    if a.workbook_weight_pct is not None:
        print(f"    workbook:    {a.workbook_weight_pct:.2f}%")
    else:
        print("    workbook:    not present")
    print(f"    suggested:   {a.suggested_outcome}  --  {a.rationale}")


def _append_history(rows: list[dict]) -> None:
    if not rows:
        return
    HISTORY_YAML.parent.mkdir(parents=True, exist_ok=True)
    if HISTORY_YAML.exists():
        with HISTORY_YAML.open() as fh:
            existing = yaml.safe_load(fh) or {}
    else:
        existing = {}
    history = existing.get("history") or []
    history.extend(rows)
    existing["history"] = history
    with HISTORY_YAML.open("w") as fh:
        fh.write(
            "# Immutable trade-log of trigger lifecycle. Append-only — never "
            "edit existing rows.\n"
        )
        yaml.safe_dump(existing, fh, sort_keys=False, default_flow_style=False, width=100)


def _remove_from_triggers(tickers_to_remove: set[str]) -> int:
    if not tickers_to_remove:
        return 0
    with TRIGGERS_YAML.open() as fh:
        raw = yaml.safe_load(fh) or {}
    keep = [t for t in (raw.get("triggers") or []) if str(t.get("ticker", "")).upper() not in tickers_to_remove]
    raw["triggers"] = keep
    with TRIGGERS_YAML.open("w") as fh:
        fh.write(
            "# Active triggers — edit in place. Mirrors .memory/open_items.md but is the\n"
            "# operational source for the dashboard.\n"
        )
        yaml.safe_dump(raw, fh, sort_keys=False, default_flow_style=False, width=100)
    return len(tickers_to_remove)


def apply_verdicts(
    verdicts: dict[str, str],
    notes: dict[str, str] | None = None,
) -> tuple[int, int]:
    notes = notes or {}
    analyses = {a.ticker: a for a in find_past_due()}
    closed_rows: list[dict] = []
    closed_tickers: set[str] = set()
    extended = 0
    for ticker, code in verdicts.items():
        ticker = ticker.upper()
        outcome = VERDICT_MAP.get(code.lower())
        if outcome is None:
            continue
        a = analyses.get(ticker)
        if a is None:
            print(f"warn: {ticker} not in past-due list; skipping", file=sys.stderr)
            continue
        if outcome == "EXTENDED":
            extended += 1
            continue
        closed_rows.append(
            {
                "ticker": ticker,
                "review_by": a.review_by,
                "closed_on": _today().isoformat(),
                "outcome": outcome,
                "confidence_at_open": a.confidence,
                "workbook_weight_at_close_pct": a.workbook_weight_pct,
                "description": a.description,
                "notes": notes.get(ticker, ""),
            }
        )
        closed_tickers.add(ticker)
    _append_history(closed_rows)
    _remove_from_triggers(closed_tickers)
    return len(closed_rows), extended


def _interactive_resolve(analyses: list[PastDueAnalysis]) -> tuple[int, int]:
    if not analyses:
        return 0, 0
    print("\nResolve each past-due trigger:")
    print("  [r] resolved (trade executed)    [c] cancelled (no trade)")
    print("  [v] reversed (executed then reversed)")
    print("  [x] expired (no action; revisit) [e] extended (push review_by)")
    print("  [s] skip (don't change anything)\n")
    closed_rows: list[dict] = []
    closed_tickers: set[str] = set()
    extended = 0
    for a in analyses:
        _print_analysis(a)
        prompt = f"  verdict for {a.ticker} [r/c/v/x/e/s] (suggested: {a.suggested_outcome[0].lower()}): "
        choice = input(prompt).strip().lower() or a.suggested_outcome[0].lower()
        outcome = VERDICT_MAP.get(choice)
        if outcome is None:
            print(f"  -> skipped {a.ticker}\n")
            continue
        if outcome == "EXTENDED":
            extended += 1
            print(f"  -> {a.ticker} marked EXTENDED (review_by edit by hand)\n")
            continue
        note = input(f"  one-line rationale for {outcome}: ").strip()
        closed_rows.append(
            {
                "ticker": a.ticker,
                "review_by": a.review_by,
                "closed_on": _today().isoformat(),
                "outcome": outcome,
                "confidence_at_open": a.confidence,
                "workbook_weight_at_close_pct": a.workbook_weight_pct,
                "description": a.description,
                "notes": note,
            }
        )
        closed_tickers.add(a.ticker)
        print(f"  -> {a.ticker} {outcome}\n")
    _append_history(closed_rows)
    _remove_from_triggers(closed_tickers)
    return len(closed_rows), extended


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inspect", action="store_true", help="Read-only diagnostic.")
    parser.add_argument("--apply", type=str, help='JSON dict {ticker: verdict_letter}.')
    parser.add_argument("--notes", type=str, help='JSON dict {ticker: note_string}.')
    args = parser.parse_args(argv)

    analyses = find_past_due()
    if not analyses:
        print("No past-due triggers. Nothing to reconcile.")
        return 0

    print(f"\n{len(analyses)} past-due trigger(s) found:\n")
    for a in analyses:
        _print_analysis(a)
        print()

    if args.inspect:
        return 0

    if args.apply:
        verdicts = json.loads(args.apply)
        notes = json.loads(args.notes) if args.notes else {}
        closed, extended = apply_verdicts(verdicts, notes)
    else:
        try:
            closed, extended = _interactive_resolve(analyses)
        except (EOFError, KeyboardInterrupt):
            print("\naborted; no changes made", file=sys.stderr)
            return 130

    print(f"\nclosed {closed} trigger(s), extended {extended}.")
    if closed > 0:
        print("Run scripts/publish.sh to push the updated triggers.yaml + history to the cloud.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
