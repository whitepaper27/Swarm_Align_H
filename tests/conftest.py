"""Test-wide isolation: never read the developer's real `.env` (keys, prices, recorder key)."""

import pytest


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    monkeypatch.setenv("AIV_NO_DOTENV", "1")
