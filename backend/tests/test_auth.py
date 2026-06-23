"""Tests for the API-Key auth dependency on /api/v1."""
import pytest
from app.config import settings


@pytest.fixture
def open_mode(monkeypatch):
    monkeypatch.setattr(settings, "app_api_keys", "")


@pytest.fixture
def locked_mode(monkeypatch):
    monkeypatch.setattr(settings, "app_api_keys", "secret-1, secret-2")


async def test_open_mode_allows_request_without_key(open_mode, client):
    resp = await client.get("/api/v1/field-definitions")
    assert resp.status_code != 401


async def test_locked_mode_rejects_missing_key(locked_mode, client):
    resp = await client.get("/api/v1/field-definitions")
    assert resp.status_code == 401


async def test_locked_mode_rejects_wrong_key(locked_mode, client):
    resp = await client.get("/api/v1/field-definitions", headers={"X-API-Key": "nope"})
    assert resp.status_code == 401


async def test_locked_mode_accepts_valid_key(locked_mode, client):
    resp = await client.get("/api/v1/field-definitions", headers={"X-API-Key": "secret-2"})
    assert resp.status_code != 401


async def test_locked_mode_accepts_query_param_key(locked_mode, client):
    # File downloads use <a href>/window.open and cannot set headers; the key
    # is passed as ?api_key=. Header and query are interchangeable.
    resp = await client.get("/api/v1/field-definitions?api_key=secret-1")
    assert resp.status_code != 401


async def test_locked_mode_rejects_wrong_query_param_key(locked_mode, client):
    resp = await client.get("/api/v1/field-definitions?api_key=nope")
    assert resp.status_code == 401


async def test_health_is_unauthenticated(locked_mode, client):
    resp = await client.get("/health")
    assert resp.status_code == 200
