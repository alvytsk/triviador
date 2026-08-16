from dataclasses import replace

from triviador.domain.game.rules import (
    DEFAULT_RULES,
    GameRules,
    required_question_budget,
    validate_rules,
)


def test_default_rules_are_valid() -> None:
    assert validate_rules(DEFAULT_RULES) == ()


def test_default_budget_matches_the_spec() -> None:
    # 3 players, 4 expansion rounds, 4 battle rounds:
    #   duels   = 4 * 3 = 12
    #   numeric = 4 expansion + 12 possible tiebreaks + 1 final = 17
    budget = required_question_budget(DEFAULT_RULES)
    assert budget.numeric == 17
    assert budget.multiple_choice == 12


def test_budget_scales_with_players_and_rounds() -> None:
    rules = replace(
        DEFAULT_RULES, player_count=2, claims_by_rank=(2, 1), expansion_rounds=2, battle_rounds=3
    )
    budget = required_question_budget(rules)
    assert budget.multiple_choice == 6
    assert budget.numeric == 2 + 6 + 1


def test_claims_must_match_player_count() -> None:
    problems = validate_rules(replace(DEFAULT_RULES, claims_by_rank=(2, 1)))
    assert any("claims_by_rank" in p for p in problems)


def test_player_count_bounds_are_enforced() -> None:
    assert any(
        "player_count" in p
        for p in validate_rules(
            replace(DEFAULT_RULES, player_count=5, claims_by_rank=(2, 1, 1, 0, 0))
        )
    )
    assert any(
        "player_count" in p
        for p in validate_rules(replace(DEFAULT_RULES, player_count=1, claims_by_rank=(2,)))
    )


def test_non_positive_counts_are_rejected() -> None:
    assert validate_rules(replace(DEFAULT_RULES, battle_rounds=0)) != ()
    assert validate_rules(replace(DEFAULT_RULES, base_hp=0)) != ()
    assert validate_rules(replace(DEFAULT_RULES, answer_timeout_ms=500)) != ()


def test_rules_are_frozen() -> None:
    rules: GameRules = DEFAULT_RULES
    try:
        rules.battle_rounds = 9  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("GameRules must be frozen")


def test_default_warmup_is_five_seconds() -> None:
    assert DEFAULT_RULES.warmup_ms == 5_000


def test_warmup_bounds_are_enforced() -> None:
    assert any("warmup_ms" in p for p in validate_rules(replace(DEFAULT_RULES, warmup_ms=999)))
    assert any("warmup_ms" in p for p in validate_rules(replace(DEFAULT_RULES, warmup_ms=60_001)))
    assert validate_rules(replace(DEFAULT_RULES, warmup_ms=1_000)) == ()
    assert validate_rules(replace(DEFAULT_RULES, warmup_ms=60_000)) == ()


def test_warmup_does_not_change_the_question_budget() -> None:
    """A warmup window presents no question, so it must not move the budget —
    otherwise every preset's coverage check shifts for no reason."""
    baseline = required_question_budget(DEFAULT_RULES)
    assert required_question_budget(replace(DEFAULT_RULES, warmup_ms=30_000)) == baseline
