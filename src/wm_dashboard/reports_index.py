"""List Daily/Weekly/Quarterly report .docx files in the portfolio folder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DEFAULT_PORTFOLIO_DIR = Path("/Users/aaronhart/Desktop/Claude Portfolio")
# Reports synced into the repo are served from here. ``scripts/sync_reports.py``
# refreshes this directory; the dashboard reads from it on the cloud.
REPO_REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/arhart3/wm-dashboard/main/data/reports"
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
    in_repo: bool = False  # True when sourced from data/reports/ in the repo

    @property
    def file_url(self) -> str:
        """URL the dashboard hyperlinks to.

        - On the cloud-synced copy, return the GitHub raw URL — clicks open the
          file from anywhere (browser downloads as .docx).
        - On a local-only file, return ``file://`` so clicks open it natively
          in Word.
        """
        if self.in_repo:
            return f"{GITHUB_RAW_BASE}/{self.path.name}"
        return self.path.as_uri()

    @property
    def modified(self) -> datetime:
        return datetime.fromtimestamp(self.path.stat().st_mtime)


def _classify(p: Path, *, in_repo: bool = False) -> Report | None:
    name = p.name
    for kind, patterns in PATTERNS.items():
        for pattern in patterns:
            m = pattern.match(name)
            if not m:
                continue
            if kind == "Quarterly":
                year, q = int(m.group(1)), int(m.group(2))
                return Report(kind=kind, path=p, label=f"Q{q} {year}", sort_key=(year, q), in_repo=in_repo)
            if kind == "IPS":
                return Report(kind=kind, path=p, label=f"IPS v{m.group(1)}", sort_key=(m.group(1),), in_repo=in_repo)
            try:
                if len(m.groups()) == 1:
                    d = date.fromisoformat(m.group(1))
                else:
                    month = _MONTH_LOOKUP[m.group(1).title()]
                    d = date(int(m.group(3)), month, int(m.group(2)))
            except (ValueError, KeyError):
                continue
            return Report(kind=kind, path=p, label=d.isoformat(), sort_key=(d,), in_repo=in_repo)
    return None


def list_reports(
    portfolio_dir: Path = DEFAULT_PORTFOLIO_DIR,
    *,
    repo_dir: Path = REPO_REPORTS_DIR,
    kinds: tuple[str, ...] = ("Daily", "Weekly", "Quarterly", "IPS"),
) -> list[Report]:
    """Return reports newest-first within kind.

    Reads from both the local ``portfolio_dir`` (your Mac's Desktop folder) and
    the in-repo ``data/reports/`` directory. When the same filename appears in
    both, the local copy wins (it's the freshest source). On the cloud the
    Desktop folder doesn't exist, so only the repo copies show.
    """
    out: list[Report] = []
    seen: set[str] = set()
    # Local first so it takes precedence on duplicates.
    for source_dir, in_repo in ((portfolio_dir, False), (repo_dir, True)):
        if not source_dir.exists():
            continue
        for child in source_dir.iterdir():
            if not child.is_file() or child.suffix.lower() != ".docx":
                continue
            if child.name.startswith(("~", ".")):
                continue
            if child.name in seen:
                continue
            rep = _classify(child, in_repo=in_repo)
            if rep is None or rep.kind not in kinds:
                continue
            seen.add(child.name)
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
