from __future__ import annotations

import re
import time


def _extract_token(body: str) -> str:
    match = re.search(r"token=([^\s]+)", body)
    assert match is not None
    return match.group(1)


def _register_and_confirm(client, email: str, password: str) -> None:
    """Registration now requires confirming the emailed link before the
    account can log in (core-api finding 3: closes /register's email_taken
    enumeration oracle). The password is chosen at /register/confirm, not at
    /register -- see auth.confirm_registration. Clears the sent-mail log
    afterward so every test below can keep asserting on
    fake_email_sender.sent[0] as THE password-reset email, not
    registration's own confirmation email."""
    client.post("/api/v1/auth/register", json={"email": email})
    sent = client.app.state.email_sender.sent
    token = _extract_token(sent[-1][2])
    confirm = client.post("/api/v1/auth/register/confirm", json={"token": token, "password": password})
    assert confirm.status_code == 204
    sent.clear()


def test_request_for_existing_email_sends_email_with_reset_link(client, fake_email_sender):
    _register_and_confirm(client, "a@example.com", "old-password")
    response = client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    assert response.status_code == 200
    assert len(fake_email_sender.sent) == 1
    to, _subject, body = fake_email_sender.sent[0]
    assert to == "a@example.com"
    assert "reset-password?token=" in body


def test_request_response_is_identical_for_known_and_unknown_emails(client, fake_email_sender):
    """The anti-enumeration property from the plan's Global Constraints: the
    response body for a REGISTERED email must be byte-identical to the one for
    an unregistered email. (The previous version of this test posted the same
    unknown address twice, so it compared an unknown email against itself and
    never exercised the property at all.)"""
    _register_and_confirm(client, "known@example.com", "old-password")

    known = client.post("/api/v1/auth/password-reset/request", json={"email": "known@example.com"})
    unknown = client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"})

    assert known.status_code == 200
    assert unknown.status_code == 200
    assert known.json() == unknown.json()

    # ...and only the registered address actually got an email, so the
    # indistinguishable responses are not just "nothing happened either way".
    assert [to for to, _subject, _body in fake_email_sender.sent] == ["known@example.com"]


def test_request_still_returns_the_generic_message_when_the_email_send_fails(client, app_and_db_url):
    """A relay outage must not turn this endpoint into an enumeration oracle.
    If send() raises, a KNOWN email would 500 while an unknown one still 200s —
    the exception itself becomes the signal that the address is registered."""

    class ExplodingEmailSender:
        def send(self, to: str, subject: str, body: str) -> None:
            raise OSError("simulated SMTP relay outage")

    app, _db_url = app_and_db_url
    # Register and confirm BEFORE swapping in the exploding sender: this test
    # is about password-reset's own resilience to a relay outage, not
    # registration's -- registration needs the real fake sender to get a
    # confirmation link it can actually claim.
    _register_and_confirm(client, "known@example.com", "old-password")
    app.state.email_sender = ExplodingEmailSender()

    known = client.post("/api/v1/auth/password-reset/request", json={"email": "known@example.com"})
    unknown = client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"})

    assert known.status_code == 200, "a send failure must not surface as a 500"
    assert unknown.status_code == 200
    assert known.json() == unknown.json()


def test_request_for_unknown_email_takes_at_least_as_long_as_the_response_floor(client):
    """Response identity alone isn't enough: without a timing floor, a known
    email pays for a DB insert plus a synchronous email send while an unknown
    one returns almost instantly, so wall-clock time becomes the enumeration
    oracle even though the JSON body is identical."""
    from core_api.routers.password_reset import _MIN_RESPONSE_SECONDS

    started = time.monotonic()
    response = client.post("/api/v1/auth/password-reset/request", json={"email": "nobody@example.com"})
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed >= _MIN_RESPONSE_SECONDS


def test_confirm_with_valid_token_updates_password_and_allows_login(client, fake_email_sender):
    _register_and_confirm(client, "a@example.com", "old-password")
    client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    token = _extract_token(fake_email_sender.sent[0][2])

    confirm = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "new-password"}
    )
    assert confirm.status_code == 204

    login = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "new-password"})
    assert login.status_code == 200
    old_login = client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "old-password"})
    assert old_login.status_code == 401


def test_confirm_rejects_reused_token(client, fake_email_sender):
    _register_and_confirm(client, "a@example.com", "old-password")
    client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    token = _extract_token(fake_email_sender.sent[0][2])
    client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "new-password"})

    reused = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "another-password"}
    )
    assert reused.status_code == 400
    assert reused.json()["code"] == "invalid_token"


