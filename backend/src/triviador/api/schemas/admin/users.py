"""§10.5's user half: the admin listing, and a role change's body."""

from pydantic import BaseModel, ConfigDict

from triviador.services.identity import UserRole


class UserView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    username: str
    display_name: str
    role: UserRole
    is_active: bool


class SetRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole
