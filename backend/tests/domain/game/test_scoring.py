from dataclasses import replace

from tests.domain.game.test_state import a_state
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.scoring import expected_score, holding_value, holdings_value
from triviador.domain.game.state import AcquisitionKind, Territory, TerritoryKind
from triviador.domain.ids import PlayerId, RegionId


def a_territory(acquisition: AcquisitionKind | None) -> Territory:
    return Territory(RegionId("x"), PlayerId("p1"), TerritoryKind.NORMAL, None, None, acquisition)


def test_holding_value_is_derived_from_acquisition_not_region_type() -> None:
    assert holding_value(a_territory(AcquisitionKind.CLAIMED), DEFAULT_RULES) == 200
    assert holding_value(a_territory(AcquisitionKind.CONQUEST), DEFAULT_RULES) == 400
    assert holding_value(a_territory(AcquisitionKind.BASE), DEFAULT_RULES) == 1000
    assert holding_value(a_territory(None), DEFAULT_RULES) == 0


def test_the_same_region_is_worth_more_to_its_conqueror() -> None:
    claimed = holding_value(a_territory(AcquisitionKind.CLAIMED), DEFAULT_RULES)
    conquered = holding_value(a_territory(AcquisitionKind.CONQUEST), DEFAULT_RULES)
    assert conquered > claimed


def test_holdings_value_sums_only_that_players_regions() -> None:
    state = a_state()
    assert holdings_value(state, PlayerId("p1")) == 1000
    assert holdings_value(state, PlayerId("p2")) == 1000


def test_expected_score_adds_bonuses_to_holdings() -> None:
    state = a_state()
    p1 = state.players[PlayerId("p1")]
    state = replace(state, players={**state.players, PlayerId("p1"): replace(p1, bonus_score=300)})
    assert expected_score(state, PlayerId("p1")) == 1300


def test_bonuses_survive_losing_every_holding() -> None:
    state = a_state()
    p1 = state.players[PlayerId("p1")]
    stripped = {
        r: replace(t, owner_id=None, acquisition=None) for r, t in state.territories.items()
    }
    state = replace(
        state,
        territories=stripped,
        players={**state.players, PlayerId("p1"): replace(p1, bonus_score=300)},
    )
    assert expected_score(state, PlayerId("p1")) == 300
