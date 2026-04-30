"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config"


@pytest.fixture
def ips_path() -> Path:
    return CONFIG / "ips.yaml"


@pytest.fixture
def targets_path() -> Path:
    return CONFIG / "targets.yaml"


@pytest.fixture
def positions_yaml_path() -> Path:
    return CONFIG / "positions.yaml"
