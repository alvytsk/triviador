from dataclasses import dataclass

from triviador.domain.questions.types import QuestionBudget

MIN_PLAYERS = 2
MAX_PLAYERS = 4
MIN_TIMEOUT_MS = 3_000
MAX_TIMEOUT_MS = 120_000
MIN_WARMUP_MS = 1_000
MAX_WARMUP_MS = 60_000


@dataclass(frozen=True)
class GameRules:
    player_count: int
    expansion_rounds: int
    battle_rounds: int
    base_hp: int
    answer_timeout_ms: int
    pick_timeout_ms: int
    # Fixed window after the pool is drawn, during which the client prefetches
    # every question image before any answer timer starts. Never derived from
    # client readiness — ADR-003 forbids a rule depending on presence.
    warmup_ms: int
    claims_by_rank: tuple[int, ...]
    pts_base: int
    pts_territory: int
    pts_conquered: int
    pts_defense: int


DEFAULT_RULES = GameRules(
    player_count=3,
    expansion_rounds=4,
    battle_rounds=4,
    base_hp=3,
    answer_timeout_ms=20_000,
    pick_timeout_ms=15_000,
    warmup_ms=5_000,
    claims_by_rank=(2, 1, 0),
    pts_base=1000,
    pts_territory=200,
    pts_conquered=400,
    pts_defense=100,
)


def validate_rules(rules: GameRules) -> tuple[str, ...]:
    problems: list[str] = []

    if not MIN_PLAYERS <= rules.player_count <= MAX_PLAYERS:
        problems.append(f"player_count must be {MIN_PLAYERS}..{MAX_PLAYERS}")
    if len(rules.claims_by_rank) != rules.player_count:
        problems.append("claims_by_rank must have exactly player_count entries")
    if any(c < 0 for c in rules.claims_by_rank):
        problems.append("claims_by_rank entries must be non-negative")
    if sum(rules.claims_by_rank) == 0:
        problems.append("claims_by_rank must grant at least one region per round")

    for name, value in (
        ("expansion_rounds", rules.expansion_rounds),
        ("battle_rounds", rules.battle_rounds),
        ("base_hp", rules.base_hp),
    ):
        if value < 1:
            problems.append(f"{name} must be at least 1")

    for name, value, low, high in (
        ("answer_timeout_ms", rules.answer_timeout_ms, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
        ("pick_timeout_ms", rules.pick_timeout_ms, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
        ("warmup_ms", rules.warmup_ms, MIN_WARMUP_MS, MAX_WARMUP_MS),
    ):
        if not low <= value <= high:
            problems.append(f"{name} must be {low}..{high}")

    for name, value in (
        ("pts_base", rules.pts_base),
        ("pts_territory", rules.pts_territory),
        ("pts_conquered", rules.pts_conquered),
        ("pts_defense", rules.pts_defense),
    ):
        if value < 0:
            problems.append(f"{name} must be non-negative")

    return tuple(problems)


def required_question_budget(rules: GameRules) -> QuestionBudget:
    """Upper bound on question consumption over every possible trajectory.

    One attack per player per battle round; each may go to a numeric tiebreak.
    Plus one numeric per expansion round and one for the final score tiebreak.
    """
    duels = rules.battle_rounds * rules.player_count
    return QuestionBudget(
        numeric=rules.expansion_rounds + duels + 1,
        multiple_choice=duels,
    )
