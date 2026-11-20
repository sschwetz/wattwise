"""Endpoints the mail actor calls: declare its providers, read the active
provider's settings, and claim and finish queued mail. Every route needs the
shared actor key with `X-Actor-Provider: mail`."""

from __future__ import annotations

import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from core_api.deps import get_db_session, require_mail_actor
from core_api.models.mail import MailProvider
from core_api.outbox import claim_outbox, finish_outbox

router = APIRouter(prefix="/actor/mail", tags=["actor-mail"], dependencies=[Depends(require_mail_actor)])

Name = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]
Label = Annotated[str, Field(min_length=1, max_length=200)]


def _no_duplicates(names: list[str], what: str) -> None:
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate {what}")


class ProviderField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    label: Label
    secret: bool
    required: bool


class ProviderDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Name
    label: Label
    fields: Annotated[list[ProviderField], Field(max_length=30)]

    @field_validator("fields")
    @classmethod
    def _unique_fields(cls, value: list[ProviderField]) -> list[ProviderField]:
        _no_duplicates([f.name for f in value], "field name")
        return value


class ProviderDeclarations(RootModel[Annotated[list[ProviderDeclaration], Field(max_length=20)]]):
    @field_validator("root")
    @classmethod
    def _unique_providers(cls, value: list[ProviderDeclaration]) -> list[ProviderDeclaration]:
        _no_duplicates([d.provider for d in value], "provider")
        return value


class ActiveProviderOut(BaseModel):
    provider: str
    settings: dict[str, str]


class ClaimedEmailOut(BaseModel):
    id: int
    to_address: str
    subject: str
    body: str


class OutboxResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["sent", "failed"]
    error: str | None = None


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


@router.put("/providers", status_code=status.HTTP_204_NO_CONTENT)
def declare_providers(payload: ProviderDeclarations, db: Session = Depends(get_db_session)) -> None:
    """Upsert each declared provider. An existing row keeps its settings and
    active flag; providers the actor no longer lists are left in place."""
    now = _now()
    for declared in payload.root:
        fields = [f.model_dump() for f in declared.fields]
        row = db.get(MailProvider, declared.provider)
        if row is None:
            row = MailProvider(provider=declared.provider, is_active=False)
            db.add(row)
        row.label = declared.label
        row.fields = fields
        row.declared_at = now
        row.updated_at = now
    db.commit()


@router.get("/active", response_model=ActiveProviderOut, responses={204: {"description": "No provider is active"}})
def active_provider(db: Session = Depends(get_db_session)) -> ActiveProviderOut | Response:
    row = db.scalars(select(MailProvider).where(MailProvider.is_active)).first()
    if row is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return ActiveProviderOut(provider=row.provider, settings=row.settings or {})


@router.post("/outbox/claim", response_model=list[ClaimedEmailOut])
def claim(
    limit: Annotated[int, Query(ge=1, le=50)],
    db: Session = Depends(get_db_session),
) -> list[ClaimedEmailOut]:
    rows = claim_outbox(db, limit, _now())
    out = [ClaimedEmailOut(id=r.id, to_address=r.to_address, subject=r.subject, body=r.body) for r in rows]
    db.commit()
    return out


@router.patch("/outbox/{id}", status_code=status.HTTP_204_NO_CONTENT)
def finish(
    id: int,
    payload: OutboxResult,
    db: Session = Depends(get_db_session),
) -> None:
    finish_outbox(db, id, payload.outcome, payload.error, _now())
    db.commit()
