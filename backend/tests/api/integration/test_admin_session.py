"""The whole admin surface, once, in the order an operator actually uses it.

Synchronous, like every test in this directory, for the reason its
conftest gives: `TestClient` runs the app on its own loop in its own
thread. Real PostgreSQL, real Garage, real argon2 — the only thing faked
here is the wall clock's patience.
"""

import io
import zipfile
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.api.integration.conftest import _SyncMediaStore, run
from tests.imports.test_parse import HEADER
from tests.media.test_pipeline import png
from triviador.config import Settings
from triviador.db.engine import engine_for, sessionmaker_for
from triviador.media.gc import GcReport

pytestmark = pytest.mark.integration

MC_ROW = (
    "multiple_choice,Which river runs through Prague?,geography,easy,"
    "Vltava,Elbe,Morava,Ohře,0,,,river.png"
)
NUM_ROW = "numeric,In which year did the Velvet Revolution begin?,history,easy,,,,,,1989,,"


def bank_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("questions.csv", "\n".join((HEADER, MC_ROW, NUM_ROW)))
        archive.writestr("media/river.png", png(800, 400))
    return buffer.getvalue()


def test_an_admin_can_furnish_a_server_from_nothing(
    admin_session: tuple[TestClient, Settings], media_store: _SyncMediaStore
) -> None:
    client, _settings = admin_session

    # 1. A category, because an import references one by slug.
    assert client.post(
        "/api/admin/categories", json={"slug": "geography", "name": "Geography"}
    ).status_code == 201

    # 2. A question typed by hand, with an image uploaded first.
    uploaded = client.post(
        "/api/admin/media", content=png(600, 300), headers={"Content-Type": "image/png"}
    )
    assert uploaded.status_code == 201
    asset = uploaded.json()
    # The blob is in the real bucket, re-encoded, with the immutable header.
    head = media_store.head_sync(f"{asset['id'][:2]}/{asset['id']}.webp")
    assert head is not None
    assert head.content_type == "image/webp"
    assert head.cache_control == "public, max-age=31536000, immutable"

    categories = client.get("/api/admin/categories").json()
    geography = next(c["id"] for c in categories if c["slug"] == "geography")
    created = client.post(
        "/api/admin/questions",
        json={
            "kind": "numeric",
            "prompt": "How many bridges cross the Vltava in Prague?",
            "category_id": geography,
            "difficulty": "medium",
            "media_asset_id": asset["id"],
            "choices": None,
            "numeric_answer": "18",
            "unit": None,
        },
    )
    assert created.status_code == 201

    # 3. A bulk import: dry-run refuses nothing, confirm applies it once.
    dry = client.post(
        "/api/admin/questions/import/dry-run",
        content=bank_zip(),
        headers={"Content-Type": "application/octet-stream", "X-Filename": "bank.zip"},
    )
    assert dry.status_code == 201 and dry.json()["rejected_count"] == 0
    import_id = dry.json()["import_id"]
    assert client.post(f"/api/admin/questions/import/{import_id}/confirm").status_code == 200
    assert client.post(f"/api/admin/questions/import/{import_id}/confirm").status_code == 409

    # `seeded` (this directory's shared reset fixture) already leaves 6
    # questions in the bank — 4 numeric, 2 multiple-choice, for
    # `FAST_RULES`'s gameplay tests — before this test adds its own 3
    # (1 hand-typed + 2 imported). Their prompts are the fixed literals
    # "how many?" and "prompt", neither of which contains "vltava", so
    # the search filter is exercised by exactly the one hand-typed
    # question and not diluted by the seeded rows.
    listed = client.get("/api/admin/questions?limit=100").json()
    assert listed["total"] == 9
    assert client.get("/api/admin/questions?q=vltava").json()["total"] == 1

    # 4. A preset, and its coverage readout. `informative` is a constant
    # `True` by design (`PresetCoverage`'s docstring: the authoritative
    # check is the one `StartGame` makes at draw time — an admin can
    # deactivate a question between reading this and starting a game, so
    # the field exists only so the screen has something to render that
    # sentence from). `is True` would pass identically if the whole
    # coverage feature were deleted and the route returned an empty body
    # coerced through the same schema, so this checks only that the field
    # is present in the contract, not that its value proves anything.
    # `bank` is load-bearing: it must be exactly the seeded 4 numeric + 2
    # MC plus this test's own 1 hand-typed numeric + 1 imported numeric +
    # 1 imported MC, not merely "some number smaller than `required`"
    # (which a `bank` stuck at zero would also satisfy against the default
    # preset's much larger budget).
    coverage = client.get("/api/admin/presets/default/coverage").json()
    assert "informative" in coverage
    assert coverage["bank"] == {"numeric": 6, "multiple_choice": 3}
    assert coverage["required"]["numeric"] > coverage["bank"]["numeric"]
    assert coverage["sufficient"] is False

    # 5. An invite, redeemed by a stranger, who then loses their account.
    code = client.post("/api/admin/invites", json={"count": 1}).json()[0]["code"]
    # A second client, no cookies — but *not* `with client.__class__(client.app)
    # as newcomer:`. Entering a fresh `TestClient` as its own context manager
    # spins up its own `anyio` blocking portal, i.e. its own event loop on
    # its own thread (see `starlette.testclient.TestClient.__enter__`). Over
    # the fakes every other test in this plan runs against, a second loop is
    # invisible. Over the real asyncpg-backed engine `client` and `newcomer`
    # would then share, it is not: SQLAlchemy's pool hands out a connection
    # `client`'s login already opened and checked back in, `newcomer`'s
    # portal tries to use it from a different loop, and asyncpg raises
    # `RuntimeError: ... got Future ... attached to a different loop` on the
    # very first request. Production never has this problem — one process,
    # one event loop — so the fix belongs here, not in `triviador.db.engine`:
    # give `newcomer` its own `httpx.Client` (so its own cookie jar) while
    # deliberately reusing `client`'s already-running portal.
    newcomer = client.__class__(client.app)
    newcomer.portal = client.portal
    try:
        # `OriginMiddleware` (§6.4) checks every state-changing request,
        # not just the ones this suite happens to send from `client` —
        # a fresh `TestClient` starts with no headers at all, so this
        # would 403 before ever reaching the redeem route without it.
        newcomer.headers["Origin"] = "http://testserver"
        redeemed = newcomer.post(
            "/api/auth/redeem",
            json={"code": code, "username": "newcomer", "password": "correct horse",
                  "display_name": "Newcomer"},
        )
        assert redeemed.status_code == 201
        assert newcomer.get("/api/auth/me").status_code == 200

        user_id = redeemed.json()["user_id"]
        assert client.post(f"/api/admin/users/{user_id}/deactivate").status_code == 200
        # Immediately, on the very next request, with the same cookie.
        assert newcomer.get("/api/auth/me").status_code == 401
    finally:
        newcomer.close()

    # 6. The admin cannot remove themselves.
    me = client.get("/api/auth/me").json()
    assert client.post(f"/api/admin/users/{me['user_id']}/deactivate").status_code == 409
    assert client.post(
        f"/api/admin/users/{me['user_id']}/role", json={"role": "player"}
    ).status_code == 409


