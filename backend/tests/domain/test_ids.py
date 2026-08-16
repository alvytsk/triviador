from triviador.domain.ids import DeadlineId, GameId, PlayerId, RegionId


def test_ids_are_distinct_newtypes_over_their_base() -> None:
    assert GameId("g1") == "g1"
    assert PlayerId("p1") == "p1"
    assert RegionId("R1") == "R1"
    assert DeadlineId(17) == 17
