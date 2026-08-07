from collections.abc import Sequence

from triviador.domain.ids import RegionId
from triviador.domain.maps.definition import MapDefinition


def validate_map(defn: MapDefinition, min_independent_set: int = 4) -> tuple[str, ...]:
    """Return every structural problem with a map. Empty tuple means valid."""
    problems: list[str] = []
    known = set(defn.region_ids())

    if len(known) != len(defn.regions):
        problems.append("duplicate region ids declared")

    for region_id, neighbours in defn.adjacency.items():
        if region_id not in known:
            problems.append(f"adjacency declared for unknown region {region_id!r}")
        for neighbour in neighbours:
            if neighbour not in known:
                problems.append(f"{region_id!r} borders unknown region {neighbour!r}")
            elif region_id not in defn.adjacency.get(neighbour, frozenset()):
                problems.append(f"asymmetric adjacency between {region_id!r} and {neighbour!r}")
        if region_id in neighbours:
            problems.append(f"{region_id!r} borders itself")

    if known and not _is_connected(defn):
        problems.append("adjacency graph is not connected")

    if _max_independent_set_at_least(defn, min_independent_set) is False:
        problems.append(
            f"no independent set of size {min_independent_set} — bases cannot be placed"
        )

    return tuple(problems)


def _is_connected(defn: MapDefinition) -> bool:
    ids = defn.region_ids()
    seen: set[RegionId] = {ids[0]}
    frontier = [ids[0]]
    while frontier:
        current = frontier.pop()
        for neighbour in defn.neighbours(current):
            if neighbour not in seen:
                seen.add(neighbour)
                frontier.append(neighbour)
    return len(seen) == len(ids)


def _max_independent_set_at_least(defn: MapDefinition, size: int) -> bool:
    """Greedy-with-backtracking search. Maps have ~15 regions, so this is cheap."""
    if size <= 0:
        return True
    return _search(defn, list(defn.region_ids()), [], size)


def _search(
    defn: MapDefinition,
    candidates: Sequence[RegionId],
    chosen: list[RegionId],
    size: int,
) -> bool:
    if len(chosen) >= size:
        return True
    for index, candidate in enumerate(candidates):
        if any(candidate in defn.neighbours(c) for c in chosen):
            continue
        if _search(defn, candidates[index + 1 :], [*chosen, candidate], size):
            return True
    return False
