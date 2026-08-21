"""§10.5's user half. Two rules, both enforced under a lock, both 409.

`self_target` covers deactivating yourself; the spec's other rule — a
demotion that would leave zero active admins — is `last_admin`, and it
applies even when the demoted user is the caller (Task 11's
`test_the_last_admin_cannot_be_demoted` demotes the admin's own account).
The two never collide: self-demotion is only blocked when it is *also* the
last-admin case, so `self_target` is not raised for `/role` at all.
"""

from fastapi import APIRouter

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.users import SetRoleRequest, UserView
from triviador.domain.ids import UserId
from triviador.services.admin import SetRoleOutcome
from triviador.services.identity import UserRecord

router = APIRouter(prefix="/users", tags=["admin"])


def _view(record: UserRecord) -> UserView:
    return UserView(
        id=str(record.user_id),
        username=record.username,
        display_name=record.display_name,
        role=record.role,
        is_active=record.is_active,
    )


@router.get("")
async def list_users(deps: Deps, principal: AdminPrincipal) -> list[UserView]:
    return [_view(record) for record in await deps.users_admin.list()]


@router.post("/{user_id}/deactivate")
async def deactivate_user(user_id: str, deps: Deps, principal: AdminPrincipal) -> UserView:
    if user_id == str(principal.user_id):
        raise ApiError(ApiErrorCode.SELF_TARGET, 409, "you cannot deactivate your own account")
    revoked = await deps.users_admin.deactivate(UserId(user_id), at=deps.clock.now())
    if revoked is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such user")
    # After the commit, never inside it: a socket closed for a
    # transaction that then rolled back is a player kicked out of a live
    # game for nothing.
    deps.hub.close_sessions(revoked, 4401)
    record = await deps.users_admin.get(UserId(user_id))
    assert record is not None
    return _view(record)


@router.post("/{user_id}/role")
async def set_role(
    user_id: str, body: SetRoleRequest, deps: Deps, principal: AdminPrincipal
) -> UserView:
    outcome, revoked = await deps.users_admin.set_role(
        UserId(user_id), role=body.role, at=deps.clock.now()
    )
    if outcome is SetRoleOutcome.NOT_FOUND:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such user")
    if outcome is SetRoleOutcome.LAST_ADMIN:
        raise ApiError(
            ApiErrorCode.LAST_ADMIN, 409, "this is the last administrator; promote another first"
        )
    deps.hub.close_sessions(revoked, 4401)
    record = await deps.users_admin.get(UserId(user_id))
    assert record is not None
    return _view(record)
