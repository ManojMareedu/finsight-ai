"""Network-free unit tests for src/api/routes/health.py.

/health must fail with 503 when Chroma or the embedding model is broken (not
report "ok" just because the process is up), and /ready must additionally
require provider config — while never making a billable LLM call.
"""

import fastapi
import pytest
from fastapi.testclient import TestClient

import src.api.routes.health as health_module


class _Settings:
    def __init__(self, key: str):
        self.openrouter_api_key = key
        self.chroma_persist_dir = "/tmp/fake-chroma"


@pytest.fixture
def client():
    app = fastapi.FastAPI()
    app.include_router(health_module.router)
    return TestClient(app)


def test_health_ok_when_core_works(monkeypatch, client):
    monkeypatch.setattr(health_module, "_check_core", lambda: None)
    assert client.get("/health").status_code == 200


def test_health_503_when_chroma_or_embeddings_fail(monkeypatch, client):
    def _boom():
        raise RuntimeError("chroma dir unreadable")

    monkeypatch.setattr(health_module, "_check_core", _boom)
    resp = client.get("/health")
    assert resp.status_code == 503
    assert "chroma dir unreadable" in resp.json()["detail"]


def test_ready_503_without_provider_key(monkeypatch, client):
    monkeypatch.setattr(health_module, "_check_core", lambda: None)
    monkeypatch.setattr(health_module, "get_settings", lambda: _Settings(""))
    assert client.get("/ready").status_code == 503


def test_ready_ok_with_provider_key_and_no_llm_call(monkeypatch, client):
    monkeypatch.setattr(health_module, "_check_core", lambda: None)
    monkeypatch.setattr(health_module, "get_settings", lambda: _Settings("sk-test"))

    import src.utils.llm_client as llm_client

    def _fail(*a, **k):
        raise AssertionError("/ready must never make a billable LLM call")

    monkeypatch.setattr(llm_client, "chat", _fail)
    assert client.get("/ready").json() == {"status": "ok"}


def test_startup_warms_embeddings_before_any_probe(monkeypatch):
    """T0-1: get_embeddings() must be called once at process startup, not
    lazily inside the first /health request — a cold-container model load
    racing the Dockerfile's 10s HEALTHCHECK timeout."""
    import asyncio

    import src.api.main as main_module

    calls = []
    monkeypatch.setattr(main_module, "get_embeddings", lambda: calls.append(1))
    asyncio.run(main_module._startup())
    assert calls == [1]
