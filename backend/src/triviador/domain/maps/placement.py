"""Where a game's bases go.

Spec 1 §3.4: `BasesAssigned` requires `player_count` mutually non-adjacent
regions, and `validate_map` asserts every registered map contains an
independent set of size ≥ 4. `_decide_start` validates only that the
regions it is handed are distinct and on the map — it does not choose
them, so it cannot check adjacency. This module is where the rule is
actually enforced.

Pure by construction: `rng` is a parameter, not a capability. The same
`Random` seed on the same map yields the same placement, which is what
makes the property test in `test_placement.py` meaningful.
"""

import random

from triviador.domain.ids import RegionId
from triviador.domain.maps.definition import MapDefinition


class BasesUnplaceable(Exception):
    """No independent set of the requested size exists on this map.

    Unreachable for a registered map — `validate_map` refuses to load one
    without an independent set of size ≥ 4, and `player_count` is capped
    at 4. Raised rather than returning a short tuple so the cause is named
    at its source instead of resurfacing as "start context is incomplete".
    """


def choose_base_regions(
    defn: MapDefinition, count: int, rng: random.Random
) -> tuple[RegionId, ...]:
    """`count` mutually non-adjacent regions, chosen uniformly at random
    among the placements reachable from a shuffled scan order.

    Exhaustive backtracking rather than randomized greedy-with-retries:
    the map validator *guarantees* a placement exists, so a search that
    can fail on an unlucky shuffle would convert a guaranteed property
    into an intermittent `StartGame` failure — the worst kind, because it
    would reproduce roughly never. Depth is bounded by `count` (≤ 4) and
    the region list by the map size (tens), so exhaustiveness costs
    nothing measurable.

    Randomness comes from the shuffle: the search takes the first
    placement it reaches in a random scan order, so different seeds land
    on different placements wherever more than one exists.
    """
    if count <= 0:
        return ()

    regions = list(defn.region_ids())
    rng.shuffle(regions)

    chosen: list[RegionId] = []
    blocked: set[RegionId] = set()

    def search(start: int) -> bool:
        # `nonlocal`, not just closing over `blocked`: the `|=`/`-=` below
        # rebind the name, which would otherwise shadow it as local to
        # `search` and raise `UnboundLocalError` on the next read.
        nonlocal blocked
        if len(chosen) == count:
            return True
        for index in range(start, len(regions)):
            region = regions[index]
            if region in blocked:
                continue
            # Compute the newly blocked set *before* mutating, so the undo
            # below restores exactly what this frame added — subtracting
            # the full neighbour set would unblock regions an outer frame
            # is still relying on.
            newly = (defn.neighbours(region) | {region}) - blocked
            blocked |= newly
            chosen.append(region)
            if search(index + 1):
                return True
            chosen.pop()
            blocked -= newly
        return False

    if not search(0):
        raise BasesUnplaceable(f"map {defn.map_id!r} has no independent set of size {count}")
    return tuple(chosen)
