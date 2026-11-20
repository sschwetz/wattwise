"""The providers this actor implements. Adding one is a module next to
smtp2go.py and an entry in this list."""

from __future__ import annotations

from mail_actor.providers import smtp2go
from mail_actor.providers.base import MailProvider

PROVIDERS: list[MailProvider] = [smtp2go]
