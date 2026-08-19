"""`GameState` → `GameSnapshot`, per viewer.

Field by field, deliberately: a `model_validate(asdict(state))` would
project `pool` — every question of the match with its `is_correct` flags —
the first time anyone added a field to `GameState`, and no test that did not
already know to look for it would notice.
"""

from triviador.api.projection.turns import project_turn
from triviador.api.projection.viewer import ViewerContext
from triviador.api.schemas.games import (
    ClientGameState,
    ClientPlayer,
    ClientRules,
    ClientTerritory,
    ClientYou,
    GameSnapshot,
)
from triviador.domain.game.rules import GameRules
from triviador.domain.game.state import GameState
from triviador.domain.questions.types import QuestionPool


def _rules(rules: GameRules) -> ClientRules:
    return ClientRules(
        player_count=rules.player_count,
        expansion_rounds=rules.expansion_rounds,
        battle_rounds=rules.battle_rounds,
        base_hp=rules.base_hp,
        answer_timeout_ms=rules.answer_timeout_ms,
        pick_timeout_ms=rules.pick_timeout_ms,
        warmup_ms=rules.warmup_ms,
        claims_by_rank=rules.claims_by_rank,
        pts_base=rules.pts_base,
        pts_territory=rules.pts_territory,
        pts_conquered=rules.pts_conquered,
        pts_defense=rules.pts_defense,
    )


def _media_prefetch(pool: QuestionPool, media_base: str) -> tuple[str, ...]:
    """Every image the match can present, as opaque URLs (§9.6).

    This is the *only* thing derived from the pool, and it is safe for the
    exact reason §9.6 gives: a content-addressed id carries no prompt, no
    answer, and no ordering that maps back to which question is which.
    Sorted, so the list does not leak the pool's draw order either.
    """
    assets = {
        q.media_asset_id
        for q in (*pool.numeric, *pool.multiple_choice)
        if q.media_asset_id is not None
    }
    assets |= {
        c.media_asset_id
        for q in pool.multiple_choice
        for c in (q.choices or ())
        if c.media_asset_id is not None
    }
    return tuple(f"{media_base}/{a}" for a in sorted(assets))


def project_snapshot(state: GameState, viewer: ViewerContext, *, media_base: str) -> GameSnapshot:
    return GameSnapshot(
        seq=state.seq,
        state=ClientGameState(
            game_id=str(state.game_id),
            map_id=str(state.map.map_id),
            phase=state.phase,
            round_no=state.round_no,
            rules=_rules(state.rules),
            turn_order=tuple(str(p) for p in state.turn_order),
            players=tuple(
                ClientPlayer(
                    player_id=str(p.player_id),
                    display_name=p.display_name,
                    seat=p.seat,
                    score=p.score,
                    bonus_score=p.bonus_score,
                    base_region=None if p.base_region is None else str(p.base_region),
                    is_eliminated=p.is_eliminated,
                )
                for p in sorted(state.players.values(), key=lambda p: p.seat)
            ),
            territories=tuple(
                ClientTerritory(
                    region_id=str(region_id),
                    owner_id=None if t.owner_id is None else str(t.owner_id),
                    kind=t.kind,
                    base_owner_id=None if t.base_owner_id is None else str(t.base_owner_id),
                    base_hp=t.base_hp,
                    acquisition=t.acquisition,
                )
                for region_id in state.map.region_ids()
                for t in (state.territories[region_id],)
            ),
            turn=project_turn(state, viewer, media_base=media_base),
            winner_id=None if state.winner_id is None else str(state.winner_id),
            media_prefetch=_media_prefetch(state.pool, media_base),
            you=ClientYou(
                player_id=None if viewer.player_id is None else str(viewer.player_id),
                role=viewer.role,
            ),
        ),
    )
