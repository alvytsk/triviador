"""Shared builders. Every test constructs states through these."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from triviador.domain.game.actions import DecisionContext, ExpireDeadline
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.rules import DEFAULT_RULES, GameRules
from triviador.domain.game.state import (
    AcquisitionKind,
    GameState,
    Phase,
    PlayerState,
    Territory,
    TerritoryKind,
)
from triviador.domain.ids import (
    CategoryId,
    GameId,
    MapId,
    PlayerId,
    QuestionId,
    RegionId,
)
from triviador.domain.maps.definition import MapDefinition, Region
from triviador.domain.questions.types import (
    CategorySnapshot,
    ChoiceSnapshot,
    Difficulty,
    QuestionKind,
    QuestionPool,
    QuestionSnapshot,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
CATEGORY = CategorySnapshot(CategoryId("c"), "general", "General")

# A 3x3 grid: nine regions, four-corner independent set, easy to reason about.
GRID_IDS = [f"r{i}" for i in range(9)]


def grid_map() -> MapDefinition:
    def neighbours(i: int) -> set[str]:
        row, col = divmod(i, 3)
        out: set[str] = set()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            r, c = row + dr, col + dc
            if 0 <= r < 3 and 0 <= c < 3:
                out.add(f"r{r * 3 + c}")
        return out

    return MapDefinition(
        map_id=MapId("grid"),
        regions=tuple(Region(RegionId(rid), rid.upper()) for rid in GRID_IDS),
        adjacency={
            RegionId(f"r{i}"): frozenset(RegionId(n) for n in neighbours(i)) for i in range(9)
        },
    )


def numeric_question(n: int, answer: int) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId(f"n{n}"),
        version=1,
        kind=QuestionKind.NUMERIC,
        prompt=f"numeric {n}?",
        category=CATEGORY,
        difficulty=Difficulty.MEDIUM,
        choices=None,
        numeric_answer=Decimal(answer),
        unit=None,
        media_asset_id=None,
    )


def mc_question(n: int, correct: int = 0) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId(f"m{n}"),
        version=1,
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt=f"mc {n}?",
        category=CATEGORY,
        difficulty=Difficulty.EASY,
        choices=tuple(
            ChoiceSnapshot(i, chr(ord("a") + i), is_correct=(i == correct), media_asset_id=None)
            for i in range(4)
        ),
        numeric_answer=None,
        unit=None,
        media_asset_id=None,
    )


def full_pool(numeric: int = 40, mc: int = 40) -> QuestionPool:
    return QuestionPool(
        numeric=tuple(numeric_question(i, 100 + i) for i in range(numeric)),
        multiple_choice=tuple(mc_question(i) for i in range(mc)),
    )


def a_player(pid: str, seat: int, **overrides: object) -> PlayerState:
    base = PlayerState(
        player_id=PlayerId(pid),
        display_name=pid.upper(),
        seat=seat,
        score=0,
        bonus_score=0,
        base_region=None,
        is_eliminated=False,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def empty_territories() -> dict[RegionId, Territory]:
    return {
        RegionId(rid): Territory(RegionId(rid), None, TerritoryKind.NORMAL, None, None, None)
        for rid in GRID_IDS
    }


def lobby_state(
    players: Mapping[str, int] | None = None,
    rules: GameRules = DEFAULT_RULES,
) -> GameState:
    seats = players if players is not None else {"p1": 0, "p2": 1, "p3": 2}
    return GameState(
        game_id=GameId("g1"),
        seq=0,
        next_deadline_id=1,
        map=grid_map(),
        rules=rules,
        phase=Phase.LOBBY,
        round_no=0,
        turn_order=tuple(PlayerId(p) for p in seats),
        players={PlayerId(p): a_player(p, s) for p, s in seats.items()},
        territories=empty_territories(),
        turn=None,
        pool=QuestionPool(numeric=(), multiple_choice=()),
        winner_id=None,
    )


def own(
    state: GameState,
    region: str,
    player: str,
    acquisition: AcquisitionKind = AcquisitionKind.CLAIMED,
) -> GameState:
    rid = RegionId(region)
    territory = replace(state.territories[rid], owner_id=PlayerId(player), acquisition=acquisition)
    new_territories = {**state.territories, rid: territory}
    updated = replace(state, territories=new_territories)
    player_state = updated.players[PlayerId(player)]
    from triviador.domain.game.scoring import expected_score

    return replace(
        updated,
        players={
            **updated.players,
            PlayerId(player): replace(
                player_state, score=expected_score(updated, PlayerId(player))
            ),
        },
    )


def expire_warmup(state: GameState) -> GameState:
    """Step past the MediaWarmup window opened by StartGame."""
    assert state.turn is not None
    return fold(
        state,
        decide(
            state,
            ExpireDeadline(state.turn.deadline.id),
            DecisionContext(now=state.turn.deadline.deadline_at + timedelta(seconds=1)),
        ),
    )


@pytest.fixture
def now() -> datetime:
    return NOW
