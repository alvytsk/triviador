"""Values migrations wrote, frozen at the version that wrote them.

Nothing here may ever be edited in place. A migration is a record of what
a database was made to contain; editing a value it seeded changes what a
*fresh* installation gets while every upgraded installation keeps the old
one, and no row in either database records which it received. To change a
default, add a new constant and a new migration.
"""

DEFAULT_PRESET_RULES = {
    "player_count": 3,
    "expansion_rounds": 4,
    "battle_rounds": 4,
    "base_hp": 3,
    "answer_timeout_ms": 20000,
    "pick_timeout_ms": 15000,
    "warmup_ms": 5000,
    "claims_by_rank": [2, 1, 0],
    "pts_base": 1000,
    "pts_territory": 200,
    "pts_conquered": 400,
    "pts_defense": 100,
}
