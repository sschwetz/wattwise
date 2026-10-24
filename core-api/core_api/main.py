from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core_api.admin_user import ensure_admin_user, expire_password_file, password_file_path
from core_api.config import get_settings
from core_api.db import make_engine, make_session_factory
from core_api.email_sender import EmailSender, SmtpEmailSender
from core_api.errors import install_error_handlers
from core_api.live import LiveHub
from core_api.scheduler import build_scheduler
from core_api.routers.account import router as account_router
from core_api.routers.auth import router as auth_router
from core_api.routers.billing import router as billing_router
from core_api.routers.collector import router as collector_router
from core_api.routers.collector_config import router as collector_config_router
from core_api.routers.ingestion import router as ingestion_router
from core_api.routers.live import router as live_router
from core_api.routers.local_rates import router as local_rates_router
from core_api.routers.password_reset import router as password_reset_router
from core_api.routers.pricing_backfill import router as pricing_backfill_router
from core_api.routers.pricing_backfill import site_router as pricing_site_data_router
from core_api.routers.query import router as query_router
from core_api.routers.sites import router as sites_router
from core_api.routers.sources import router as sources_router
from core_api.routers.totp import router as totp_router


def create_app(database_url: str | None = None, email_sender: EmailSender | None = None) -> FastAPI:
    # The engine/session factory are built eagerly (not solely inside
    # `lifespan`) because Starlette's TestClient only runs the ASGI lifespan
    # protocol when used as a context manager (`with TestClient(app) as c`).
    # Plain `TestClient(app)` + `.get(...)`, used throughout this test suite,
    # never triggers it, so `app.state.session_factory` must already be set
    # by the time the app object is returned. `lifespan` is kept to dispose
    # of the engine cleanly for real ASGI servers that do run it.
    # make_engine falls back to get_settings().database_url when database_url
    # is None; resolving it here too so ensure_admin_user writes its one-time
    # password file next to the SAME database the engine above actually uses,
    # rather than next to whatever get_settings() would resolve on its own.
    effective_database_url = database_url or get_settings().database_url
    engine = make_engine(effective_database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.session_factory = session_factory
        ensure_admin_user(session_factory, get_settings(), effective_database_url)
        # Also checked at startup: a container that was down at the daily run
        # time would otherwise keep the file until the next 03:17.
        password_file = password_file_path(effective_database_url)
        expire_password_file(password_file)
        scheduler = None
        if get_settings().scheduler_enabled:
            scheduler = build_scheduler(session_factory, password_file)
            scheduler.start()
        app.state.scheduler = scheduler
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        engine.dispose()

    # root_path is the prefix a reverse proxy strips before forwarding. It only
    # changes the URLs FastAPI generates, so /docs finds /openapi.json.
    app = FastAPI(title="Core API", lifespan=lifespan, root_path=get_settings().root_path)
    # The webapp is served from its own origin (a separate nginx container)
    # and calls this API directly from the browser, so without this every
    # request fails CORS before it ever reaches a route.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[get_settings().frontend_base_url],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.session_factory = session_factory
    app.state.email_sender = email_sender or SmtpEmailSender()
    app.state.live_hub = LiveHub()

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        # Unauthenticated and DB-free on purpose: this is Compose's
        # service_healthy signal that uvicorn is actually accepting requests
        # (migrations/admin bootstrap in docker-entrypoint.sh already ran by
        # the time uvicorn starts), not a dependency/DB liveness probe.
        return {"status": "ok"}

    install_error_handlers(app)
    # Spec: "Prefix: /api/v1 (cheap now, avoids pain at the first breaking
    # change)." Applied at include-time so each router's own prefix (e.g.
    # "/sites/{site_id}") is unaffected and simply nests under it.
    api_prefix = "/api/v1"
    app.include_router(auth_router, prefix=api_prefix)
    app.include_router(account_router, prefix=api_prefix)
    app.include_router(sites_router, prefix=api_prefix)
    app.include_router(sources_router, prefix=api_prefix)
    app.include_router(local_rates_router, prefix=api_prefix)
    app.include_router(billing_router, prefix=api_prefix)
    app.include_router(ingestion_router, prefix=api_prefix)
    app.include_router(live_router, prefix=api_prefix)
    app.include_router(query_router, prefix=api_prefix)
    app.include_router(collector_config_router, prefix=api_prefix)
    app.include_router(collector_router, prefix=api_prefix)
    app.include_router(pricing_backfill_router, prefix=api_prefix)
    app.include_router(pricing_site_data_router, prefix=api_prefix)
    app.include_router(totp_router, prefix=api_prefix)
    app.include_router(password_reset_router, prefix=api_prefix)
    return app
