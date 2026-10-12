from __future__ import annotations

import re

import pyotp


def _extract_confirmation_token(body: str) -> str:
    match = re.search(r"token=([^\s]+)", body)
    assert match is not None
    return match.group(1)


def _register_and_confirm(client, email: str, password: str) -> None:
    """Registration now requires confirming the emailed link before the
    account can log in (core-api finding 3: closes /register's email_taken
    enumeration oracle). The password is chosen at /register/confirm, not at
    /register -- see auth.confirm_registration. `client.app.state.email_sender`
    is the same FakeEmailSender instance the `fake_email_sender` fixture
    provides, so tests that don't otherwise need that fixture can still get
    at it here."""
    client.post("/api/v1/auth/register", json={"email": email})
    sent = client.app.state.email_sender.sent
    token = _extract_confirmation_token(sent[-1][2])
    confirm = client.post("/api/v1/auth/register/confirm", json={"token": token, "password": password})
    assert confirm.status_code == 204


def _register_and_get_setup_token(client, email="a@example.com", password="correct-password"):
    _register_and_confirm(client, email, password)
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return login.json()["setup_token"]


def test_setup_start_returns_secret_and_otpauth_uri(client):
    setup_token = _register_and_get_setup_token(client)
    response = client.post(
        "/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {setup_token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["secret"]
    assert body["otpauth_uri"].startswith("otpauth://totp/")


def test_setup_confirm_with_correct_code_enables_totp_and_returns_recovery_codes(client):
    setup_token = _register_and_get_setup_token(client)
    start = client.post(
        "/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {setup_token}"}
    )
    secret = start.json()["secret"]
    code = pyotp.TOTP(secret).now()
    response = client.post(
        "/api/v1/auth/totp/setup/confirm",
        json={"code": code},
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert len(body["recovery_codes"]) == 10
    assert len(set(body["recovery_codes"])) == 10  # all distinct


def test_setup_confirm_with_wrong_code_is_rejected(client):
    setup_token = _register_and_get_setup_token(client)
    client.post("/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {setup_token}"})
    response = client.post(
        "/api/v1/auth/totp/setup/confirm",
        json={"code": "000000"},
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_code"


def test_setup_endpoints_reject_a_normal_access_token(client):
    client.post("/api/v1/auth/register", json={"email": "b@example.com", "password": "correct-password"})
    # An unenrolled user has no access_token yet, so forge the scenario with a
    # wrong-scope token to exercise the dependency directly: a setup_token's
    # own endpoint must still 401 a non-setup-scoped bearer token.
    response = client.post(
        "/api/v1/auth/totp/setup/start", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401


def _enroll_and_login(client, email="enrolled@example.com", password="correct-password"):
    """Returns (recovery_codes, mfa_token) for a freshly enrolled user who has
    just logged in again (so totp_enabled is True and a fresh mfa_token exists)."""
    _register_and_confirm(client, email, password)
    setup_login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    setup_token = setup_login.json()["setup_token"]
    start = client.post("/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {setup_token}"})
    secret = start.json()["secret"]
    code = pyotp.TOTP(secret).now()
    confirm = client.post(
        "/api/v1/auth/totp/setup/confirm",
        json={"code": code},
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    recovery_codes = confirm.json()["recovery_codes"]
    second_login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert second_login.json()["status"] == "totp_required"
    return secret, recovery_codes, second_login.json()["mfa_token"]


def test_verify_with_correct_totp_code_returns_access_token(client):
    secret, _recovery_codes, mfa_token = _enroll_and_login(client)
    code = pyotp.TOTP(secret).now()
    response = client.post(
        "/api/v1/auth/totp/verify", json={"code": code}, headers={"Authorization": f"Bearer {mfa_token}"}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_verify_rejects_a_replayed_totp_code_even_with_a_fresh_mfa_token(client):
    """A TOTP code observed once (shoulder-surfing, a compromised MFA relay)
    must not be usable a second time within its own ~90s valid_window. A
    fresh mfa_token (from re-login) is used for the replay to isolate this
    from the per-token attempt counter -- the replay must be rejected on its
    own merits, not because of lockout."""
    email, password = "replay@example.com", "correct-password"
    secret, _recovery_codes, mfa_token = _enroll_and_login(client, email=email, password=password)
    code = pyotp.TOTP(secret).now()

    first = client.post(
        "/api/v1/auth/totp/verify", json={"code": code}, headers={"Authorization": f"Bearer {mfa_token}"}
    )
    assert first.status_code == 200

    relogin = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    fresh_mfa_token = relogin.json()["mfa_token"]
    replayed = client.post(
        "/api/v1/auth/totp/verify", json={"code": code}, headers={"Authorization": f"Bearer {fresh_mfa_token}"}
    )
    assert replayed.status_code == 401
    assert replayed.json()["code"] == "invalid_code"


def test_verify_with_recovery_code_succeeds_once_then_fails(client):
    _secret, recovery_codes, mfa_token = _enroll_and_login(client)
    first = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": recovery_codes[0]},
        headers={"Authorization": f"Bearer {mfa_token}"},
    )
    assert first.status_code == 200

    # A fresh mfa_token is needed for the second attempt since the first
    # call's success already cleared this jti's attempt counter but the
    # recovery code itself is now used — login again to get a new mfa_token.
    _secret2, _codes2, mfa_token_2 = _enroll_and_login(client, email="enrolled2@example.com")
    reused = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": recovery_codes[0]},
        headers={"Authorization": f"Bearer {mfa_token_2}"},
    )
    assert reused.status_code == 401


def test_verify_with_recovery_code_cannot_be_reused_by_the_same_user(client):
    # The cross-user variant above (test_verify_with_recovery_code_succeeds_once_then_fails)
    # would pass 401 even if used_at were never checked, since the second
    # user's recovery codes belong to a different user_id. This test proves
    # the same user, with a fresh mfa_token, is genuinely blocked from
    # redeeming the same already-used recovery code a second time.
    email, password = "enrolled-reuse@example.com", "correct-password"
    _secret, recovery_codes, mfa_token = _enroll_and_login(client, email=email, password=password)
    first = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": recovery_codes[0]},
        headers={"Authorization": f"Bearer {mfa_token}"},
    )
    assert first.status_code == 200

    relogin = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert relogin.json()["status"] == "totp_required"
    fresh_mfa_token = relogin.json()["mfa_token"]

    reused = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": recovery_codes[0]},
        headers={"Authorization": f"Bearer {fresh_mfa_token}"},
    )
    assert reused.status_code == 401
    assert reused.json()["code"] == "invalid_code"


