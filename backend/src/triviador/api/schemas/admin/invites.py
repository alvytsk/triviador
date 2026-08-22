from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IssueInvitesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=500)
    expires_in_hours: int = Field(default=168, ge=1, le=8760)


class IssuedInvite(BaseModel):
    """Carries `code`. This model appears in exactly one response and
    never in a listing — see `InviteView`."""

    model_config = ConfigDict(extra="forbid")

    id: str
    code: str
    expires_at: datetime


class InviteView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["pending", "used", "revoked", "expired"]
    expires_at: datetime
    used_by: str | None
