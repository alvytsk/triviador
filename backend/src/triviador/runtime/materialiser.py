"""Command → `DecisionContext`, inside the command's own transaction.

ADR-004 requires `decide` to be a mathematical function: same state, same
command, same context, same events, forever. Everything non-deterministic
— the current instant, a shuffle, a random draw from the question bank —
is resolved *here* and travels into the domain as a value. What the domain
then writes into events is what replay reads back, so a replay can never
observe a different shuffle or a different pool.

Running inside the caller's transaction is not a detail: §5.3 requires the
`FOR SHARE` locks taken by the pool draw to still be held when the
resulting `QuestionPoolDrawn` event is inserted. That is what makes
"fewer than n rows → rejection, game stays in LOBBY" an authoritative
checkpoint rather than an advisory one.
"""

import logging
import random
from dataclasses import dataclass

from triviador.domain.game.actions import (
    Command,
    DecisionContext,
    ExpireDeadline,
    StartGame,
)
from triviador.domain.game.rules import required_question_budget
from triviador.domain.game.state import ExpansionPicking, GameState
from triviador.domain.maps.placement import choose_base_regions
from triviador.domain.questions.types import QuestionPool
from triviador.services.ports import Clock, QuestionPoolUnavailable, Transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Materialiser:
    clock: Clock
    rng: random.Random

    async def build(self, state: GameState, command: Command, tx: Transaction) -> DecisionContext:
        now = self.clock.now()

        if isinstance(command, StartGame):
            player_ids = list(state.players)
            self.rng.shuffle(player_ids)
            return DecisionContext(
                now=now,
                shuffled_player_ids=tuple(player_ids),
                base_regions=choose_base_regions(state.map, len(player_ids), self.rng),
                drawn_pool=await self._draw_pool(state, tx),
            )

        if isinstance(command, ExpireDeadline) and isinstance(state.turn, ExpansionPicking):
            # `_decide_auto_pick` falls back to `state.free_regions()` when
            # this is None — i.e. map order, so every timed-out pick in
            # every game would take the lowest-numbered free region.
            free = list(state.free_regions())
            self.rng.shuffle(free)
            return DecisionContext(now=now, shuffled_region_ids=tuple(free))

        return DecisionContext(now=now)

    async def _draw_pool(self, state: GameState, tx: Transaction) -> QuestionPool | None:
        """A bank shortfall is a *domain* shortfall (§5.5): return `None`
        and let `_decide_start` raise `RejectedCommand(
        QUESTION_POOL_INSUFFICIENT)`, so the rejection policy is stated
        once, in the domain, rather than duplicated here.

        `MalformedQuestion` is caught by the same clause deliberately. It
        is bad content, not a broken database, and quarantining on it
        would put a game that is sitting harmlessly in LOBBY into a reload
        loop that ends only when someone edits a row — the same reasoning
        §5.5 uses to keep broadcaster failure out of fault handling. It is
        logged at error, with the offending id, precisely because the
        rejection the player sees names the wrong cause.

        Anything else — a dropped connection, a serialization failure —
        propagates, and the executor's retry or quarantine handles it.
        """
        try:
            return await tx.questions.select_pool(required_question_budget(state.rules))
        except QuestionPoolUnavailable:
            logger.exception("question pool unavailable for game %s", state.game_id)
            return None
