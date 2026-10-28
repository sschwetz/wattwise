from __future__ import annotations

import datetime
import os
import zoneinfo
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from core_api import login_attempts, mfa_attempts
from core_api.bootstrap import run_migrations
from core_api.db import make_engine, make_session_factory
from core_api.email_sender import FakeEmailSender
from core_api.main import create_app
from core_api.models import Base
from core_api.models.identity import Site, User

TEST_SECRETS_ENCRYPTION_KEY = "Bs7vdii7tm51P9fe-7-D4L0nWd7BkKEjE8A6ixcc0FI="


@pytest.fixture(scope="session", autouse=True)
def _set_test_jwt_secret():
    os.environ.setdefault("JWT_SECRET", "test-secret")
    os.environ.setdefault("SECRETS_ENCRYPTION_KEY", TEST_SECRETS_ENCRYPTION_KEY)


ENVOY_COLLECTOR_KEY = "test-envoy-key"
WEATHER_COLLECTOR_KEY = "test-weather-key"
PRICING_COLLECTOR_KEY = "test-pricing-key"


@pytest.fixture(scope="session", autouse=True)
def _set_test_collector_keys():
    os.environ.setdefault("ENVOY_COLLECTOR_KEY", ENVOY_COLLECTOR_KEY)
    os.environ.setdefault("WEATHER_COLLECTOR_KEY", WEATHER_COLLECTOR_KEY)
    os.environ.setdefault("PRICING_COLLECTOR_KEY", PRICING_COLLECTOR_KEY)


@pytest.fixture(autouse=True)
def _reset_mfa_attempts():
    """mfa_attempts keys its failed-attempt counter by user_id in module-level
    process state, while every test gets a fresh database whose ids restart at
    1 — so without this, a test that locks out "user 1" would leave the next
    test's unrelated "user 1" already locked out. Purely a test-isolation
    concern: in production, ids are stable and the counter ageing out over a
    15-minute window is the intended behavior."""
    mfa_attempts._tracker._failures.clear()
    login_attempts._tracker._failures.clear()
    yield
    mfa_attempts._tracker._failures.clear()
    login_attempts._tracker._failures.clear()


@pytest.fixture()
def fake_email_sender() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture()
def app_and_db_url(tmp_path, monkeypatch, fake_email_sender):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", TEST_SECRETS_ENCRYPTION_KEY)
    monkeypatch.setenv("AVATAR_STORAGE_DIR", str(tmp_path / "avatars"))
    db_url = f"sqlite:///{tmp_path}/app_test.db"
    run_migrations(db_url)
    app = create_app(database_url=db_url, email_sender=fake_email_sender)
    return app, db_url


@pytest.fixture()
def client(app_and_db_url):
    app, _ = app_and_db_url
    return TestClient(app)


@pytest.fixture()
def db(tmp_path) -> Iterator[Session]:
    engine = make_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def user(db: Session) -> User:
    u = User(email="owner@example.com", password_hash="hash")
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def site(db: Session, user: User) -> Site:
    s = Site(owner_user_id=user.id, name="Home", timezone="Australia/Sydney")
    db.add(s)
    db.commit()
    return s


UTC = datetime.UTC

SYDNEY = zoneinfo.ZoneInfo("Australia/Sydney")


def sydney_local(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime.datetime:
    """The UTC instant corresponding to this Sydney local wall-clock time --
    lets a test express '3pm local' while still passing around the
    UTC-represented values these jobs expect (see core_api.time_utils.as_utc
    for why representation matters here, not just absolute instant: SQLite
    silently drops tzinfo on write, so a non-UTC-represented value would
    store/compare the wrong wall-clock numbers once it touches the DB)."""
    return datetime.datetime(year, month, day, hour, minute, tzinfo=SYDNEY).astimezone(UTC)
