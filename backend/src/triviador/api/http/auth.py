"""§6.1's auth surface. Four routes, one cookie."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, Response

from triviador.api.deps import Deps, Principal
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.schemas.auth import LoginRequest, Me, RedeemRequest
from triviador.db.security import new_token, token_digest
from triviador.domain.ids import SessionId, UserId
from triviador.services.identity import RedeemOutcome, UserRecord

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _me(user: UserRecord) -> Me:
    return Me(
        user_id=str(user.user_id),
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


async def _issue_session(deps: Deps, response: Response, user_id: UserId) -> None:
    token = new_token()
    expires_at = deps.clock.now() + timedelta(days=deps.settings.session_ttl_days)
    await deps.sessions.create(
        session_id=SessionId(uuid.uuid4().hex),
        user_id=user_id,
        token_hash=token_digest(token),
        expires_at=expires_at,
    )
    response.set_cookie(
        deps.settings.session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=deps.settings.cookie_secure,
        max_age=deps.settings.session_ttl_days * 86_400,
        path="/",
    )


@router.post("/redeem", status_code=201)
async def redeem(body: RedeemRequest, response: Response, deps: Deps) -> Me:
    user_id = UserId(uuid.uuid4().hex)
    outcome = await deps.invites.redeem(
        code_hash=token_digest(body.code),
        user_id=user_id,
        username=body.username,
        password_hash=deps.hasher.hash(body.password),
        display_name=body.display_name,
        now=deps.clock.now(),
    )
    if outcome is RedeemOutcome.INVITE_INVALID:
        raise ApiError(ApiErrorCode.INVITE_INVALID, 401, "invite code is not usable")
    if outcome is RedeemOutcome.USERNAME_TAKEN:
        raise ApiError(ApiErrorCode.USERNAME_TAKEN, 409, "that username is taken")

    user = await deps.users.get(user_id)
    assert user is not None  # just created inside the same transaction
    await _issue_session(deps, response, user_id)
    return _me(user)


@router.post("/login")
async def login(body: LoginRequest, response: Response, deps: Deps) -> Me:
    user = await deps.users.get_by_username(body.username)
    if user is None or not user.is_active:
        # Exactly one `verify` against a hash computed once at startup —
        # the same work the found-user path does. Hashing here instead
        # would cost *two* argon2 operations on the unknown-user path and
        # one on the wrong-password path, which is the same oracle running
        # in the other direction and just as measurable with curl.
        deps.hasher.verify(body.password, deps.dummy_password_hash)
        raise ApiError(ApiErrorCode.CREDENTIALS_INVALID, 401, "invalid username or password")
    if not deps.hasher.verify(body.password, user.password_hash):
        raise ApiError(ApiErrorCode.CREDENTIALS_INVALID, 401, "invalid username or password")
    await _issue_session(deps, response, user.user_id)
    return _me(user)


@router.post("/logout", status_code=204)
async def logout(response: Response, deps: Deps, principal: Principal) -> None:
    await deps.sessions.revoke(principal.session_id, at=deps.clock.now())
    response.delete_cookie(deps.settings.session_cookie_name, path="/")


@router.get("/me")
async def me(deps: Deps, principal: Principal) -> Me:
    user = await deps.users.get(principal.user_id)
    if user is None:
        # The session resolved, so the row existed a moment ago. A user
        # deleted between the two reads is not a 500.
        raise ApiError(ApiErrorCode.UNAUTHENTICATED, 401, "not signed in")
    return _me(user)
