"""Shared fixtures: account factory and canned-HTTP client factory."""
from __future__ import annotations

import httpx
import pytest

from app.config import AccountConfig


@pytest.fixture
def account():
    def make(**kw) -> AccountConfig:
        params = dict(name="test", provider="openrouter", api_key="sk-testkey-ab12")
        params.update(kw)
        acc = AccountConfig(**params)
        acc.slug = "test"
        return acc

    return make


@pytest.fixture
def mock_client():
    """httpx client routed through a local handler — no network."""

    def make(handler) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return make
