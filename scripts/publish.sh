#!/usr/bin/env bash
# Publish the dashboard: sync reports + positions, commit, push.
#
# Run after generating a new daily/weekly/quarterly .docx report so it appears
# on the cloud dashboard. Idempotent — safe to run repeatedly; if there are
# no changes, exits without committing.
#
# Usage:
#   ./scripts/publish.sh                # run from repo root
#   ./scripts/publish.sh --dry-run      # preview only

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_PY="$REPO_ROOT/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "venv python not found at $VENV_PY" >&2
  echo "Run 'python3 -m venv .venv && .venv/bin/pip install -e .[dev,fetch]' first." >&2
  exit 1
fi

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

echo "==> Syncing reports from local Desktop to data/reports/"
if [[ $DRY_RUN -eq 1 ]]; then
  "$VENV_PY" scripts/sync_reports.py --dry-run
else
  "$VENV_PY" scripts/sync_reports.py
fi

echo "==> Refreshing positions.yaml from the workbook (if available)"
if [[ -f "/Users/aaronhart/Desktop/Claude Portfolio/WM_Growth_Portfolio_Apr2026.xlsx" ]]; then
  if [[ $DRY_RUN -eq 0 ]]; then
    "$VENV_PY" scripts/export_positions.py
  else
    echo "(dry-run: would run export_positions.py)"
  fi
else
  echo "(workbook not found - skipping positions refresh)"
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "==> Dry run complete. No commit/push."
  exit 0
fi

echo "==> Checking for changes"
git add data/reports/ config/positions.yaml 2>/dev/null || true

if git diff --cached --quiet; then
  echo "No changes to publish."
  exit 0
fi

CHANGED_REPORTS=$(git diff --cached --name-only -- data/reports/ | wc -l | tr -d ' ')
POSITIONS_CHANGED=$(git diff --cached --name-only -- config/positions.yaml | wc -l | tr -d ' ')
SUMMARY=""
if [[ $CHANGED_REPORTS -gt 0 ]]; then
  SUMMARY="${CHANGED_REPORTS} report(s)"
fi
if [[ $POSITIONS_CHANGED -gt 0 ]]; then
  SUMMARY="${SUMMARY:+$SUMMARY + }positions"
fi
TS=$(date -u +"%Y-%m-%dT%H:%MZ")

echo "==> Committing: publish ${SUMMARY} (${TS})"
git commit -m "publish: ${SUMMARY} (${TS})" >/dev/null

echo "==> Pushing to origin/main (race-resilient: rebase-and-retry up to 3x)"
for attempt in 1 2 3; do
  if git push origin main 2>&1 | tail -3; then
    echo "Push succeeded on attempt ${attempt}."
    break
  fi
  if [[ $attempt -eq 3 ]]; then
    echo "Push failed after 3 attempts." >&2
    exit 1
  fi
  echo "Push rejected; pulling and rebasing (attempt ${attempt})."
  git pull --rebase origin main
done

echo
echo "Done. Streamlit Cloud will rebuild in ~30s."
echo "URL: https://arhart3-wm-dashboard-app-dp3loo.streamlit.app/"
