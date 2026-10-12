from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core_api.errors import AppError, install_error_handlers


def make_test_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    def boom():
        raise AppError(404, "not_found", "Widget not found", detail={"widget_id": 42})

    return app


def test_app_error_renders_structured_body():
    client = TestClient(make_test_app())
    response = client.get("/boom")
    assert response.status_code == 404
    assert response.json() == {
        "code": "not_found",
        "message": "Widget not found",
        "detail": {"widget_id": 42},
    }


def test_validation_error_renders_structured_body(client):
    """Finding 3/I1: FastAPI's own 422s must also carry {code, message, detail}."""
    response = client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "x"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert body["message"] == "Request validation failed"
    assert isinstance(body["detail"], list) and body["detail"]


def test_unhandled_exception_renders_structured_body_without_leaking_details():
    """Finding 3/I1: an unhandled exception must return {code, message, detail}
    at 500, not a bare-text response leaking the exception's internals."""
    app = make_test_app()

    @app.get("/kaboom")
    def kaboom():
        raise RuntimeError("some internal secret detail")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/kaboom")
    assert response.status_code == 500
    assert response.json() == {"code": "internal_error", "message": "An internal error occurred", "detail": None}
    assert "some internal secret detail" not in response.text
