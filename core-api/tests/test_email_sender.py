from __future__ import annotations

from core_api.email_sender import FakeEmailSender


def test_fake_email_sender_records_sends():
    sender = FakeEmailSender()
    sender.send(to="a@example.com", subject="Subject", body="Body")
    assert sender.sent == [("a@example.com", "Subject", "Body")]