def test_confirm_rejects_expired_token(client, fake_email_sender, app_and_db_url):
    import datetime

    from core_api.db import make_engine, make_session_factory
    from core_api.models.identity import PasswordResetToken, User
    from core_api.security import hash_token
    from sqlalchemy import select

    _app, db_url = app_and_db_url
    _register_and_confirm(client, "a@example.com", "old-password")

    engine = make_engine(db_url)
    session = make_session_factory(engine)()
    user = session.execute(select(User).where(User.email == "a@example.com")).scalar_one()
    session.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token("expired-token"),
            expires_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1),
        )
    )
    session.commit()
    session.close()

    response = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": "expired-token", "new_password": "new-password"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_confirm_invalidates_the_users_other_outstanding_tokens(client, fake_email_sender):
    """Completing a reset must burn every outstanding token for that user, not
    just the one presented. Otherwise a user who requested twice (or an attacker
    who requested one first against a known email) leaves the other link live
    for the rest of its 1-hour window, even after the user believes the account
    is secured."""
    _register_and_confirm(client, "a@example.com", "old-password")

    client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    assert len(fake_email_sender.sent) == 2
    first_token = _extract_token(fake_email_sender.sent[0][2])
    second_token = _extract_token(fake_email_sender.sent[1][2])
    assert first_token != second_token

    # Consume the SECOND (more recent) token.
    confirm = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": second_token, "new_password": "new-password"},
    )
    assert confirm.status_code == 204

    # The FIRST token was never presented to /confirm, but must now be dead.
    stale = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": first_token, "new_password": "attacker-password"},
    )
    assert stale.status_code == 400
    assert stale.json()["code"] == "invalid_token"

    # The password really is the one set by the legitimate confirm.
    assert client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "new-password"}
    ).status_code == 200
    assert client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "attacker-password"}
    ).status_code == 401


def test_confirm_rejects_a_racing_double_submit_instead_of_letting_the_second_commit_win(
    client, fake_email_sender, monkeypatch
):
    """Two overlapping confirms for the same token (e.g. a retried request)
    must not both succeed. Without an atomic claim, both requests' SELECT sees
    used_at still NULL, so both proceed to set the password and commit — the
    second commit silently wins, overwriting the first with no error to
    either caller. A `threading.Barrier` forces the two requests' writes to
    overlap deterministically, standing in for real concurrent HTTP requests."""
    import threading

    from core_api.routers import password_reset as password_reset_module

    _register_and_confirm(client, "race@example.com", "old-password")
    client.post("/api/v1/auth/password-reset/request", json={"email": "race@example.com"})
    token = _extract_token(fake_email_sender.sent[0][2])

    barrier = threading.Barrier(2)
    original_hash_token = password_reset_module.hash_token

    def synced_hash_token(raw_token):
        # Runs at the start of each request's token lookup, before either has
        # read or written anything — forces both requests to race into their
        # SELECT/claim at the same time instead of running fully sequentially.
        barrier.wait()
        return original_hash_token(raw_token)

    monkeypatch.setattr(password_reset_module, "hash_token", synced_hash_token)

    results: list[int] = []

    def attempt(new_password: str) -> None:
        response = client.post(
            "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": new_password}
        )
        results.append(response.status_code)

    t1 = threading.Thread(target=attempt, args=("password-one!",))
    t2 = threading.Thread(target=attempt, args=("password-two!",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [204, 400], "exactly one racer should win; the other must be cleanly rejected"

    login_one = client.post("/api/v1/auth/login", json={"email": "race@example.com", "password": "password-one!"})
    login_two = client.post("/api/v1/auth/login", json={"email": "race@example.com", "password": "password-two!"})
    # Exactly one of the two passwords must actually be live — whichever
    # response was the 204 — never both, and never neither.
    assert sorted([login_one.status_code, login_two.status_code]) == [200, 401]


def test_confirm_rejects_unknown_token(client):
    response = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": "never-issued", "new_password": "new-password"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_token"


def test_confirm_enforces_the_same_password_length_rules_as_register(client, fake_email_sender):
    """A reset must not be a back door around the length minimum that
    /auth/register enforces."""
    _register_and_confirm(client, "a@example.com", "old-password")
    client.post("/api/v1/auth/password-reset/request", json={"email": "a@example.com"})
    token = _extract_token(fake_email_sender.sent[0][2])

    empty = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": ""})
    assert empty.status_code == 422

    too_short = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "7chars!"}
    )
    assert too_short.status_code == 422

    too_long = client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "x" * 73}
    )
    assert too_long.status_code == 422

    # The token survived the rejected attempts and still works.
    ok = client.post("/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": "8chars!!"})
    assert ok.status_code == 204
