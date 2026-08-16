# Golden corpus

Three complete game trajectories, played once through the real
`decide`/`fold` reducer and committed here as raw event rows.
`tests/codec/test_golden_corpus.py` reads these files, decodes each row with
`db.codec.codec.decode`, folds them through the reducer, and compares the
result to a committed expected summary. It never calls `encode` — a test
that encodes its own output and decodes it back only proves the codec agrees
with itself, which is true of every broken codec too.

This catches a semantic change to how the reducer *applies* an event
(`evolve`/`_apply`) — not to what `decide()` computes when producing one.
`_apply` never calls `decide()`, `expected_score`, or `holding_value`; it
only interprets event data already recorded, so a decide-side bug is
invisible to it by construction. The domain's `decide()`-calling unit tests
under `tests/domain/game/` remain the primary guard for game logic; this
corpus is a second, narrower layer on top of them, not a superset.

## Trajectories

- **`expansion_to_battle.json`** — creation, three joins, start, the
  `MediaWarmup` window, a full two-round expansion phase that fills the 3x3
  grid (3 bases + 6 claimed regions), and three battle-round-1 attacks (a
  capture, a held defense, a mutual miss) that carry the game into the
  opening of battle round 2. The broad trajectory: touches lifecycle,
  question, expansion, and battle events.
- **`surrender_ends_game.json`** — two consecutive surrenders during
  EXPANSION drop active players to one, which finishes the game immediately.
  This exercises the *event sequence* Plan 2's fix for Spec 1 §3.6 Defect A
  produces (`_decide_surrender` checking `active_players() <= 1` itself,
  rather than relying on a stale window later expiring), and pins how
  `fold` replays that sequence. It does **not** guard the fix itself:
  `_apply` only replays the `GameFinished` event already baked into these
  committed rows, so reverting the fix in `_decide_surrender` and rerunning
  this corpus still passes unchanged. The decide-side guarantee lives in
  `tests/domain/game/test_surrender.py::test_surrender_leaving_one_active_player_finishes_the_game`
  (and its EXPANSION-phase sibling in `test_expansion_picking.py`), which
  call `decide()` directly. `winner_id` is deliberately **not** null —
  that's what makes the replay worth pinning.
- **`abort_from_lobby.json`** — genesis, one join, a system-authorized
  `AbortGame(actor_id=None)`. Short, and the only corpus entry covering that
  path.

## Regenerating

```
uv run python tests/tools/generate_golden.py
```

The generator and the test both resolve `MapDefinition` from the same
shared builder in `tests/conftest.py` (`grid_map()`) — there is no second,
hand-rolled map definition anywhere in this corpus.

**A diff in these files during an unrelated change is a finding, not a
chore.** Regenerate only when a domain change is *intended* to alter game
history, and review the diff with the same care as the code change that
produced it. Skimming past a diff here to make the suite green again is
exactly the failure mode this corpus exists to catch.
