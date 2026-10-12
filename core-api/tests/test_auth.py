from __future__ import annotations

import re


def _extract_confirmation_token(body: str) -> str:
    match = re.search(r"token=([^\s]+)", body)
    assert match is not None
    return match.group(1)


def _register_and_get_token(client, fake_email_sender, email: str) -> str:
    """Registers `email` (no password -- that's chosen at confirm time) and
    returns the confirmation token just emailed to it, for tests that want
    to exercise /register/confirm directly."""
    client.post("/api/v1/auth/register", json={"email": email})
    return _extract_confirmation_token(fake_email_sender.sent[-1][2])


def _register_and_confirm(client, email: str, password: str) -> None:
    """Registration now requires confirming the emailed link before the
    account can log in (core-api finding 3: closes /register's email_taken
    enumeration oracle). The password itself is chosen at /register/confirm,
    not at /register -- see auth.confirm_registration -- so it's supplied
    here, not in the initial /register call. `client.app.state.email_sender`
    is the same FakeEmailSender instance the `fake_email_sender` fixture
    provides, so tests that don't otherwise need that fixture can still get
    at it here."""
    client.post("/api/v1/auth/register", json={"email": email})
    sent = client.app.state.email_sender.sent
    token = _extract_confirmation_token(sent[-1][2])
    confirm = client.post("/api/v1/auth/register/confirm", json={"token": token, "password": password})
    assert confirm.status_code == 204


def test_login_with_unenrolled_user_requires_totp_setup(client):
    _register_and_confirm(client, "a@example.com", "correct-password")
    response = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "correct-password"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "totp_setup_required"
    assert body["setup_token"]
    assert body["mfa_token"] is None


def test_login_rejects_wrong_password(client):
    _register_and_confirm(client, "a@example.com", "correct-password")
    response = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_login_rejects_unknown_email(client):
    response = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_login_rejects_an_unconfirmed_account_even_with_the_correct_password(client, fake_email_sender):
    """The other half of closing /register's enumeration oracle (core-api
    finding 3): an unconfirmed account must be rejected the SAME way as a
    wrong password -- invalid_credentials, no distinguishing detail -- not a
    different error that would let an attacker tell "this email is mine,
    just unconfirmed" apart from "this email is already someone else's"."""
    client.post("/api/v1/auth/register", json={"email": "unconfirmed@example.com"})
    response = client.post(
        "/api/v1/auth/login", json={"email": "unconfirmed@example.com", "password": "correct-password"}
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"

    token = _extract_confirmation_token(fake_email_sender.sent[-1][2])
    client.post("/api/v1/auth/register/confirm", json={"token": token, "password": "correct-password"})
    confirmed = client.post(
        "/api/v1/auth/login", json={"email": "unconfirmed@example.com", "password": "correct-password"}
    )
    assert confirmed.status_code == 200


def test_login_locks_out_after_five_failed_attempts(client):
    """Without this, a password for an account that hasn't finished TOTP setup
    could be brute-forced with no limit, then the attacker enrolls their OWN
    authenticator on /auth/totp/setup — full account takeover with no 2FA ever
    in the way."""
    _register_and_confirm(client, "a@example.com", "correct-password")
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong"})
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_credentials"

    # Even the CORRECT password is now rejected while locked out.
    response = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "correct-password"})
    assert response.status_code == 401
    assert response.json()["code"] == "too_many_attempts"


def test_login_lockout_is_keyed_per_email_not_global(client):
    _register_and_confirm(client, "a@example.com", "correct-password")
    _register_and_confirm(client, "b@example.com", "correct-password")
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong"})

    response = client.post("/api/v1/auth/login", json={"email": "b@example.com", "password": "correct-password"})
    assert response.status_code == 200


def test_login_lockout_also_throttles_an_unregistered_email(client):
    """Must lock out unknown emails too: if only known emails ever locked, the
    presence/absence of a lockout after 5 attempts would itself leak whether
    the email is registered."""
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
        assert response.status_code == 401

    response = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x"})
    assert response.status_code == 401
    assert response.json()["code"] == "too_many_attempts"


def test_login_success_clears_the_failure_counter(client):
    _register_and_confirm(client, "a@example.com", "correct-password")
    for _ in range(4):
        client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong"})

    ok = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "correct-password"})
    assert ok.status_code == 200

    # The counter was reset by the success above, so one more wrong guess
    # should not trip the (otherwise now-at-5) lockout.
    response = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_setup_token_cannot_be_used_against_a_normal_protected_endpoint(client):
    """End-to-end version of the security.py-level check added in Task 3:
    a setup_token issued by /auth/login must not work as a bearer token
    anywhere else in the API."""
    _register_and_confirm(client, "a@example.com", "correct-password")
    login = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "correct-password"})
    setup_token = login.json()["setup_token"]
    response = client.get("/api/v1/sites", headers={"Authorization": f"Bearer {setup_token}"})
    assert response.status_code == 401


