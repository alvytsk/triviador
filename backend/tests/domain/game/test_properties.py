from dataclasses import replace

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from tests.conftest import NOW, full_pool, lobby_state
from triviador.domain.game.actions import Command, DecisionContext
from triviador.domain.game.events import GameEvent
from triviador.domain.game.rules import (
    DEFAULT_RULES,
    GameRules,
    required_question_budget,
    validate_rules,
)
from triviador.domain.game.scoring import expected_score
from triviador.domain.game.state import TERMINAL_PHASES, Phase
from triviador.domain.ids import RegionId


@st.composite
def valid_rules(draw: st.DrawFn) -> GameRules:
    players = draw(st.integers(min_value=2, max_value=4))
    claims = draw(st.lists(st.integers(0, 3), min_size=players, max_size=players).filter(sum))
    return replace(
        DEFAULT_RULES,
        player_count=players,
        claims_by_rank=tuple(claims),
        expansion_rounds=draw(st.integers(1, 5)),
        battle_rounds=draw(st.integers(1, 5)),
    )


@given(valid_rules())
def test_generated_rules_are_valid(rules: GameRules) -> None:
    assert validate_rules(rules) == ()


@given(valid_rules())
def test_budget_is_monotonic_in_rounds(rules: GameRules) -> None:
    bigger = replace(rules, battle_rounds=rules.battle_rounds + 1)
    assert required_question_budget(bigger).numeric > required_question_budget(rules).numeric
    assert (
        required_question_budget(bigger).multiple_choice
        > required_question_budget(rules).multiple_choice
    )


