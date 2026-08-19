"""§8.7's `project_snapshot`, and the one leak that would end the game.

`GameState.pool` holds every question of the whole match, each with its
`is_correct` flags. It is the single largest secret in the system and it
sits one attribute away from the object being projected.
"""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from tests.conftest import NOW, full_pool, lobby_state, own
from triviador.api.projection.snapshot import project_snapshot
from triviador.api.projection.viewer import ViewerContext
from triviador.api.schemas.games import ClientGameState
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import (
    Deadline,
    DeadlineKind,
    ExpansionQuestion,
    GameState,
    Phase,
    TerritoryKind,
)
from triviador.domain.ids import DeadlineId, MediaAssetId, PlayerId, UserId
from triviador.services.identity import UserRole

MEDIA = "/media"


def viewer(pid: str | None = "p1") -> ViewerContext:
    return ViewerContext(UserId(pid or "x"), PlayerId(pid) if pid else None, UserRole.PLAYER)


def playing_state() -> GameState:
    # `full_pool()`'s question 0 has `numeric_answer=100`, which collides
    # with `DEFAULT_RULES.pts_defense == 100` — a value that IS legitimately
    # projected (rules are public, per §5 of the brief). Left alone, that
    # collision would make `test_no_correct_answer_of_any_kind_appears`
    # pass or fail for the wrong reason: "100" is genuinely in the body via
    # `pts_defense`, not via a leaked answer. Give the presented question a
    # distinctive answer so the leak check tests what it claims to.
    pool = full_pool()
    pool = replace(
        pool,
        numeric=(replace(pool.numeric[0], numeric_answer=Decimal("918273645")), *pool.numeric[1:]),
    )
    state = own(own(lobby_state(), "r0", "p1"), "r4", "p2")
    return replace(
        state,
        seq=42,
        phase=Phase.EXPANSION,
        round_no=2,
        pool=pool,
        turn=ExpansionQuestion(
            deadline=Deadline(DeadlineId(3), DeadlineKind.ANSWER, NOW + timedelta(seconds=20)),
            question=pool.numeric[0],
            answers={},
        ),
    )


def test_the_snapshot_carries_the_sequence_it_was_taken_at() -> None:
    """§8.4's dispatcher compares `seq`, and §9.3's cache writer keeps the
    newer of a REST response and a WS update by comparing it. It lives on
    the envelope, not inside the state, so there is one of it."""
    snapshot = project_snapshot(playing_state(), viewer(), media_base=MEDIA)
    assert snapshot.seq == 42
    assert "seq" not in ClientGameState.model_fields


def test_not_one_undrawn_question_reaches_the_client() -> None:
    """The whole match's pool is in `state.pool`. Exactly one question — the
    one the open turn presents — may appear, and only through the turn."""
    state = playing_state()
    body = project_snapshot(state, viewer(), media_base=MEDIA).model_dump_json()
    assert state.pool.numeric[0].prompt in body
    for question in (*state.pool.numeric[1:], *state.pool.multiple_choice):
        assert question.prompt not in body


def test_no_correct_answer_of_any_kind_appears() -> None:
    state = playing_state()
    body = project_snapshot(state, viewer(), media_base=MEDIA).model_dump_json()
    assert "is_correct" not in body
    assert "numeric_answer" not in body
    assert str(state.pool.numeric[0].numeric_answer) not in body


def test_media_prefetch_covers_the_pool_and_is_opaque() -> None:
    """§9.6: the client must prefetch every image before any timer starts,
    which means it needs URLs for questions it has not been shown. They are
    content-addressed ids and nothing else — no prompt, no question id, no
    ordering that maps back to the pool."""
    pool = full_pool(numeric=2, mc=0)
    with_media = replace(
        pool,
        numeric=tuple(
            replace(q, media_asset_id=MediaAssetId(f"asset{i}")) for i, q in enumerate(pool.numeric)
        ),
    )
    state = replace(playing_state(), pool=with_media)
    snapshot = project_snapshot(state, viewer(), media_base=MEDIA)
    assert set(snapshot.state.media_prefetch) == {"/media/asset0", "/media/asset1"}
    for url in snapshot.state.media_prefetch:
        assert "numeric" not in url


