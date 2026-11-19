from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from outreach.config import load_config
from outreach.store import Store
from outreach.threads_api import ThreadsPost

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 19, 18, 0, tzinfo=UTC)


@pytest.fixture
def cfg(tmp_path):
    shutil.copy(ROOT / "config.example.toml", tmp_path / "config.toml")
    return load_config(tmp_path / "config.toml")


@pytest.fixture
def store(cfg):
    s = Store(cfg.db_path)
    yield s
    s.close()


def make_post(pid: str, username: str = "newmom", text: str | None = None, hours_ago: int = 2) -> ThreadsPost:
    ts = NOW.replace(hour=NOW.hour - hours_ago).strftime("%Y-%m-%dT%H:%M:%S+0000")
    return ThreadsPost(
        id=pid,
        text=text or "Three weeks postpartum and I cried in the shower again. Does this get easier?",
        username=username,
        permalink=f"https://www.threads.com/@{username}/post/{pid}",
        timestamp=ts,
    )
