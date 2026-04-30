"""Sync .docx reports from the local portfolio folder into the repo.

Copies any Daily / Weekly / Quarterly / IPS .docx file from
``/Users/aaronhart/Desktop/Claude Portfolio/`` into
``wm-dashboard/data/reports/``. Run before ``git push`` to publish new
reports to the cloud dashboard.

Usage::

    python scripts/sync_reports.py            # incremental, copies new + modified
    python scripts/sync_reports.py --dry-run  # preview only
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wm_dashboard.reports_index import (  # noqa: E402
    DEFAULT_PORTFOLIO_DIR,
    REPO_REPORTS_DIR,
    _classify,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_PORTFOLIO_DIR)
    parser.add_argument("--dest", type=Path, default=REPO_REPORTS_DIR)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"source not found: {args.source}", file=sys.stderr)
        return 1
    args.dest.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    skipped: list[str] = []
    for child in args.source.iterdir():
        if not child.is_file() or child.suffix.lower() != ".docx":
            continue
        if child.name.startswith(("~", ".")):
            continue
        rep = _classify(child)
        if rep is None:
            skipped.append(f"unrecognized: {child.name}")
            continue
        target = args.dest / child.name
        if target.exists() and target.stat().st_mtime >= child.stat().st_mtime:
            continue
        if args.dry_run:
            copied.append(f"would copy: {child.name}")
        else:
            shutil.copy2(child, target)
            copied.append(f"copied: {child.name}")

    for line in copied:
        print(line)
    for line in skipped:
        print(line, file=sys.stderr)
    print(f"\n{len(copied)} copied, {len(skipped)} skipped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
