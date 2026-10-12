from __future__ import annotations

import datetime
import time

import pyotp
import pytest
import re

from sqlalchemy import select

from core_api.db import make_engine, make_session_factory
from core_api.errors import AppError
from core_api.models.identity import EmailChangeToken, RecoveryCode, User
from core_api.routers.account import confirm_totp_reenroll
from core_api.schemas import ConfirmTotpReenrollIn
from core_api.security import create_access_token, generate_totp_secret, hash_password, hash_token, verify_totp_code


def make_user(db_url: str, amber_api_key: str | None = None) -> int:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = User(email="acct-owner@example.com", password_hash="x", amber_api_key=amber_api_key)
    session.add(user)
    session.commit()
    user_id = user.id
    session.close()
    return user_id


def auth(user_id: int) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_get_me_reports_no_amber_key_when_unset(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.get("/api/v1/users/me", headers=auth(user_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == "acct-owner@example.com"
    assert body["has_amber_api_key"] is False
    assert "amber_api_key" not in body


def test_get_me_reports_amber_key_present_without_revealing_it(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url, amber_api_key="psk_real_secret")

    response = client.get("/api/v1/users/me", headers=auth(user_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_amber_api_key"] is True
    assert "psk_real_secret" not in response.text


def test_get_me_requires_auth(client, app_and_db_url):
    response = client.get("/api/v1/users/me")
    assert response.status_code in (401, 422)


def test_patch_me_sets_amber_api_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.patch(
        "/api/v1/users/me", json={"amber_api_key": "psk_new_secret"}, headers=auth(user_id)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_amber_api_key"] is True
    assert "psk_new_secret" not in response.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.amber_api_key == "psk_new_secret"


def test_patch_me_rejects_empty_amber_api_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.patch("/api/v1/users/me", json={"amber_api_key": ""}, headers=auth(user_id))
    assert response.status_code == 422


def test_patch_me_overwrites_an_existing_amber_api_key(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url, amber_api_key="psk_old_secret")

    response = client.patch(
        "/api/v1/users/me", json={"amber_api_key": "psk_replacement"}, headers=auth(user_id)
    )
    assert response.status_code == 200, response.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.amber_api_key == "psk_replacement"


def make_enrolled_user(db_url: str, *, password: str = "correct-password") -> tuple[int, str]:
    """Returns (user_id, totp_secret) for a user who is already fully TOTP-enrolled,
    matching the state every endpoint in this plan expects its caller to be in."""
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    secret = generate_totp_secret()
    u = User(email="enrolled@example.com", password_hash=hash_password(password), totp_secret=secret, totp_enabled=True)
    session.add(u)
    session.commit()
    user_id = u.id
    session.close()
    return user_id, secret


def test_change_password_with_correct_current_password_and_totp_succeeds(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/password",
        json={
            "current_password": "correct-password",
            "new_password": "new-password-123",
            "totp_code": pyotp.TOTP(secret).now(),
        },
        headers=auth(user_id),
    )
    assert response.status_code == 204

    login = client.post(
        "/api/v1/auth/login", json={"email": "enrolled@example.com", "password": "new-password-123"}
    )
    assert login.status_code == 200
    assert login.json()["status"] == "totp_required"


def test_change_password_rejects_wrong_current_password(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "wrong-password", "new_password": "new-password-123", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_change_password_rejects_wrong_totp_code_even_with_correct_password(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, _secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "correct-password", "new_password": "new-password-123", "totp_code": "000000"},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_code"


def test_change_password_requires_auth(client, app_and_db_url):
    response = client.post(
        "/api/v1/users/me/password",
        json={"current_password": "x", "new_password": "new-password-123", "totp_code": "000000"},
    )
    assert response.status_code in (401, 422)


def _extract_confirm_token(body: str) -> str:
    match = re.search(r"token=([^\s]+)", body)
    assert match is not None
    return match.group(1)


def test_request_email_change_sends_link_to_the_new_address(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "new@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 200, response.text
    assert len(fake_email_sender.sent) == 1
    to, _subject, body = fake_email_sender.sent[0]
    assert to == "new@example.com"
    assert "confirm-email-change?token=" in body


def test_request_email_change_rejects_wrong_current_password(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "new@example.com", "current_password": "wrong", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert fake_email_sender.sent == []


def test_request_email_change_rejects_wrong_totp_code_even_with_correct_password(client, app_and_db_url, fake_email_sender):
    """The step-up gate is BOTH factors: holding the password (and a valid
    access_token) is not enough without the second factor."""
    _, db_url = app_and_db_url
    user_id, _secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "new@example.com", "current_password": "correct-password", "totp_code": "000000"},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_code"
    assert fake_email_sender.sent == []


def test_step_up_rejects_a_replayed_totp_code(client, app_and_db_url, fake_email_sender):
    """_verify_step_up gates every security-sensitive mutation in this file
    (password change, email change, TOTP re-enrollment) -- a TOTP code
    observed once must not be replayable across any of them within its own
    ~90s valid_window."""
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)
    code = pyotp.TOTP(secret).now()

    first = client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "first@example.com", "current_password": "correct-password", "totp_code": code},
        headers=auth(user_id),
    )
    assert first.status_code == 200, first.text

    replayed = client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "second@example.com", "current_password": "correct-password", "totp_code": code},
        headers=auth(user_id),
    )
    assert replayed.status_code == 401
    assert replayed.json()["code"] == "invalid_code"


def test_a_second_email_change_request_invalidates_the_first_requests_token(client, app_and_db_url, fake_email_sender):
    """_invalidate_outstanding_email_change_tokens firing on a second /request
    from the SAME user: the older unconfirmed link must stop working, so a
    stale email in the inbox can't later move the account somewhere the user
    has since changed their mind about."""
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)
    totp = pyotp.TOTP(secret)
    # Distinct steps (not totp.now() twice): the anti-replay guard added for
    # core-api finding 7 would otherwise reject the second request's code as
    # a replay of the first, which isn't what this test is about.
    for i, new_email in enumerate(("first-choice@example.com", "second-choice@example.com")):
        request = client.post(
            "/api/v1/users/me/email/change/request",
            json={
                "new_email": new_email,
                "current_password": "correct-password",
                "totp_code": totp.at(time.time() + i * totp.interval),
            },
            headers=auth(user_id),
        )
        assert request.status_code == 200, request.text
    assert len(fake_email_sender.sent) == 2
    first_token = _extract_confirm_token(fake_email_sender.sent[0][2])

    response = client.post("/api/v1/users/me/email/change/confirm", json={"token": first_token})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_request_email_change_rejects_an_email_already_in_use(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)
    client.post("/api/v1/auth/register", json={"email": "taken@example.com"})

    response = client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "taken@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


def test_confirm_email_change_updates_email_and_allows_login_with_it(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)
    client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "new@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    token = _extract_confirm_token(fake_email_sender.sent[0][2])

    confirm = client.post("/api/v1/users/me/email/change/confirm", json={"token": token})
    assert confirm.status_code == 204

    login = client.post("/api/v1/auth/login", json={"email": "new@example.com", "password": "correct-password"})
    assert login.status_code == 200


def test_confirm_email_change_rejects_reused_token(client, app_and_db_url, fake_email_sender):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)
    client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "new@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    token = _extract_confirm_token(fake_email_sender.sent[0][2])
    client.post("/api/v1/users/me/email/change/confirm", json={"token": token})

    reused = client.post("/api/v1/users/me/email/change/confirm", json={"token": token})
    assert reused.status_code == 400
    assert reused.json()["code"] == "invalid_token"


def test_confirm_email_change_rejects_a_racing_double_submit_instead_of_letting_the_second_commit_win(
    client, app_and_db_url, fake_email_sender, monkeypatch
):
    """Same double-submit race as password_reset's confirm: two overlapping
    confirms for the same token must not both succeed. Without an atomic
    claim, both requests' SELECT sees used_at still NULL, so both proceed to
    set a (different) new email and commit -- the second commit silently
    wins. A `threading.Barrier` forces the two requests' writes to overlap
    deterministically, standing in for real concurrent HTTP requests."""
    import threading

    from core_api.routers import account as account_module

    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)
    client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "race@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    token = _extract_confirm_token(fake_email_sender.sent[0][2])

    barrier = threading.Barrier(2)
    original_hash_token = account_module.hash_token

    def synced_hash_token(raw_token):
        barrier.wait()
        return original_hash_token(raw_token)

    monkeypatch.setattr(account_module, "hash_token", synced_hash_token)

    results: list[int] = []

    def attempt() -> None:
        response = client.post("/api/v1/users/me/email/change/confirm", json={"token": token})
        results.append(response.status_code)

    t1 = threading.Thread(target=attempt)
    t2 = threading.Thread(target=attempt)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [204, 400], "exactly one racer should win; the other must be cleanly rejected"


