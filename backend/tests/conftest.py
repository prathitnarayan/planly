"""
Tests never touch real Supabase, even when backend/.env has real values:
fresh in-memory storage per test, and dev-mode login (unless a test turns auth on).
"""

import pytest

from app.core import config
from app.core.repo import InMemoryRepo
from app.main import app, get_repo


@pytest.fixture(autouse=True)
def isolated_app(monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "dev")
    repo = InMemoryRepo()
    app.dependency_overrides[get_repo] = lambda: repo
    yield repo
    app.dependency_overrides.clear()
