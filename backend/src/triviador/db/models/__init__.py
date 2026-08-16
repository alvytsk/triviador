"""Every ORM model, re-exported from one place.

This is the single import Alembic's `env.py` makes (Task 3) to populate
`Base.metadata` before autogenerate diffs it against the live schema. A
model missing from `__all__` here means autogenerate silently proposes
dropping its table.
"""

from triviador.db.models.auth import InviteCode, Session, User
from triviador.db.models.content import (
    Category,
    MediaAsset,
    Question,
    QuestionChoice,
    QuestionImport,
    QuestionNumeric,
)
from triviador.db.models.games import Game, GameEventRow, GamePlayer
from triviador.db.models.presets import RulePreset

__all__ = [
    "Category",
    "Game",
    "GameEventRow",
    "GamePlayer",
    "InviteCode",
    "MediaAsset",
    "Question",
    "QuestionChoice",
    "QuestionImport",
    "QuestionNumeric",
    "RulePreset",
    "Session",
    "User",
]