def test_a_stale_media_asset_id_is_404_not_a_database_outage(
    admin_session: tuple[TestClient, Settings],
) -> None:
    """Important #1's actual scenario, against real PostgreSQL: an admin
    uploads an image, never attaches it to anything, `media-gc` sweeps the
    now-unreferenced row away, and only then does the editor tab that had
    the id in its form state `PATCH` a question with it. Before the fix,
    `questions.media_asset_id`'s foreign key violation surfaced as a raw
    `IntegrityError`, which the global handler answers with 503
    `database_unavailable` — as if the database itself, not the id, were
    the problem.
    """
    client, settings = admin_session

    misc = client.post(
        "/api/admin/categories", json={"slug": "misc", "name": "Misc"}
    ).json()
    asset = client.post(
        "/api/admin/media", content=png(40, 40), headers={"Content-Type": "image/png"}
    ).json()
    created = client.post(
        "/api/admin/questions",
        json={
            "kind": "numeric",
            "prompt": "How many bridges cross the river?",
            "category_id": misc["id"],
            "difficulty": "easy",
            "media_asset_id": None,
            "choices": None,
            "numeric_answer": "1",
            "unit": None,
        },
    ).json()["question"]

    # Nothing in the database names `asset["id"]` yet, so deleting the row
    # directly is exactly what `media-gc`'s sweep would do to it — no FK
    # stands in the way, the same way none stands in `media-gc`'s way.
    async def delete_asset() -> None:
        async with engine_for(settings.database_url) as engine:
            sessions = sessionmaker_for(engine)
            async with sessions() as db, db.begin():
                await db.execute(
                    text("DELETE FROM media_assets WHERE id = :id"), {"id": asset["id"]}
                )

    run(delete_asset())

    patched = client.patch(
        f"/api/admin/questions/{created['id']}",
        json={
            "kind": "numeric",
            "prompt": created["prompt"],
            "category_id": misc["id"],
            "difficulty": "easy",
            "media_asset_id": asset["id"],
            "choices": None,
            "numeric_answer": "1",
            "unit": None,
        },
    )
    assert patched.status_code == 404
    assert patched.json()["code"] == "not_found"


def test_media_gc_keeps_what_a_question_still_names_and_collects_what_nothing_does(
    admin_session: tuple[TestClient, Settings],
    media_store: _SyncMediaStore,
    run_media_gc: Callable[..., GcReport],
) -> None:
    """§10.4's two-way check, against the real store: the asset attached to
    a live question survives, and an upload nobody attached does not."""
    client, _ = admin_session
    attached = client.post(
        "/api/admin/media", content=png(120, 60), headers={"Content-Type": "image/png"}
    ).json()
    orphan = client.post(
        "/api/admin/media", content=png(121, 61), headers={"Content-Type": "image/png"}
    ).json()
    client.post("/api/admin/categories", json={"slug": "misc", "name": "Misc"})
    misc = next(
        c["id"] for c in client.get("/api/admin/categories").json() if c["slug"] == "misc"
    )
    client.post(
        "/api/admin/questions",
        json={"kind": "numeric", "prompt": "Kept?", "category_id": misc, "difficulty": "easy",
              "media_asset_id": attached["id"], "choices": None, "numeric_answer": "1",
              "unit": None},
    )

    # Dry run first: it must report the same verdict and change nothing.
    preview = run_media_gc(dry_run=True)
    assert orphan["id"] in preview.unreferenced
    assert attached["id"] not in preview.unreferenced
    assert preview.deleted is False
    assert media_store.head_sync(f"{orphan['id'][:2]}/{orphan['id']}.webp") is not None

    report = run_media_gc()
    assert orphan["id"] in report.unreferenced
    assert attached["id"] not in report.unreferenced
    assert media_store.head_sync(f"{orphan['id'][:2]}/{orphan['id']}.webp") is None
    assert media_store.head_sync(f"{attached['id'][:2]}/{attached['id']}.webp") is not None