class GameMachine(RuleBasedStateMachine):
    """Drive random legal commands and assert the invariants after every step."""

    def __init__(self) -> None:
        super().__init__()
        self.state = lobby_state()
        self.events: list[object] = []
        self.accepted = 0
        self.budget = required_question_budget(self.state.rules)

    def _decide_purely(self, command: Command, ctx: DecisionContext) -> tuple[GameEvent, ...]:
        """The `purity` property from spec §12.1: `decide(state, command,
        ctx)` called twice with the exact same inputs must behave exactly
        the same way — the same events, or the same rejection — since
        nothing about `decide` is allowed to be stateful or time-dependent
        beyond what `ctx` already says. Every command the machine issues
        goes through here, so this is checked on every step, not just once."""
        from triviador.domain.game.actions import RejectedCommand
        from triviador.domain.game.reducer import decide

        try:
            events = decide(self.state, command, ctx)
        except RejectedCommand as exc:
            try:
                decide(self.state, command, ctx)
            except RejectedCommand as exc2:
                assert exc2.code == exc.code, "decide is not pure: repeat rejection differed"
            else:
                raise AssertionError("decide is not pure: repeat call did not reject") from None
            raise
        assert decide(self.state, command, ctx) == events, (
            "decide is not pure: repeat call differed"
        )
        return events

    def _apply(self, command: Command) -> None:
        from triviador.domain.game.actions import RejectedCommand
        from triviador.domain.game.reducer import fold

        try:
            events = self._decide_purely(command, self._ctx())
        except RejectedCommand:
            return  # rejections change nothing; that is itself under test
        if events:
            self.accepted += 1
            self.events.extend(events)
            self.state = fold(self.state, events)

    def _ctx(self, late: bool = False) -> DecisionContext:
        from datetime import timedelta

        deadline = self.state.current_deadline()
        now = deadline.deadline_at + timedelta(seconds=1) if late and deadline else NOW
        return DecisionContext(
            now=now,
            shuffled_player_ids=tuple(self.state.players),
            base_regions=(RegionId("r0"), RegionId("r2"), RegionId("r6")),
            shuffled_region_ids=self.state.free_regions(),
            drawn_pool=full_pool(),
        )

    @precondition(lambda self: self.state.phase is Phase.LOBBY and self.state.players)
    @rule()
    def start(self) -> None:
        from triviador.domain.game.actions import StartGame

        self._apply(StartGame(next(iter(self.state.players))))

    @precondition(
        lambda self: (
            self.state.phase is Phase.LOBBY
            and len(self.state.players) < self.state.rules.player_count
        )
    )
    @rule(seat=st.integers(0, 3))
    def join(self, seat: int) -> None:
        """Companion to `surrender`: once `LOBBY` surrender can empty the
        roster (Task 21 fix review — `PlayerLeft` used to crash `fold`
        outright), the machine needs a way back in, or Hypothesis reports
        `InvalidDefinition` on an empty lobby where no other rule's
        precondition can ever fire again."""
        from triviador.domain.game.actions import JoinGame
        from triviador.domain.ids import PlayerId

        self._apply(JoinGame(PlayerId(f"p{seat}"), f"Newcomer{seat}"))

    @precondition(
        lambda self: self.state.phase not in TERMINAL_PHASES and self.state.active_players()
    )
    @rule(player_index=st.integers(0, 3))
    def surrender(self, player_index: int) -> None:
        from triviador.domain.game.actions import Surrender

        active = self.state.active_players()
        self._apply(Surrender(active[player_index % len(active)]))

    @precondition(lambda self: self.state.phase not in TERMINAL_PHASES and self.state.players)
    @rule(player_index=st.integers(0, 3))
    def abort(self, player_index: int) -> None:
        from triviador.domain.game.actions import AbortGame

        candidates = self.state.active_players() or tuple(self.state.players)
        self._apply(AbortGame(candidates[player_index % len(candidates)]))

    @precondition(lambda self: self.state.current_deadline() is not None)
    @rule(
        player_index=st.integers(0, 3),
        choice=st.integers(0, 3),
        guess=st.integers(0, 300),
        elapsed=st.integers(0, 20_000),
    )
    def answer(self, player_index: int, choice: int, guess: int, elapsed: int) -> None:
        from decimal import Decimal

        from triviador.domain.game.actions import SubmitAnswer
        from triviador.domain.game.state import ChoiceAnswer, NumericAnswer
        from triviador.domain.questions.types import QuestionKind

        turn = self.state.turn
        active = self.state.active_players()
        if not active or turn is None or not hasattr(turn, "question"):
            return
        player = active[player_index % len(active)]
        value = (
            NumericAnswer(Decimal(guess))
            if turn.question.kind is QuestionKind.NUMERIC
            else ChoiceAnswer(choice)
        )
        window = self.state.current_deadline()
        assert window is not None
        self._apply(SubmitAnswer(player, window.id, value, elapsed))

    @precondition(lambda self: self.state.current_deadline() is not None)
    @rule(region_index=st.integers(0, 8))
    def pick_or_target(self, region_index: int) -> None:
        from triviador.domain.game.actions import PickRegion, SelectAttackTarget
        from triviador.domain.game.reducer import legal_targets
        from triviador.domain.game.state import BattleTargetSelect, ExpansionPicking

        turn = self.state.turn
        window = self.state.current_deadline()
        assert window is not None
        if isinstance(turn, ExpansionPicking):
            free = self.state.free_regions()
            if free:
                self._apply(
                    PickRegion(turn.current_picker, window.id, free[region_index % len(free)])
                )
        elif isinstance(turn, BattleTargetSelect):
            targets = legal_targets(self.state, turn.attacker_id)
            if targets:
                self._apply(
                    SelectAttackTarget(
                        turn.attacker_id, window.id, targets[region_index % len(targets)]
                    )
                )

    @precondition(lambda self: self.state.current_deadline() is not None)
    @rule()
    def expire(self) -> None:
        from triviador.domain.game.actions import ExpireDeadline, RejectedCommand
        from triviador.domain.game.reducer import fold

        window = self.state.current_deadline()
        assert window is not None
        try:
            events = self._decide_purely(ExpireDeadline(window.id), self._ctx(late=True))
        except RejectedCommand:
            return
        if events:
            self.accepted += 1
            self.events.extend(events)
            self.state = fold(self.state, events)

    @invariant()
    def score_matches_holdings_plus_bonus(self) -> None:
        for player_id in self.state.players:
            assert self.state.players[player_id].score == expected_score(self.state, player_id)

    @invariant()
    def score_log_reconstructs_the_score(self) -> None:
        from triviador.domain.game.events import ScoreChanged

        for player_id in self.state.players:
            total = sum(
                e.delta
                for e in self.events
                if isinstance(e, ScoreChanged) and e.player_id == player_id
            )
            assert total == self.state.players[player_id].score

    @invariant()
    def replay_reproduces_the_state(self) -> None:
        from triviador.domain.game.reducer import fold

        assert fold(lobby_state(), self.events) == self.state  # type: ignore[arg-type]

    @invariant()
    def a_turn_has_exactly_one_deadline(self) -> None:
        if self.state.turn is None:
            assert self.state.current_deadline() is None
        else:
            assert self.state.current_deadline() is not None

    @invariant()
    def terminal_phases_have_no_open_turn(self) -> None:
        if self.state.phase in TERMINAL_PHASES:
            assert self.state.turn is None

    @invariant()
    def eliminated_players_own_nothing(self) -> None:
        for player_id, player in self.state.players.items():
            if player.is_eliminated:
                assert self.state.owned_by(player_id) == ()

    @invariant()
    def the_pool_is_never_exhausted(self) -> None:
        assert self.state.pool.numeric_used <= self.budget.numeric
        assert self.state.pool.mc_used <= self.budget.multiple_choice

    @invariant()
    def progress_is_bounded(self) -> None:
        rules = self.state.rules
        ceiling = (
            rules.expansion_rounds * (1 + sum(rules.claims_by_rank))
            + rules.battle_rounds * rules.player_count * 4
            + len(self.state.map.regions) * 3
            + 50
        )
        assert self.accepted <= ceiling, "state machine has an unbounded cycle"

    @precondition(lambda self: self.state.phase in TERMINAL_PHASES)
    @rule()
    def terminal_is_absorbing(self) -> None:
        assert self.state.phase in (Phase.FINISHED, Phase.ABORTED)


TestGameMachine = GameMachine.TestCase
TestGameMachine.settings = settings(
    max_examples=200,
    stateful_step_count=120,
    suppress_health_check=[HealthCheck.too_slow],
)
