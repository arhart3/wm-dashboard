"""Export the current positions table from the xlsx to ``config/positions.yaml``.

Run this whenever the workbook changes so the committed yaml stays in sync.
The hosted dashboard reads the yaml when the xlsx is unavailable (e.g. on
Streamlit Cloud).

Usage::

    python scripts/export_positions.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wm_dashboard.tracker import load_portfolio  # noqa: E402


def main() -> int:
    targets = ROOT / "config" / "targets.yaml"
    out_path = ROOT / "config" / "positions.yaml"
    portfolio = load_portfolio(targets_path=targets)

    lines: list[str] = []
    lines.append(
        "# Snapshot of the positions table from the workbook. Used as a fallback"
    )
    lines.append("# when the source xlsx is unavailable (e.g. on Streamlit Cloud).")
    lines.append("#")
    lines.append("# Refresh by running:")
    lines.append("#   python scripts/export_positions.py")
    lines.append("#")
    lines.append(
        f"# Generated: {datetime.now():%Y-%m-%d %H:%M} from {portfolio.source_path.name}"
    )
    lines.append("")
    lines.append(f"asof_iso: {datetime.fromtimestamp(portfolio.source_path.stat().st_mtime).isoformat()}")
    lines.append("positions:")
    for pos in portfolio.positions:
        sec = (pos.sector or "").replace('"', "'")
        co = (pos.company or "").replace('"', "'")
        lines.append(f"  - ticker: {pos.ticker}")
        if co:
            lines.append(f'    company: "{co}"')
        if sec:
            lines.append(f'    sector: "{sec}"')
        lines.append(f"    current_weight_pct: {pos.current_weight_pct}")
        if pos.target_weight_pct is not None:
            lines.append(f"    target_weight_pct: {pos.target_weight_pct}")
        if pos.price is not None:
            lines.append(f"    price: {pos.price}")
        lines.append(f"    is_cash: {str(pos.is_cash).lower()}")
        lines.append(f"    is_etf: {str(pos.is_etf).lower()}")
    out_path.write_text("\n".join(lines) + "\n")
    print(f"wrote {out_path} ({len(portfolio.positions)} positions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