def test_media_prefetch_covers_choice_level_media_too() -> None:
    """`_media_prefetch` walks two data paths: question-level `media_asset_id`
    (covered above) and choice-level `media_asset_id` on multiple-choice
    questions' choices. Every other fixture uses `mc=0`, so this second path
    never ran — a choice image that misses this list is one the client loads
    during the answer window, exactly the unfairness §9.6's warmup exists to
    prevent."""
    pool = full_pool(numeric=0, mc=1)
    mc = pool.multiple_choice[0]
    assert mc.choices is not None
    with_media = replace(
        pool,
        multiple_choice=(
            replace(
                mc,
                choices=tuple(
                    replace(c, media_asset_id=MediaAssetId(f"choice{c.idx}")) for c in mc.choices
                ),
            ),
        ),
    )
    state = replace(playing_state(), pool=with_media)
    snapshot = project_snapshot(state, viewer(), media_base=MEDIA)
    assert {"/media/choice0", "/media/choice1", "/media/choice2", "/media/choice3"} <= set(
        snapshot.state.media_prefetch
    )


def test_media_prefetch_is_sorted_not_draw_order() -> None:
    """The docstring says sortedness is what stops the list leaking the
    pool's draw order. A `set(...)` comparison cannot prove that — a
    regression emitting assets in draw order would still pass it — so this
    uses ids whose sort order differs from their insertion order."""
    pool = full_pool(numeric=3, mc=0)
    with_media = replace(
        pool,
        numeric=tuple(
            replace(q, media_asset_id=MediaAssetId(a))
            for q, a in zip(pool.numeric, ("zeta", "alpha", "mu"), strict=True)
        ),
    )
    state = replace(playing_state(), pool=with_media)
    snapshot = project_snapshot(state, viewer(), media_base=MEDIA)
    assert snapshot.state.media_prefetch == ("/media/alpha", "/media/mu", "/media/zeta")
    assert snapshot.state.media_prefetch == tuple(sorted(snapshot.state.media_prefetch))


def test_a_lobby_projects_with_no_turn_and_nothing_to_prefetch() -> None:
    snapshot = project_snapshot(lobby_state(), viewer(), media_base=MEDIA)
    assert snapshot.state.turn is None
    assert snapshot.state.media_prefetch == ()
    assert snapshot.state.phase == Phase.LOBBY


def test_every_player_and_every_region_is_present() -> None:
    """The client renders the whole board from this and nothing else, so a
    partial projection is a partial board."""
    state = playing_state()
    snapshot = project_snapshot(state, viewer(), media_base=MEDIA)
    assert {p.player_id for p in snapshot.state.players} == {str(p) for p in state.players}
    assert {t.region_id for t in snapshot.state.territories} == {
        str(r) for r in state.map.region_ids()
    }


def test_territory_ownership_and_bases_survive_the_projection() -> None:
    state = playing_state()
    projected = {
        t.region_id: t
        for t in project_snapshot(state, viewer(), media_base=MEDIA).state.territories
    }
    assert projected["r0"].owner_id == "p1"
    assert projected["r1"].owner_id is None
    assert projected["r0"].kind == TerritoryKind.NORMAL


def test_the_you_block_tells_a_participant_who_they_are() -> None:
    snapshot = project_snapshot(playing_state(), viewer("p1"), media_base=MEDIA)
    assert snapshot.state.you.player_id == "p1"
    assert snapshot.state.you.role is UserRole.PLAYER


def test_a_non_participant_gets_a_you_block_with_no_player() -> None:
    snapshot = project_snapshot(playing_state(), viewer(None), media_base=MEDIA)
    assert snapshot.state.you.player_id is None


def test_the_rules_are_public_and_projected_whole() -> None:
    """Nothing in `GameRules` is secret — it is frozen at creation and the
    client needs it to say "round 2 of 4" — so withholding any of it would
    only mean the client guessing."""
    snapshot = project_snapshot(playing_state(), viewer(), media_base=MEDIA)
    assert snapshot.state.rules.expansion_rounds == DEFAULT_RULES.expansion_rounds
    assert snapshot.state.rules.claims_by_rank == DEFAULT_RULES.claims_by_rank


def test_adjacency_is_never_projected() -> None:
    """§6.1 says `GET /api/maps/{id}` never returns adjacency; the snapshot
    is the other door to the same fact, and §8.8 is the reason — the client
    is told its options, not the rule that produced them."""
    body = project_snapshot(playing_state(), viewer(), media_base=MEDIA).model_dump_json()
    assert "adjacency" not in body
    assert "neighbours" not in body
