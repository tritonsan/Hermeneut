from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.mcp_proxy import _validate_proxy_token, router
from app.settings import Settings


def test_validate_proxy_token_accepts_missing_expected_token():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    request = client.build_request("GET", "/mcp/health")

    _validate_proxy_token(request, None)


def test_mcp_proxy_health_rejects_invalid_token(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    monkeypatch.setattr(
        "app.routers.mcp_proxy.get_settings",
        lambda: Settings(mcp_proxy_token="expected-token"),
    )

    response = client.get("/mcp/health?token=wrong-token")

    assert response.status_code == 401
