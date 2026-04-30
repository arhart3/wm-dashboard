"""List Daily/Weekly/Quarterly report .docx files in the portfolio folder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DEFAULT_PORTFOLIO_DIR = Path("/Users/aaronhart/Desktop/Claude Portfolio")
# Multiple patterns per kind so we catch the modern ISO-date convention plus
# legacy filenames (e.g. DailyReport_Apr17_2026.docx pre-dates the rename).
_MONTH = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "Daily": [
        re.compile(r"^(?:Daily|DailyReport)[_-](\d{4}-\d{2}-\d{2})\.docx$", re.IGNORECASE),
        re.compile(rf"^(?:Daily|DailyReport)[_-]({_MONTH})(\d{{1,2}})[_-](\d{{4}})\.docx$", re.IGNORECASE),
    ],
    "Weekly": [
        re.compile(r"^(?:Weekly|WeeklyReport)[_-](\d{4}-\d{2}-\d{2})\.docx$", re.IGNORECASE),
    ],
    "Quarterly": [
        re.compile(r"^Quarterly[_-](\d{4})-Q(\d)\.docx$", re.IGNORECASE),
    ],
    "IPS": [
        re.compile(r"^.*IPS_v(\d+\.\d+)\.docx$", re.IGNORECASE),
    ],
}

_MONTH_LOOKUP = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
)}


@dataclass(frozen=True)
class Report:
    kind: str  # "Daily" | "Weekly" | "Quarterly" | "IPS"
    path: Path
    label: str
    sort_key: tuple

    @property
    def file_url(self) -> str:
        return self.path.as_uri()

    @property
    def modified(self) -> datetime:
        return datetime.fromtimestamp(self.path.stat().st_mtime)


def _classify(p: Path) -> Report | None:
    name = p.name
    for kind, patterns in PATTERNS.items():
        for pattern in patterns:
            m = pattern.match(name)
            if not m:
                continue
            if kind == "Quarterly":
                year, q = int(m.group(1)), int(m.group(2))
                return Report(kind=kind, path=p, label=f"Q{q} {year}", sort_key=(year, q))
            if kind == "IPS":
                return Report(kind=kind, path=p, label=f"IPS v{m.group(1)}", sort_key=(m.group(1),))
            # Daily / Weekly: try ISO first, then "Apr17_2026" legacy form.
            try:
                if len(m.groups()) == 1:
                    d = date.fromisoformat(m.group(1))
                else:
                    month = _MONTH_LOOKUP[m.group(1).title()]
                    d = date(int(m.group(3)), month, int(m.group(2)))
            except (ValueError, KeyError):
                continue
            return Report(kind=kind, path=p, label=d.isoformat(), sort_key=(d,))
    return None


def list_reports(
    portfolio_dir: Path = DEFAULT_PORTFOLIO_DIR,
    *,
    kinds: tuple[str, ...] = ("Daily", "Weekly", "Quarterly", "IPS"),
) -> list[Report]:
    """Return reports under ``portfolio_dir``, newest first within each kind."""
    if not portfolio_dir.exists():
        return []
    out: list[Report] = []
    for child in portfolio_dir.iterdir():
        if not child.is_file() or child.suffix.lower() != ".docx":
            continue
        if child.name.startswith(("~", ".")):
            continue
        rep = _classify(child)
        if rep is None or rep.kind not in kinds:
            continue
        out.append(rep)
    # Sort each kind on its own most-meaningful key (date for Daily/Weekly,
    # year+quarter for Quarterly, version for IPS), newest first within kind,
    # and order kinds in their conventional reading order. Sorting per-kind
    # avoids the heterogeneous-key TypeError when comparing a date to a
    # version string across kinds.
    kind_order = {"Daily": 0, "Weekly": 1, "Quarterly": 2, "IPS": 3}
    by_kind: dict[str, list[Report]] = {}
    for r in out:
        by_kind.setdefault(r.kind, []).append(r)
    sorted_out: list[Report] = []
    for kind in sorted(by_kind, key=lambda k: kind_order.get(k, 99)):
        sorted_out.extend(sorted(by_kind[kind], key=lambda r: r.sort_key, reverse=True))
    return sorted_out
