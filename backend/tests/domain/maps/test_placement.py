"""Spec 1 §3.4. The map validator guarantees an independent set of size ≥ 4
exists for every registered map, so for a valid map this search must always
succeed — a randomized greedy that gives up on an unlucky shuffle would turn
a guaranteed property into a flaky one."""

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.conftest import grid_map
from triviador.domain.ids import MapId, RegionId
from triviador.domain.maps.definition import MapDefinition, Region
from triviador.domain.maps.placement import BasesUnplaceable, choose_base_regions


def test_zero_bases_is_an_empty_tuple() -> None:
    assert choose_base_regions(grid_map(), 0, random.Random(0)) == ()


@pytest.mark.parametrize("seed", range(25))
def test_chosen_regions_are_distinct_and_mutually_non_adjacent(seed: int) -> None:
    """This also pins the search's completeness: `grid_map()`'s adjacency
    is 4-connected (orthogonal only, no diagonals), so it has six distinct
    4-element independent sets, not only the four corners — a shuffle that
    puts the centre (`r4`) or an edge midpoint first still has a completion
    and must be found by backtracking rather than giving up."""
    defn = grid_map()
    chosen = choose_base_regions(defn, 4, random.Random(seed))

    assert len(chosen) == 4
    assert len(set(chosen)) == 4
    assert set(chosen) <= set(defn.region_ids())
    for region in chosen:
        assert defn.neighbours(region).isdisjoint(chosen)


def test_different_seeds_produce_different_placements() -> None:
    """Bases must not be predictable across games on the same map — a
    deterministic placement would make the first pick of every expansion
    round known in advance."""
    defn = grid_map()
    seen = {choose_base_regions(defn, 2, random.Random(seed)) for seed in range(30)}
    assert len(seen) > 1


def test_raises_when_no_independent_set_of_that_size_exists() -> None:
    """A complete graph on three regions admits one base, never two. This
    can only be reached by an unregistered or invalid map, so it raises
    rather than returning a short tuple — a short tuple would reach
    `_decide_start` and be rejected there as an *incomplete start context*,
    naming the wrong cause."""
    complete = MapDefinition(
        map_id=MapId("triangle"),
        regions=(
            Region(RegionId("a"), "A"),
            Region(RegionId("b"), "B"),
            Region(RegionId("c"), "C"),
        ),
        adjacency={
            RegionId("a"): frozenset({RegionId("b"), RegionId("c")}),
            RegionId("b"): frozenset({RegionId("a"), RegionId("c")}),
            RegionId("c"): frozenset({RegionId("a"), RegionId("b")}),
        },
    )
    with pytest.raises(BasesUnplaceable):
        choose_base_regions(complete, 2, random.Random(0))


@given(seed=st.integers(min_value=0, max_value=10_000), count=st.integers(min_value=1, max_value=4))
def test_property_result_is_always_a_valid_independent_set(seed: int, count: int) -> None:
    defn = grid_map()
    chosen = choose_base_regions(defn, count, random.Random(seed))
    assert len(set(chosen)) == count
    for region in chosen:
        assert defn.neighbours(region).isdisjoint(chosen)
