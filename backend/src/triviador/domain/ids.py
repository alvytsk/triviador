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

# Identity, as distinct from participation. A user's `PlayerId` inside a
# game *is* their `UserId` — `games.host_id` and `game_players.user_id` are
# both foreign keys to `users.id` — but the two names carry different
# meanings and different lifetimes, and a signature that says `UserId` is
# saying "this is not scoped to a game".
UserId = NewType("UserId", str)
SessionId = NewType("SessionId", str)
