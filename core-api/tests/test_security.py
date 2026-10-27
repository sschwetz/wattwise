from __future__ import annotations

import datetime
import time

import jwt
import pyotp
import pytest
from cryptography.fernet import Fernet

from core_api.security import (
    create_access_token,
    create_scoped_token,
    decode_access_token,
    decode_scoped_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_password,
    hash_recovery_code,
    hash_token,
    verify_password,
    verify_totp_code,
    verify_totp_code_once,
)


def test_hash_password_round_trips(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_verify_password_rejects_none_hash():
    assert not verify_password("anything", None)


def test_verify_password_rejects_malformed_hash():
    # e.g. the '' backfilled by c2ba2e5a8811's downgrade() -- bcrypt.checkpw
    # raises ValueError on this rather than returning False, which must not
    # escape as a 500 (see verify_password's docstring).
    assert not verify_password("anything", "")


def test_create_and_decode_access_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    token = create_access_token(user_id=42)
    assert decode_access_token(token) == 42


def test_decode_access_token_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    token = create_access_token(user_id=42)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(tampered)


def test_decode_access_token_rejects_expired_token(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_EXPIRY_HOURS", "0")
    token = create_access_token(user_id=42)
    time.sleep(1.1)
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token)


def test_scoped_token_round_trips_user_id_and_jti():
    token, jti = create_scoped_token(42, "totp_setup")
    user_id, decoded_jti = decode_scoped_token(token, "totp_setup")
    assert user_id == 42
    assert decoded_jti == jti


def test_scoped_token_rejects_wrong_scope():
    token, _jti = create_scoped_token(42, "totp_setup")
    with pytest.raises(jwt.InvalidTokenError):
        decode_scoped_token(token, "mfa")


def test_hash_token_is_deterministic_and_matches_sha256():
    import hashlib

    assert hash_token("abc") == hashlib.sha256(b"abc").hexdigest()


def test_decode_scoped_token_rejects_a_token_missing_its_jti_claim(monkeypatch):
    """A correctly-signed scoped token with no jti used to raise a bare
    KeyError, which no caller's `except pyjwt.InvalidTokenError` catches — so it
    became an unhandled 500 instead of a clean 401."""
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    now = datetime.datetime.now(datetime.UTC)
    token = jwt.encode(
        {"sub": "42", "scope": "mfa", "iat": now, "exp": now + datetime.timedelta(minutes=5)},
        "test-secret",
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_scoped_token(token, "mfa")


def test_decode_access_token_rejects_a_scoped_token():
    """The critical cross-direction check: a setup_token/mfa_token must NOT
    work as a normal access_token just because it's signed with the same
    secret. Without this, a token issued before TOTP is ever verified would
    grant full API access — a full 2FA bypass during its 5-minute window."""
    scoped_token, _jti = create_scoped_token(42, "totp_setup")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(scoped_token)


def test_verify_totp_code_once_accepts_a_fresh_code_and_returns_its_step():
    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    step = verify_totp_code_once(secret, code, None)
    assert step is not None


def test_verify_totp_code_once_rejects_a_wrong_code():
    secret = generate_totp_secret()
    assert verify_totp_code_once(secret, "000000", None) is None


def test_verify_totp_code_once_rejects_a_replayed_code():
    """The anti-replay property: a code observed once (shoulder-surfing, a
    compromised MFA relay) must not be usable a second time within its own
    ~90s valid_window, even though pyotp's own .verify() would still accept
    it there."""
    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    first_step = verify_totp_code_once(secret, code, None)
    assert first_step is not None

    replayed = verify_totp_code_once(secret, code, first_step)
    assert replayed is None
    # The plain (non-anti-replay) verifier has no opinion on reuse -- this
    # confirms the replay rejection is a property of verify_totp_code_once
    # specifically, not of the code having somehow become invalid.
    assert verify_totp_code(secret, code) is True


def test_verify_totp_code_once_accepts_a_later_code_after_a_replay_was_rejected():
    secret = generate_totp_secret()
    totp = pyotp.TOTP(secret)
    first_code = totp.now()
    first_step = verify_totp_code_once(secret, first_code, None)

    # A code from a later step (simulating time passing) is still accepted.
    later_code = totp.at(time.time() + totp.interval)
    later_step = verify_totp_code_once(secret, later_code, first_step)
    assert later_step is not None
    assert later_step > first_step


def test_hash_recovery_code_is_keyed_and_deterministic(monkeypatch):
    first = hash_recovery_code("abcdef0123456789")
    assert first == hash_recovery_code("abcdef0123456789")
    assert first != hash_token("abcdef0123456789")

    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert hash_recovery_code("abcdef0123456789") != first


def test_generate_recovery_codes_are_16_hex_chars():
    codes = generate_recovery_codes()
    assert len(codes) == 10
    assert all(len(c) == 16 and int(c, 16) >= 0 for c in codes)
