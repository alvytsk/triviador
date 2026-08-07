from triviador.domain.ids import MapId, RegionId
from triviador.domain.maps.definition import MapDefinition, Region
from triviador.domain.maps.validation import validate_map


def a_map(adjacency: dict[str, list[str]]) -> MapDefinition:
    return MapDefinition(
        map_id=MapId("test"),
        regions=tuple(Region(RegionId(r), r.title()) for r in sorted(adjacency)),
        adjacency={RegionId(r): frozenset(RegionId(n) for n in ns) for r, ns in adjacency.items()},
    )


def test_a_well_formed_map_has_no_problems() -> None:
    # A path a-b-c-d-e-f-g-h: alternating nodes give an independent set of 4.
    chain = {
        "a": ["b"],
        "b": ["a", "c"],
        "c": ["b", "d"],
        "d": ["c", "e"],
        "e": ["d", "f"],
        "f": ["e", "g"],
        "g": ["f", "h"],
        "h": ["g"],
    }
    assert validate_map(a_map(chain)) == ()


def test_asymmetric_adjacency_is_reported() -> None:
    problems = validate_map(a_map({"a": ["b"], "b": []}))
    assert any("asymmetric" in p for p in problems)


def test_disconnected_graph_is_reported() -> None:
    problems = validate_map(a_map({"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"]}))
    assert any("connected" in p for p in problems)


def test_unknown_neighbour_is_reported() -> None:
    defn = MapDefinition(
        map_id=MapId("test"),
        regions=(Region(RegionId("a"), "A"),),
        adjacency={RegionId("a"): frozenset({RegionId("ghost")})},
    )
    problems = validate_map(defn)
    assert any("unknown region" in p for p in problems)


def test_too_small_independent_set_is_reported() -> None:
    # A complete graph on 4 nodes has a maximum independent set of 1.
    clique = {r: [o for o in "abcd" if o != r] for r in "abcd"}
    problems = validate_map(a_map(clique))
    assert any("independent set" in p for p in problems)


def test_neighbours_returns_the_declared_set() -> None:
    defn = a_map({"a": ["b"], "b": ["a"]})
    assert defn.neighbours(RegionId("a")) == frozenset({RegionId("b")})
    assert defn.region_ids() == (RegionId("a"), RegionId("b"))
