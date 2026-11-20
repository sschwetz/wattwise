from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

SettingValue = Annotated[str, Field(max_length=500)]


class ProviderFieldOut(BaseModel):
    name: str
    label: str
    secret: bool
    required: bool
    # Always null for a secret field: the stored value never leaves the server.
    value: str | None
    is_set: bool


class ProviderOut(BaseModel):
    provider: str
    label: str
    available: bool
    is_active: bool
    fields: list[ProviderFieldOut]


class ProviderSettingsIn(BaseModel):
    settings: dict[str, SettingValue]


class TestQueuedOut(BaseModel):
    queued: bool = True


class OutboxSummaryOut(BaseModel):
    pending: int
    sent_24h: int
    failed_24h: int
    last_error: str | None