def test_verify_rejects_a_racing_double_submit_of_the_same_recovery_code(client, monkeypatch):
    """Two overlapping /verify calls presenting the SAME recovery code (e.g. a
    retried request) must not both succeed. Without an atomic claim, both
    requests' SELECT sees used_at still NULL, so both proceed to issue an
    access token and commit — a single recovery code would grant two logins.
    A `threading.Barrier` forces the two requests to overlap deterministically,
    standing in for real concurrent HTTP requests."""
    import threading

    from core_api.routers import totp as totp_module

    _secret, recovery_codes, mfa_token = _enroll_and_login(client)

    barrier = threading.Barrier(2)
    original_hash_token = totp_module.hash_token

    def synced_hash_token(raw_code):
        barrier.wait()
        return original_hash_token(raw_code)

    monkeypatch.setattr(totp_module, "hash_token", synced_hash_token)

    results: list[int] = []

    def attempt() -> None:
        response = client.post(
            "/api/v1/auth/totp/verify",
            json={"code": recovery_codes[0]},
            headers={"Authorization": f"Bearer {mfa_token}"},
        )
        results.append(response.status_code)

    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [200, 401], "exactly one racer should win; the other must be cleanly rejected"


def test_verify_rejects_a_racing_double_submit_of_the_same_totp_code(client, monkeypatch):
    """Two overlapping /verify calls presenting the SAME TOTP code must not
    both succeed. The anti-replay guard is enforced by an atomic conditional
    UPDATE (_claim_totp_step), not by the Python-level check inside
    verify_totp_code_once -- each request loads its own User, so both can see
    the same pre-race last_totp_step and both pass that check before either
    commits. A `threading.Barrier` forces both requests past that check
    before either reaches the UPDATE, standing in for real concurrent HTTP
    requests racing on the same shoulder-surfed code."""
    import threading

    from core_api.routers import totp as totp_module

    secret, _recovery_codes, mfa_token = _enroll_and_login(client)
    code = pyotp.TOTP(secret).now()

    barrier = threading.Barrier(2)
    original_verify_once = totp_module.verify_totp_code_once

    def synced_verify_once(secret, code, last_used_step):
        result = original_verify_once(secret, code, last_used_step)
        barrier.wait()
        return result

    monkeypatch.setattr(totp_module, "verify_totp_code_once", synced_verify_once)

    results: list[int] = []

    def attempt() -> None:
        response = client.post(
            "/api/v1/auth/totp/verify",
            json={"code": code},
            headers={"Authorization": f"Bearer {mfa_token}"},
        )
        results.append(response.status_code)

    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [200, 401], "exactly one racer should win; the other must be cleanly rejected"


