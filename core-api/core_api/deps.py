from __future__ import annotations

import datetime
import secrets
from collections.abc import Callable, Iterator

import jwt as pyjwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from core_api.config import get_settings
from core_api.db import get_db
from core_api.email_sender import EmailSender
from core_api.errors import AppError
from core_api.mfa_attempts import is_locked
from core_api.models.identity import Site, SiteAccess, User
from core_api.models.sources import Source, SourceKind
from core_api.security import decode_access_token, decode_scoped_token


def get_db_session(request: Request) -> Iterator[Session]:
    yield from get_db(request.app.state.session_factory)


def get_email_sender(request: Request) -> EmailSender:
    return request.app.state.email_sender


_bearer_scheme = HTTPBearer(auto_error=False)


def _resolve_user_from_token(
    credentials: HTTPAuthorizationCredentials | None,
    db: Session,
    decode: Callable[[str], tuple[int, str | None]],
) -> tuple[User, str | None]:
    """Shared auth preamble for get_current_user/require_setup_token/require_mfa_token:
    missing-credentials check, token decode with a uniform invalid_token error, and the
    user-still-exists lookup. `decode` returns (user_id, jti) — jti is None for a plain
    access token, which has no jti claim."""
    if credentials is None:
        raise AppError(401, "missing_token", "Authorization header is required")
    try:
        user_id, jti = decode(credentials.credentials)
    except pyjwt.InvalidTokenError as exc:
        raise AppError(401, "invalid_token", "Invalid or expired token") from exc
    user = db.get(User, user_id)
    if user is None:
        raise AppError(401, "invalid_token", "User no longer exists")
    return user, jti


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db_session),
) -> User:
    user, _jti = _resolve_user_from_token(credentials, db, lambda token: (decode_access_token(token), None))
    return user


def require_setup_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db_session),
) -> User:
    user, _jti = _resolve_user_from_token(credentials, db, lambda token: decode_scoped_token(token, "totp_setup"))
    return user


def _decode_mfa_token_and_check_lockout(token: str) -> tuple[int, str | None]:
    """decode_scoped_token's result, with the attempt-cap check applied right
    after decode and before the caller's user-exists lookup -- preserving
    require_mfa_token's original check order (decode -> lockout -> user
    lookup). Raising AppError here (rather than pyjwt.InvalidTokenError)
    means it passes straight through _resolve_user_from_token's narrower
    except clause instead of being reworded into invalid_token.

    The lockout is keyed on user_id, deliberately NOT on the token's jti: a
    per-token budget is reset by simply logging in again for a fresh
    mfa_token, which made the cap decorative against an attacker who already
    has the password. See core_api.mfa_attempts."""
    user_id, jti = decode_scoped_token(token, "mfa")
    if is_locked(user_id):
        raise AppError(401, "too_many_attempts", "Too many failed attempts; please log in again")
    return user_id, jti


def require_mfa_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: Session = Depends(get_db_session),
) -> User:
    user, _jti = _resolve_user_from_token(credentials, db, _decode_mfa_token_and_check_lockout)
    return user


def site_visible_to(db: Session, site_id: int, user_id: int) -> Site | None:
    """The site if `user_id` owns it or holds a SiteAccess grant, else None.
    A missing site and a hidden one look the same to the caller."""
    site = db.get(Site, site_id)
    if site is None:
        return None
    if site.owner_user_id != user_id and db.get(SiteAccess, (site_id, user_id)) is None:
        return None
    return site


def require_site_access(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> Site:
    site = site_visible_to(db, site_id, current_user.id)
    if site is None:
        raise AppError(404, "not_found", f"Site {site_id} not found")
    return site


def require_site_owner(
    site_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> Site:
    site = site_visible_to(db, site_id, current_user.id)
    if site is None:
        raise AppError(404, "not_found", f"Site {site_id} not found")
    if site.owner_user_id != current_user.id:
        raise AppError(403, "forbidden", "Only the site owner can perform this action")
    return site


_KIND_KEY_ATTR: dict[SourceKind, str] = {
    SourceKind.ENERGY: "envoy_collector_key",
    SourceKind.WEATHER: "weather_collector_key",
    SourceKind.PRICING: "pricing_collector_key",
}


def check_collector_key(kind: SourceKind, x_collector_key: str) -> bool:
    """Compare a collector-supplied key against the expected shared key for
    `kind`. Compares UTF-8-encoded bytes rather than the raw `str` because
    `secrets.compare_digest` raises `TypeError` on non-ASCII `str` arguments,
    and Starlette decodes header bytes as latin-1 -- so a non-ASCII header
    value would otherwise crash this check with a 500 instead of cleanly
    401ing."""
    expected = getattr(get_settings(), _KIND_KEY_ATTR[kind])
    return secrets.compare_digest(x_collector_key.encode("utf-8"), expected.encode("utf-8"))


def get_source_by_collector_key(
    source_id: int,
    x_collector_key: str = Header(...),
    x_collector_provider: str | None = Header(None),
    db: Session = Depends(get_db_session),
) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    if not check_collector_key(source.kind, x_collector_key):
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    # A collector that names its provider can only reach that provider's
    # sources, so one vendor's collector cannot touch another's data.
    if x_collector_provider is not None and x_collector_provider != source.provider:
        raise AppError(401, "invalid_collector_key", "Invalid collector key for this source")
    if not source.enabled:
        raise AppError(403, "source_disabled", "This source is disabled")
    site = db.get(Site, source.site_id)
    if site is None or not site.enabled:
        raise AppError(403, "site_disabled", "This site is disabled")
    if source.effective_to is not None and source.effective_to <= datetime.datetime.now(datetime.UTC):
        raise AppError(403, "source_expired", "This source's effective period has ended")
    return source


def require_source_kind(expected_kind: SourceKind):
    """Dependency factory gating an ingestion endpoint to sources of the
    expected kind — without this, an energy source's collector key could be
    used to POST to the weather/prices endpoints, silently writing rows
    tagged with the wrong source id (invisible to every kind-filtered query
    endpoint)."""

    def _dependency(source: Source = Depends(get_source_by_collector_key)) -> Source:
        if source.kind != expected_kind:
            raise AppError(403, "wrong_source_kind", f"This source is not a {expected_kind.value} source")
        return source

    return _dependency
