"""Identifier aliases. No logic lives here."""

from typing import NewType

GameId = NewType("GameId", str)
PlayerId = NewType("PlayerId", str)
RegionId = NewType("RegionId", str)
MapId = NewType("MapId", str)
QuestionId = NewType("QuestionId", str)
CategoryId = NewType("CategoryId", str)
MediaAssetId = NewType("MediaAssetId", str)

# Monotonic per game, allocated from GameState.next_deadline_id.
DeadlineId = NewType("DeadlineId", int)
