"""§6.1's auth surface, and the cookie that carries it."""

from datetime import timedelta

import httpx
import pytest

from tests.api.fakes import FakeClock, FakeHasher, FakeInvites, FakeUsers
from triviador.api.deps import AppDependencies
from triviador.api.errors import ApiErrorCode
from triviador.db.security import token_digest
from triviador.domain.ids import UserId
from triviador.services.identity import InviteStore, UserRole


async def register(client: httpx.AsyncClient, invites: InviteStore, **kw: str) -> httpx.Response:
    # `token_digest`, not a literal "hashed-code": the route hashes the raw
    # code the same way real invites are looked up, and the fake's `valid`
    # dict is keyed by that digest — not by the wire value.
    #
    # `invites` arrives typed as the `InviteStore` Protocol (it is
    # `deps.invites` at every call site); the narrowing assertion is the
    # same convention `tests/runtime/conftest.drain_runtime` uses to reach
    # `FakeClock`-only behaviour behind a Protocol-typed field.
    assert isinstance(invites, FakeInvites)
    code = kw.get("code", "raw-code")
    invites.valid[token_digest(code)] = True
    return await client.post(
        "/api/auth/redeem",
        json={
            "code": code,
            "username": kw.get("username", "alice"),
            "password": kw.get("password", "correct horse"),
            "display_name": kw.get("display_name", "Alice"),
        },
    )


async def test_redeeming_a_valid_invite_creates_a_player_and_signs_them_in(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    response = await register(client, deps.invites)
    assert response.status_code == 201
    assert response.json() == {
        "user_id": response.json()["user_id"],
        "username": "alice",
        "display_name": "Alice",
        "role": "player",
    }
    assert deps.settings.session_cookie_name in response.cookies


async def test_the_session_cookie_is_httponly_lax_and_matches_cookie_secure(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """A cookie readable from JavaScript is a session token in every XSS,
    and `SameSite=Lax` is half of §6.4's CSRF story — the other half is the
    origin check, which is why neither alone is enough."""
    response = await register(client, deps.invites)
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "secure" not in header  # cookie_secure=False in the fixture


async def test_a_bad_invite_is_401_and_creates_nobody(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    response = await client.post(
        "/api/auth/redeem",
        json={
            "code": "wrong",
            "username": "mallory",
            "password": "correct horse",
            "display_name": "M",
        },
    )
    assert response.status_code == 401
    assert response.json()["code"] == ApiErrorCode.INVITE_INVALID
    assert await deps.users.get_by_username("mallory") is None


async def test_a_taken_username_is_409(client: httpx.AsyncClient, deps: AppDependencies) -> None:
    await register(client, deps.invites)
    response = await register(client, deps.invites)
    assert response.status_code == 409
    assert response.json()["code"] == ApiErrorCode.USERNAME_TAKEN


@pytest.mark.parametrize(
    "body",
    [
        {"username": "a", "password": "correct horse", "display_name": "A", "code": "c"},
        {"username": "alice", "password": "short", "display_name": "A", "code": "c"},
        {"username": "alice", "password": "correct horse", "display_name": "", "code": "c"},
        {"username": "al ice", "password": "correct horse", "display_name": "A", "code": "c"},
    ],
    ids=["username-too-short", "password-too-short", "empty-display-name", "username-has-space"],
)
async def test_a_malformed_registration_is_422(
    client: httpx.AsyncClient, body: dict[str, str]
) -> None:
    assert (await client.post("/api/auth/redeem", json=body)).status_code == 422


async def test_a_registration_carrying_a_role_is_rejected_outright(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """`extra="forbid"`, and the reason it is not optional: the field the
    request must never be able to set is the one that grants admin. The
    invite is marked valid so the test proves the field is rejected even
    when the redemption would otherwise succeed."""
    assert isinstance(deps.invites, FakeInvites)
    deps.invites.valid[token_digest("raw-code")] = True
    response = await client.post(
        "/api/auth/redeem",
        json={
            "code": "raw-code",
            "username": "mallory",
            "password": "correct horse",
            "display_name": "M",
            "role": "admin",
        },
    )
    assert response.status_code == 422


async def test_logging_in_returns_the_principal_and_a_cookie(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    await register(client, deps.invites)
    client.cookies.clear()
    response = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "correct horse"}
    )
    assert response.status_code == 200
    assert response.json()["username"] == "alice"
    assert deps.settings.session_cookie_name in response.cookies


@pytest.mark.parametrize(
    ("username", "password"),
    [("alice", "wrong"), ("nobody", "correct horse")],
    ids=["wrong-password", "unknown-user"],
)
async def test_both_kinds_of_bad_credentials_answer_identically(
    client: httpx.AsyncClient, deps: AppDependencies, username: str, password: str
) -> None:
    """Identical code, identical message. A distinguishable response is a
    username oracle, and with argon2 on one path and nothing on the other
    the *timing* is an oracle too — which is why the route verifies against
    a dummy hash when the user does not exist."""
    await register(client, deps.invites)
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 401
    assert response.json()["code"] == ApiErrorCode.CREDENTIALS_INVALID
    assert response.json()["message"] == "invalid username or password"


async def test_both_credential_failures_do_exactly_one_verification(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The mitigation is "one `verify` on every path", not "some extra work
    on the short path". Counting the calls is the only way to assert it
    without timing anything, and timing assertions do not belong in a test
    suite."""
    await register(client, deps.invites)
    assert isinstance(deps.hasher, FakeHasher)
    deps.hasher.verifications = 0
    await client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    unknown = deps.hasher.verifications
    deps.hasher.verifications = 0
    await client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
    assert unknown == deps.hasher.verifications == 1


async def test_me_returns_the_signed_in_user(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    await register(client, deps.invites)
    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["role"] == UserRole.PLAYER


async def test_me_without_a_cookie_is_401(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == ApiErrorCode.UNAUTHENTICATED


async def test_logging_out_revokes_the_session_and_clears_the_cookie(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    await register(client, deps.invites)
    response = await client.post("/api/auth/logout")
    assert response.status_code == 204
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_a_deactivated_user_is_401_on_the_very_next_request(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """Spec 1 §7's requirement, at the layer that enforces it: no cache, no
    grace period, no waiting for the cookie to expire."""
    body = (await register(client, deps.invites)).json()
    assert isinstance(deps.users, FakeUsers)
    deps.users.deactivate(UserId(body["user_id"]))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_an_expired_session_is_401(client: httpx.AsyncClient, deps: AppDependencies) -> None:
    await register(client, deps.invites)
    assert isinstance(deps.clock, FakeClock)
    deps.clock.advance(timedelta(days=deps.settings.session_ttl_days + 1))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_a_stored_password_is_never_the_password(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    await register(client, deps.invites)
    record = await deps.users.get_by_username("alice")
    assert record is not None
    assert "correct horse" not in record.password_hash
