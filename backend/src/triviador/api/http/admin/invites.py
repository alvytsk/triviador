"""§10.5's invite half.

The plaintext code exists in exactly one response body, because
`invite_codes` stores only `token_digest(code)` (Plan 3). An admin who
loses the code issues another one — which costs nothing — and a listing
that could show it again would mean the digest was never protecting
anything.
"""

from datetime import timedelta

from fastapi import APIRouter

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.admin.invites import InviteView, IssuedInvite, IssueInvitesRequest
from triviador.services.admin import InviteRecord

router = APIRouter(prefix="/invites", tags=["admin"])


def _view(record: InviteRecord) -> InviteView:
    return InviteView(
        id=record.invite_id,
        status=record.status,
        expires_at=record.expires_at,
        used_by=record.used_by,
    )


@router.post("", status_code=201)
async def issue_invites(
    body: IssueInvitesRequest, deps: Deps, principal: AdminPrincipal
) -> list[IssuedInvite]:
    expires_at = deps.clock.now() + timedelta(hours=body.expires_in_hours)
    issued = await deps.invites_admin.issue(
        count=body.count, expires_at=expires_at, created_by=principal.user_id
    )
    return [
        IssuedInvite(id=invite_id, code=code, expires_at=expires_at)
        for invite_id, code in issued
    ]


@router.get("")
async def list_invites(deps: Deps, principal: AdminPrincipal) -> list[InviteView]:
    return [_view(record) for record in await deps.invites_admin.list_all(now=deps.clock.now())]


@router.post("/{invite_id}/revoke")
async def revoke_invite(invite_id: str, deps: Deps, principal: AdminPrincipal) -> InviteView:
    """Idempotent: revoking an already-revoked code answers 200 with the
    same body. An admin clicking twice has not made a mistake worth an
    error, and the second click is indistinguishable from a retry."""
    if not await deps.invites_admin.revoke(invite_id, at=deps.clock.now()):
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such invite")
    records = await deps.invites_admin.list_all(now=deps.clock.now())
    record = next(r for r in records if r.invite_id == invite_id)
    return _view(record)
