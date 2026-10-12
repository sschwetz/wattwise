from __future__ import annotations

import os

from fastapi.testclient import TestClient

from core_api.bootstrap import run_migrations
from core_api.main import create_app


def test_cors_allows_the_configured_frontend_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.example.com")
    db_url = f"sqlite:///{tmp_path}/cors_test.db"
    run_migrations(db_url)
    app = create_app(database_url=db_url)
    with TestClient(app) as test_client:
        response = test_client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert response.headers.get("access-control-allow-origin") == "https://app.example.com"


def test_cors_rejects_an_unrecognized_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.example.com")
    db_url = f"sqlite:///{tmp_path}/cors_test.db"
    run_migrations(db_url)
    app = create_app(database_url=db_url)
    with TestClient(app) as test_client:
        response = test_client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
    assert "access-control-allow-origin" not in response.headers


def test_create_app_boots(client):
    # No routes yet at this point in the plan beyond error handling; this
    # just proves the app + migrated DB + session dependency all wire up.
    response = client.get("/openapi.json")
    assert response.status_code == 200


def test_all_expected_routes_are_registered(client):
    from starlette.routing import Mount, Route

    def collect_paths(routes, prefix=""):
        """Recursively collect all route paths, handling Route, Mount, and _IncludedRouter types."""
        paths = set()
        for route in routes:
            route_type = type(route).__name__
            if route_type == "_IncludedRouter":
                # _IncludedRouter contains an original_router with the actual routes
                # The include_context has the prefix
                include_context = getattr(route, 'include_context', None)
                route_prefix = prefix
                if include_context and hasattr(include_context, 'prefix'):
                    route_prefix = prefix + include_context.prefix

                # Get the original router's routes
                orig_router = getattr(route, 'original_router', None)
                if orig_router and hasattr(orig_router, 'routes'):
                    for subroute in orig_router.routes:
                        subroute_path = getattr(subroute, 'path', None)
                        if subroute_path:
                            paths.add(route_prefix + subroute_path)
            elif isinstance(route, Mount):
                # Mount objects have a path and contain nested routes
                mount_prefix = prefix + route.path
                if hasattr(route, 'routes'):
                    paths.update(collect_paths(route.routes, mount_prefix))
            elif isinstance(route, Route):
                # Route objects have a direct path
                paths.add(prefix + route.path)
        return paths

    paths = collect_paths(client.app.routes)
    prefix = "/api/v1"
    expected = {
        prefix + "/auth/login",
        prefix + "/sites",
        prefix + "/sites/{site_id}",
        prefix + "/sites/{site_id}/access",
        prefix + "/sites/{site_id}/access/{user_id}",
        prefix + "/sites/{site_id}/sources",
        prefix + "/sites/{site_id}/sources/{source_id}",
        prefix + "/sources/{source_id}/readings/energy",
        prefix + "/sources/{source_id}/readings/weather",
        prefix + "/sources/{source_id}/prices",
        prefix + "/sites/{site_id}/readings/energy",
        prefix + "/sites/{site_id}/readings/weather",
        prefix + "/sites/{site_id}/prices",
        prefix + "/sites/{site_id}/cost",
        prefix + "/sites/{site_id}/demand-charges",
        prefix + "/sites/{site_id}/tariffs",
        prefix + "/sites/{site_id}/tariffs/{tariff_id}/demand-windows",
        prefix + "/sites/{site_id}/billing-cycle",
        prefix + "/sites/{site_id}/tariffs/{tariff_id}",
        prefix + "/sites/{site_id}/billing-cycle/{cycle_id}",
    }
    missing = expected - paths
    assert not missing, f"Missing routes: {missing}"