def test_register_returns_a_generic_message_and_sends_a_confirmation_email(client, fake_email_sender):
    response = client.post("/api/v1/auth/register", json={"email": "new@example.com"})
    assert response.status_code == 200
    assert "password" not in response.json()
    assert "password_hash" not in response.json()

    assert len(fake_email_sender.sent) == 1
    to, _subject, body = fake_email_sender.sent[0]
    assert to == "new@example.com"
    assert "confirm-registration?token=" in body


def test_register_response_is_identical_for_a_new_and_an_already_confirmed_email(client, fake_email_sender):
    """The anti-enumeration property this flow exists for (core-api
    finding 3): a duplicate registration against an already-CONFIRMED email
    must get the exact same response as a brand-new one, with no
    status-code or body difference to reveal that the email is taken."""
    _register_and_confirm(client, "taken@example.com", "a-password")
    fake_email_sender.sent.clear()

    duplicate_response = client.post("/api/v1/auth/register", json={"email": "taken@example.com"})
    new_response = client.post("/api/v1/auth/register", json={"email": "fresh@example.com"})

    assert duplicate_response.status_code == new_response.status_code == 200
    assert duplicate_response.json() == new_response.json()
    # ...and only the genuinely new registration -- not the already-confirmed
    # duplicate -- got an email.
    assert [to for to, _subject, _body in fake_email_sender.sent] == ["fresh@example.com"]

    unchanged = client.post("/api/v1/auth/login", json={"email": "taken@example.com", "password": "a-password"})
    assert unchanged.status_code == 200, "a confirmed account must be unaffected by a later re-registration"


def test_register_against_a_still_unconfirmed_email_resends_confirmation(client, fake_email_sender):
    """Closes an account-squatting DoS: without this, the first person to
    register an email they don't own -- and never confirm it -- would
    permanently block its real owner from ever registering, since `existing`
    matches on email regardless of confirmation state. Because nobody has
    proven control of the inbox yet, it's safe to let a second registration
    attempt reissue a fresh confirmation link: only whoever actually
    receives that email (the real owner) can turn it into a login, and only
    they ever get to choose the account's password, at confirm time."""
    client.post("/api/v1/auth/register", json={"email": "squatted@example.com"})
    response = client.post("/api/v1/auth/register", json={"email": "squatted@example.com"})
    assert response.status_code == 200

    # Both attempts, still unconfirmed each time, sent a confirmation email.
    assert [to for to, _subject, _body in fake_email_sender.sent] == [
        "squatted@example.com",
        "squatted@example.com",
    ]
    token = _extract_confirmation_token(fake_email_sender.sent[-1][2])
    confirm = client.post("/api/v1/auth/register/confirm", json={"token": token, "password": "real-owner-password"})
    assert confirm.status_code == 204

    real_owner = client.post(
        "/api/v1/auth/login", json={"email": "squatted@example.com", "password": "real-owner-password"}
    )
    assert real_owner.status_code == 200


def test_register_does_not_write_the_password_hash_before_any_confirmation(client, fake_email_sender, app_and_db_url):
    """Regression test for a pre-confirmation account-takeover an earlier
    version of this flow introduced: it wrote existing.password_hash
    synchronously as soon as a re-registration request arrived, with no
    inbox proof at all. /register no longer even accepts a password -- it's
    chosen at /register/confirm, by whoever holds the token (see
    confirm_registration) -- so this checks register() leaves
    User.password_hash completely untouched across repeated, unconfirmed
    registration attempts."""
    from sqlalchemy import select

    from core_api.db import make_engine, make_session_factory
    from core_api.models.identity import User

    _app, db_url = app_and_db_url
    client.post("/api/v1/auth/register", json={"email": "no-overwrite@example.com"})

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    before = session.execute(select(User).where(User.email == "no-overwrite@example.com")).scalar_one().password_hash
    session.close()

    client.post("/api/v1/auth/register", json={"email": "no-overwrite@example.com"})

    session = make_session_factory(engine)()
    after = session.execute(select(User).where(User.email == "no-overwrite@example.com")).scalar_one().password_hash
    session.close()

    assert after == before, "a re-registration must not touch password_hash before any token is confirmed"


