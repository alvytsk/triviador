from pydantic import BaseModel, ConfigDict, Field

from triviador.services.identity import UserRole

USERNAME = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9._-]+$")
PASSWORD = Field(min_length=8, max_length=256)
DISPLAY_NAME = Field(min_length=1, max_length=32)


class RedeemRequest(BaseModel):
    """`extra="forbid"` is load-bearing rather than tidy: the field this
    body must never be able to carry is `role`."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=128)
    username: str = USERNAME
    password: str = PASSWORD
    display_name: str = DISPLAY_NAME


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class Me(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    username: str
    display_name: str
    role: UserRole
