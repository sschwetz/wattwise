from __future__ import annotations

import datetime
import hashlib
import secrets
import time

import bcrypt
import jwt
import pyotp

from core_api.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    # password_hash is None for a confirmed-but-not-yet-set account (see
    # User.password_hash) and, after a schema downgrade, can be an empty
    # string -- bcrypt.checkpw raises ValueError on anything that isn't a
    # valid bcrypt hash rather than returning False. Letting that escape
    # would 500 instead of the uniform 401 every other failed login
    # returns, turning the distinct status code into exactly the
    # account-existence oracle this whole module exists to avoid.
    if password_hash is None:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + datetime.timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str) -> int:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if "scope" in payload:
        raise jwt.InvalidTokenError("access token must not carry a scope claim")
    return int(payload["sub"])


def create_scoped_token(user_id: int, scope: str, *, expiry_minutes: int = 5) -> tuple[str, str]:
    """A short-lived JWT for a specific next step in the login flow
    (TOTP setup or TOTP verify) — distinct from create_access_token's
    long-lived, unscoped token, so it can't be used against any other
    endpoint. jti lets the caller track failed-attempt counts server-side
    without the stateless JWT itself needing to carry mutable state."""
    settings = get_settings()
    now = datetime.datetime.now(datetime.timezone.utc)
    jti = secrets.token_urlsafe(16)
    payload = {
        "sub": str(user_id),
        "scope": scope,
        "jti": jti,
        "iat": now,
        "exp": now + datetime.timedelta(minutes=expiry_minutes),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, jti


def decode_scoped_token(token: str, expected_scope: str) -> tuple[int, str]:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    if payload.get("scope") != expected_scope:
        raise jwt.InvalidTokenError(f"expected scope {expected_scope!r}, got {payload.get('scope')!r}")
    jti = payload.get("jti")
    if jti is None:
        # A bare payload["jti"] would raise KeyError, which no caller's
        # `except pyjwt.InvalidTokenError` catches — turning a malformed token
        # into an unhandled 500 instead of a clean 401. Only reachable by
        # someone who already holds JWT_SECRET, but cheap to close properly.
        raise jwt.InvalidTokenError("scoped token missing jti claim")
    return int(payload["sub"]), jti


def hash_token(raw: str) -> str:
    """SHA-256 for system-generated random credentials (recovery codes,
    password reset tokens) — deterministic by design, unlike bcrypt, so
    verification is a direct DB lookup rather than an iterate-and-compare
    loop. Not for passwords; see Global Constraints in the plan this came
    from."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, email: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name="WattWise")


def verify_totp_code(secret: str, code: str) -> bool:
    # valid_window=1 tolerates the previous/next 30s window for clock drift
    # between the server and the user's authenticator device.
    return pyotp.totp.TOTP(secret).verify(code, valid_window=1)


def _totp_step_for_code(secret: str, code: str, *, valid_window: int = 1) -> int | None:
    totp = pyotp.totp.TOTP(secret)
    current_step = int(time.time() / totp.interval)
    for offset in range(-valid_window, valid_window + 1):
        step = current_step + offset
        if totp.at(step * totp.interval) == code:
            return step
    return None


def verify_totp_code_once(secret: str, code: str, last_used_step: int | None) -> int | None:
    """Like verify_totp_code, but additionally rejects a code whose matched
    30s step is not AFTER `last_used_step` -- closing TOTP's anti-replay gap.
    Without this, a code observed once (shoulder-surfing a screen, a
    compromised MFA relay) stays valid for reuse for its whole ~90s
    valid_window, since pyotp's own .verify() has no notion of "already
    used". Returns the matched step (the caller persists it as the new
    last_used_step) on success, or None if the code is wrong or a replay."""
    step = _totp_step_for_code(secret, code)
    if step is None:
        return None
    if last_used_step is not None and step <= last_used_step:
        return None
    return step


def generate_recovery_codes(count: int = 10) -> list[str]:
    return [secrets.token_hex(5) for _ in range(count)]