def test_register_invalidates_an_earlier_unconfirmed_registrations_token(client, fake_email_sender):
    """A later re-registration attempt against the same still-unconfirmed
    email must burn the earlier attempt's confirmation link, not leave it
    sitting alongside the new one -- otherwise whichever of the two emails
    in the owner's inbox gets clicked second would silently confirm using a
    stale link. Mirrors the outstanding-token invalidation already used by
    password_reset and account.py's email change."""
    client.post("/api/v1/auth/register", json={"email": "stale-link@example.com"})
    first_token = _extract_confirmation_token(fake_email_sender.sent[0][2])

    client.post("/api/v1/auth/register", json={"email": "stale-link@example.com"})
    second_token = _extract_confirmation_token(fake_email_sender.sent[1][2])

    stale = client.post(
        "/api/v1/auth/register/confirm", json={"token": first_token, "password": "first-password"}
    )
    assert stale.status_code == 400
    assert stale.json()["code"] == "invalid_token"

    fresh = client.post(
        "/api/v1/auth/register/confirm", json={"token": second_token, "password": "second-password"}
    )
    assert fresh.status_code == 204


def test_register_confirm_rejects_empty_password(client, fake_email_sender):
    """A bare `str` field accepted "" — and the resulting account could then log
    in with an empty password."""
    token = _register_and_get_token(client, fake_email_sender, "empty@example.com")
    response = client.post("/api/v1/auth/register/confirm", json={"token": token, "password": ""})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_register_confirm_rejects_password_shorter_than_eight_characters(client, fake_email_sender):
    token = _register_and_get_token(client, fake_email_sender, "short@example.com")
    response = client.post("/api/v1/auth/register/confirm", json={"token": token, "password": "7chars!"})
    assert response.status_code == 422


def test_register_confirm_accepts_exactly_eight_character_password(client, fake_email_sender):
    token = _register_and_get_token(client, fake_email_sender, "eight@example.com")
    response = client.post("/api/v1/auth/register/confirm", json={"token": token, "password": "8chars!!"})
    assert response.status_code == 204


def test_register_confirm_rejects_password_longer_than_bcrypts_72_byte_limit(client, fake_email_sender):
    """bcrypt silently truncates at 72 bytes, so accepting a longer password
    would imply entropy the hash never actually sees."""
    token = _register_and_get_token(client, fake_email_sender, "long@example.com")
    response = client.post("/api/v1/auth/register/confirm", json={"token": token, "password": "x" * 73})
    assert response.status_code == 422

    at_limit_token = _register_and_get_token(client, fake_email_sender, "atlimit@example.com")
    at_limit = client.post(
        "/api/v1/auth/register/confirm", json={"token": at_limit_token, "password": "x" * 72}
    )
    assert at_limit.status_code == 204


def test_register_confirm_rejects_unknown_token(client):
    response = client.post(
        "/api/v1/auth/register/confirm", json={"token": "never-issued", "password": "a-password"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_register_confirm_rejects_reused_token(client, fake_email_sender):
    client.post("/api/v1/auth/register", json={"email": "reuse@example.com"})
    token = _extract_confirmation_token(fake_email_sender.sent[0][2])
    client.post("/api/v1/auth/register/confirm", json={"token": token, "password": "a-password"})

    reused = client.post("/api/v1/auth/register/confirm", json={"token": token, "password": "a-password"})
    assert reused.status_code == 400
    assert reused.json()["code"] == "invalid_token"


def test_register_confirm_rejects_expired_token(client, app_and_db_url):
    import datetime

    from sqlalchemy import select

    from core_api.db import make_engine, make_session_factory
    from core_api.models.identity import EmailConfirmationToken, User
    from core_api.security import hash_token

    _app, db_url = app_and_db_url
    client.post("/api/v1/auth/register", json={"email": "expired-confirm@example.com"})

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.execute(select(User).where(User.email == "expired-confirm@example.com")).scalar_one()
    session.add(
        EmailConfirmationToken(
            user_id=user.id,
            token_hash=hash_token("expired-token"),
            expires_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1),
        )
    )
    session.commit()
    session.close()

    response = client.post(
        "/api/v1/auth/register/confirm", json={"token": "expired-token", "password": "a-password"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_register_confirm_rejects_a_racing_double_submit(client, fake_email_sender, monkeypatch):
    """Same double-submit race as password_reset/email-change confirm: two
    overlapping confirms for the same token must not both succeed. A
    `threading.Barrier` forces the two requests' writes to overlap
    deterministically, standing in for real concurrent HTTP requests."""
    import threading

    from core_api.routers import auth as auth_module

    client.post("/api/v1/auth/register", json={"email": "confirm-race@example.com"})
    token = _extract_confirmation_token(fake_email_sender.sent[0][2])

    barrier = threading.Barrier(2)
    original_hash_token = auth_module.hash_token

    def synced_hash_token(raw_token):
        barrier.wait()
        return original_hash_token(raw_token)

    monkeypatch.setattr(auth_module, "hash_token", synced_hash_token)

    results: list[int] = []

    def attempt() -> None:
        response = client.post(
            "/api/v1/auth/register/confirm", json={"token": token, "password": "a-password"}
        )
        results.append(response.status_code)

    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [204, 400], "exactly one racer should win; the other must be cleanly rejected"