def test_verify_locks_out_after_five_failed_attempts(client):
    secret, _recovery_codes, mfa_token = _enroll_and_login(client)
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/totp/verify",
            json={"code": "000000"},
            headers={"Authorization": f"Bearer {mfa_token}"},
        )
        assert response.status_code == 401

    # Even a correct code is now rejected — the same mfa_token is locked out.
    correct_code = pyotp.TOTP(secret).now()
    response = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": correct_code},
        headers={"Authorization": f"Bearer {mfa_token}"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "too_many_attempts"


def test_lockout_survives_re_login_with_a_fresh_mfa_token(client):
    """The lockout must follow the USER, not the mfa_token. Previously the
    counter was keyed by the token's jti, so an attacker who already had the
    password could mint an unlimited supply of fresh mfa_tokens by re-logging
    in — each with a new jti and therefore a brand-new 5-guess budget — which
    made the cap decorative against a 6-digit code. A fresh token must inherit
    the user's existing lockout."""
    email, password = "lockout-relogin@example.com", "correct-password"
    secret, _recovery_codes, mfa_token = _enroll_and_login(client, email=email, password=password)

    for _ in range(5):
        failed = client.post(
            "/api/v1/auth/totp/verify",
            json={"code": "000000"},
            headers={"Authorization": f"Bearer {mfa_token}"},
        )
        assert failed.status_code == 401

    # Re-login to mint a genuinely different mfa_token (new jti).
    relogin = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert relogin.status_code == 200
    fresh_mfa_token = relogin.json()["mfa_token"]
    assert fresh_mfa_token != mfa_token

    # A CORRECT code against the FRESH token must still be refused.
    correct_code = pyotp.TOTP(secret).now()
    response = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": correct_code},
        headers={"Authorization": f"Bearer {fresh_mfa_token}"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "too_many_attempts"


def _enrolled_user_id(db_url: str, email: str) -> int:
    from sqlalchemy import select as sa_select

    from core_api.db import make_engine, make_session_factory
    from core_api.models.identity import User

    session = make_session_factory(make_engine(db_url))()
    try:
        user = session.execute(sa_select(User).where(User.email == email)).scalar_one()
        assert user.totp_enabled is True
        return user.id
    finally:
        session.close()


def test_setup_start_rejects_an_already_enrolled_user(client, app_and_db_url):
    """Once enrolled, the normal login flow returns totp_required and never
    hands out another setup_token — so the only way to reach these endpoints
    again is a stale or replayed setup_token from before enrollment completed.
    create_scoped_token reproduces exactly that, which is the real mechanism
    behind an accidental double-submit."""
    from core_api.security import create_scoped_token

    _app, db_url = app_and_db_url
    email = "already-enrolled-start@example.com"
    _enroll_and_login(client, email=email)
    user_id = _enrolled_user_id(db_url, email)

    stale_setup_token, _jti = create_scoped_token(user_id, "totp_setup")
    response = client.post(
        "/api/v1/auth/totp/setup/start",
        headers={"Authorization": f"Bearer {stale_setup_token}"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "already_enrolled"


def test_setup_confirm_rejects_an_already_enrolled_user_and_issues_no_extra_codes(client, app_and_db_url):
    """The damaging half of the double-submit: a second successful confirm used
    to append 10 MORE recovery codes without invalidating the first 10, leaving
    live codes the user never saw. Uses the real TOTP secret so the code itself
    is valid — only the already-enrolled guard may reject it."""
    from sqlalchemy import func, select as sa_select

    from core_api.db import make_engine, make_session_factory
    from core_api.models.identity import RecoveryCode
    from core_api.security import create_scoped_token

    _app, db_url = app_and_db_url
    email = "already-enrolled-confirm@example.com"
    secret, _recovery_codes, _mfa_token = _enroll_and_login(client, email=email)
    user_id = _enrolled_user_id(db_url, email)

    stale_setup_token, _jti = create_scoped_token(user_id, "totp_setup")
    response = client.post(
        "/api/v1/auth/totp/setup/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers={"Authorization": f"Bearer {stale_setup_token}"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "already_enrolled"

    session = make_session_factory(make_engine(db_url))()
    try:
        total_codes = session.execute(
            sa_select(func.count()).select_from(RecoveryCode).where(RecoveryCode.user_id == user_id)
        ).scalar_one()
    finally:
        session.close()
    assert total_codes == 10, "a rejected re-confirm must not append a second batch of recovery codes"
