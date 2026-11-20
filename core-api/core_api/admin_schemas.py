from __future__ import annotations

from pydantic import BaseModel, EmailStr, field_validator

from core_api.models.identity import SiteAccessRole, SiteRole
from core_api.schemas import DisplayName


class AdminUserSite(BaseModel):
    site_id: int
    site_name: str
    role: SiteRole


class AdminUserOut(BaseModel):
    """A user as an administrator sees them. Secrets appear only as has_* flags."""

    id: int
    email: str
    display_name: str | None
    is_admin: bool
    has_avatar: bool
    totp_enabled: bool
    email_confirmed: bool
    has_amber_api_key: bool
    sites: list[AdminUserSite]


class AdminUserPatch(BaseModel):
    """Fields left out of the body are not changed. display_name null clears it."""

    display_name: DisplayName | None = None
    email: EmailStr | None = None
    is_admin: bool | None = None

    @field_validator("email", "is_admin")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Runs only on values present in the body, since defaults are not validated.
        if value is None:
            raise ValueError("must not be null")
        return value


class AdminSiteGrantIn(BaseModel):
    """Owner is not a grant, so only SiteAccessRole values are accepted."""

    role: SiteAccessRole