def test_confirm_email_change_rejects_unknown_token(client):
    response = client.post("/api/v1/users/me/email/change/confirm", json={"token": "never-issued"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_confirm_email_change_rejects_expired_token(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, _secret = make_enrolled_user(db_url)

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    session.add(
        EmailChangeToken(
            user_id=user_id,
            new_email="new@example.com",
            token_hash=hash_token("expired-token"),
            expires_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1),
        )
    )
    session.commit()
    session.close()

    response = client.post("/api/v1/users/me/email/change/confirm", json={"token": "expired-token"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_two_concurrent_requests_for_the_same_new_email_only_one_confirm_succeeds(client, app_and_db_url, fake_email_sender):
    """The Review Focus race: two different accounts both request a change to
    the same still-available address. Both /request calls succeed (the
    address isn't taken yet), but only the first /confirm may actually claim
    it -- the second must fail at commit time, not silently overwrite."""
    _, db_url = app_and_db_url
    user_a_id, secret_a = make_enrolled_user(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user_b = User(email="b@example.com", password_hash=hash_password("correct-password"), totp_secret=generate_totp_secret(), totp_enabled=True)
    session.add(user_b)
    session.commit()
    user_b_id, secret_b = user_b.id, user_b.totp_secret
    session.close()

    client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "shared@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret_a).now()},
        headers=auth(user_a_id),
    )
    token_a = _extract_confirm_token(fake_email_sender.sent[0][2])
    client.post(
        "/api/v1/users/me/email/change/request",
        json={"new_email": "shared@example.com", "current_password": "correct-password", "totp_code": pyotp.TOTP(secret_b).now()},
        headers=auth(user_b_id),
    )
    token_b = _extract_confirm_token(fake_email_sender.sent[1][2])

    first = client.post("/api/v1/users/me/email/change/confirm", json={"token": token_a})
    assert first.status_code == 204

    second = client.post("/api/v1/users/me/email/change/confirm", json={"token": token_b})
    assert second.status_code == 409
    assert second.json()["code"] == "email_taken"


def test_reenroll_start_returns_a_new_secret_without_touching_the_live_one(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, old_secret = make_enrolled_user(db_url)
    old_totp = pyotp.TOTP(old_secret)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": old_totp.now()},
        headers=auth(user_id),
    )
    assert response.status_code == 200, response.text
    new_secret = response.json()["secret"]
    assert new_secret != old_secret

    # The OLD authenticator must still work for login -- the live totp_secret
    # must be untouched until confirm. A LATER code, not the one just spent
    # on the step-up above: the anti-replay guard added for core-api
    # finding 7 would otherwise reject reusing that exact code.
    login = client.post("/api/v1/auth/login", json={"email": "enrolled@example.com", "password": "correct-password"})
    mfa_token = login.json()["mfa_token"]
    verify = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": old_totp.at(time.time() + old_totp.interval)},
        headers={"Authorization": f"Bearer {mfa_token}"},
    )
    assert verify.status_code == 200


def test_reenroll_start_rejects_wrong_current_password(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "wrong", "totp_code": pyotp.TOTP(secret).now()},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_reenroll_start_rejects_wrong_totp_code_even_with_correct_password(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, _secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": "000000"},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_code"

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.pending_totp_secret is None


def test_reenroll_confirm_without_a_prior_start_is_rejected(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, _secret = make_enrolled_user(db_url)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/confirm", json={"code": "000000"}, headers=auth(user_id)
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_state"


def test_reenroll_confirm_with_correct_code_promotes_the_new_secret_and_returns_recovery_codes(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, old_secret = make_enrolled_user(db_url)

    start = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": pyotp.TOTP(old_secret).now()},
        headers=auth(user_id),
    )
    new_secret = start.json()["secret"]

    confirm = client.post(
        "/api/v1/users/me/totp/reenroll/confirm",
        json={"code": pyotp.TOTP(new_secret).now()},
        headers=auth(user_id),
    )
    assert confirm.status_code == 200
    recovery_codes = confirm.json()["recovery_codes"]
    assert len(recovery_codes) == 10
    assert len(set(recovery_codes)) == 10

    # The OLD authenticator no longer works -- the new secret is now live.
    login = client.post("/api/v1/auth/login", json={"email": "enrolled@example.com", "password": "correct-password"})
    mfa_token = login.json()["mfa_token"]
    old_code_attempt = client.post(
        "/api/v1/auth/totp/verify", json={"code": pyotp.TOTP(old_secret).now()}, headers={"Authorization": f"Bearer {mfa_token}"}
    )
    assert old_code_attempt.status_code == 401


def test_reenroll_confirm_invalidates_previous_unused_recovery_codes(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, old_secret = make_enrolled_user(db_url)
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    session.add(RecoveryCode(user_id=user_id, code_hash=hash_token("old-recovery-code")))
    session.commit()
    session.close()

    start = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": pyotp.TOTP(old_secret).now()},
        headers=auth(user_id),
    )
    new_secret = start.json()["secret"]
    client.post(
        "/api/v1/users/me/totp/reenroll/confirm",
        json={"code": pyotp.TOTP(new_secret).now()},
        headers=auth(user_id),
    )

    login = client.post("/api/v1/auth/login", json={"email": "enrolled@example.com", "password": "correct-password"})
    mfa_token = login.json()["mfa_token"]
    old_recovery_attempt = client.post(
        "/api/v1/auth/totp/verify", json={"code": "old-recovery-code"}, headers={"Authorization": f"Bearer {mfa_token}"}
    )
    assert old_recovery_attempt.status_code == 401


def _start_reenroll(client, user_id: int, current_totp_secret: str) -> str:
    """Runs the step-up gate and returns the new pending secret."""
    start = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": pyotp.TOTP(current_totp_secret).now()},
        headers=auth(user_id),
    )
    assert start.status_code == 200, start.text
    return start.json()["secret"]


def _unused_recovery_codes(db_url: str, user_id: int) -> list[RecoveryCode]:
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    rows = (
        session.execute(
            select(RecoveryCode).where(RecoveryCode.user_id == user_id, RecoveryCode.used_at.is_(None))
        )
        .scalars()
        .all()
    )
    session.close()
    return list(rows)


def test_reenroll_confirm_rejects_a_wrong_code_against_a_valid_pending_secret(client, app_and_db_url):
    """Server-side coverage for the confirm step itself: a genuine pending
    secret exists, but the submitted code doesn't match it."""
    _, db_url = app_and_db_url
    user_id, old_secret = make_enrolled_user(db_url)
    _start_reenroll(client, user_id, old_secret)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/confirm", json={"code": "000000"}, headers=auth(user_id)
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_code"

    # The pending secret survives a failed confirm -- the user can retry with
    # the next code from the authenticator they just scanned.
    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.pending_totp_secret is not None
    assert user.totp_secret == old_secret


def test_reenroll_confirm_twice_with_the_same_code_rejects_the_second(client, app_and_db_url):
    """The visible half of the double-submit: once the first confirm has
    promoted the secret there is no replacement in progress any more, so the
    second (verify_totp_code has no replay guard, so the same 6 digits would
    otherwise pass again) must not mint a second batch of recovery codes."""
    _, db_url = app_and_db_url
    user_id, old_secret = make_enrolled_user(db_url)
    new_secret = _start_reenroll(client, user_id, old_secret)
    code = pyotp.TOTP(new_secret).now()

    first = client.post("/api/v1/users/me/totp/reenroll/confirm", json={"code": code}, headers=auth(user_id))
    assert first.status_code == 200, first.text

    second = client.post("/api/v1/users/me/totp/reenroll/confirm", json={"code": code}, headers=auth(user_id))
    assert second.status_code == 400
    assert second.json()["code"] == "invalid_state"

    live_codes = _unused_recovery_codes(db_url, user_id)
    assert len(live_codes) == 10
    assert {hash_token(c) for c in first.json()["recovery_codes"]} == {c.code_hash for c in live_codes}


def test_reenroll_confirm_that_loses_the_promotion_race_mints_no_second_batch(client, app_and_db_url):
    """The race itself, not just its aftermath: a request that read
    pending_totp_secret BEFORE a concurrent confirm promoted it still holds a
    matching in-memory secret and passes both up-front checks. The conditional
    UPDATE is what stops it -- it matches zero rows, so the loser is rejected
    instead of also generating 10 recovery codes the user never sees.

    Driven by calling the handler directly with that stale User, because
    TestClient cannot overlap two in-flight requests."""
    _, db_url = app_and_db_url
    user_id, old_secret = make_enrolled_user(db_url)
    new_secret = _start_reenroll(client, user_id, old_secret)

    # The row as the losing request read it, kept detached so the winner's
    # commit can't refresh it out from under us.
    engine = make_engine(db_url)
    session_factory = make_session_factory(engine)
    reader = session_factory()
    stale_user = reader.get(User, user_id)
    assert stale_user.pending_totp_secret == new_secret
    reader.expunge(stale_user)
    reader.close()

    winner = client.post(
        "/api/v1/users/me/totp/reenroll/confirm",
        json={"code": pyotp.TOTP(new_secret).now()},
        headers=auth(user_id),
    )
    assert winner.status_code == 200, winner.text

    loser_session = session_factory()
    try:
        with pytest.raises(AppError) as excinfo:
            confirm_totp_reenroll(
                ConfirmTotpReenrollIn(code=pyotp.TOTP(new_secret).now()),
                current_user=stale_user,
                db=loser_session,
            )
    finally:
        loser_session.close()
    assert excinfo.value.status_code == 400
    assert excinfo.value.code == "invalid_state"

    live_codes = _unused_recovery_codes(db_url, user_id)
    assert len(live_codes) == 10
    assert {hash_token(c) for c in winner.json()["recovery_codes"]} == {c.code_hash for c in live_codes}


def enroll_through_the_real_flow(
    client, db_url: str, *, email: str = "lost-device@example.com", password: str = "correct-password"
) -> tuple[int, str, list[str]]:
    """Registers and fully TOTP-enrolls a user through the real endpoints, so
    the recovery codes under test are exactly the ones a user would be
    holding. Returns (user_id, totp_secret, recovery_codes)."""
    client.post("/api/v1/auth/register", json={"email": email})
    confirm_token = _extract_confirm_token(client.app.state.email_sender.sent[-1][2])
    confirm_registration = client.post(
        "/api/v1/auth/register/confirm", json={"token": confirm_token, "password": password}
    )
    assert confirm_registration.status_code == 204
    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    setup_token = login.json()["setup_token"]
    start = client.post("/api/v1/auth/totp/setup/start", headers={"Authorization": f"Bearer {setup_token}"})
    secret = start.json()["secret"]
    confirm = client.post(
        "/api/v1/auth/totp/setup/confirm",
        json={"code": pyotp.TOTP(secret).now()},
        headers={"Authorization": f"Bearer {setup_token}"},
    )
    assert confirm.status_code == 200, confirm.text

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user_id = session.execute(select(User).where(User.email == email)).scalar_one().id
    session.close()
    return user_id, secret, confirm.json()["recovery_codes"]


def test_reenroll_start_accepts_an_unused_recovery_code_as_the_second_factor(client, app_and_db_url):
    """The actual lost-device path the Account page advertises: the user got in
    with a recovery code and has no working authenticator, so the step-up gate
    must accept a recovery code as the second factor -- otherwise replacing the
    authenticator is a permanent dead end."""
    _, db_url = app_and_db_url
    user_id, _secret, recovery_codes = enroll_through_the_real_flow(client, db_url)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": recovery_codes[0]},
        headers=auth(user_id),
    )
    assert response.status_code == 200, response.text
    assert response.json()["secret"]

    # Using it here spends it, exactly as using one to log in does.
    live_codes = _unused_recovery_codes(db_url, user_id)
    assert len(live_codes) == 9
    assert hash_token(recovery_codes[0]) not in {c.code_hash for c in live_codes}


def test_step_up_rejects_a_recovery_code_that_has_already_been_used(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id, _secret, recovery_codes = enroll_through_the_real_flow(client, db_url)
    first = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": recovery_codes[0]},
        headers=auth(user_id),
    )
    assert first.status_code == 200, first.text

    reused = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "correct-password", "totp_code": recovery_codes[0]},
        headers=auth(user_id),
    )
    assert reused.status_code == 401
    assert reused.json()["code"] == "invalid_code"


def test_step_up_still_requires_the_password_alongside_a_recovery_code(client, app_and_db_url):
    """Broadening the second factor must not weaken the first: a valid
    recovery code on its own is not a step-up."""
    _, db_url = app_and_db_url
    user_id, _secret, recovery_codes = enroll_through_the_real_flow(client, db_url)

    response = client.post(
        "/api/v1/users/me/totp/reenroll/start",
        json={"current_password": "wrong-password", "totp_code": recovery_codes[0]},
        headers=auth(user_id),
    )
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"

    # The rejected attempt must not have spent the code.
    assert len(_unused_recovery_codes(db_url, user_id)) == 10


def test_get_me_reports_null_display_name_when_unset(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.get("/api/v1/users/me", headers=auth(user_id))
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] is None


def test_patch_profile_sets_display_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.patch(
        "/api/v1/users/me/profile", json={"display_name": "Ada Lovelace"}, headers=auth(user_id)
    )
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Ada Lovelace"

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.display_name == "Ada Lovelace"


def test_patch_profile_rejects_empty_display_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.patch("/api/v1/users/me/profile", json={"display_name": ""}, headers=auth(user_id))
    assert response.status_code == 422


def test_patch_profile_requires_auth(client, app_and_db_url):
    response = client.patch("/api/v1/users/me/profile", json={"display_name": "Ada Lovelace"})
    assert response.status_code in (401, 422)


def test_patch_profile_rejects_whitespace_only_display_name(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.patch("/api/v1/users/me/profile", json={"display_name": "   "}, headers=auth(user_id))
    assert response.status_code == 422


def test_patch_profile_strips_surrounding_whitespace(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.patch(
        "/api/v1/users/me/profile", json={"display_name": "  Ada Lovelace  "}, headers=auth(user_id)
    )
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == "Ada Lovelace"

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.get(User, user_id)
    session.close()
    assert user.display_name == "Ada Lovelace"


def test_patch_profile_rejects_display_name_over_max_length(client, app_and_db_url):
    _, db_url = app_and_db_url
    user_id = make_user(db_url)

    response = client.patch(
        "/api/v1/users/me/profile", json={"display_name": "x" * 101}, headers=auth(user_id)
    )
    assert response.status_code == 422
