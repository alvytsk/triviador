import json
from pathlib import Path

import pytest

from triviador.domain.ids import MapId, RegionId
from triviador.maps.registry import InvalidMapError, MapRegistry

REPO_MAPS = Path(__file__).resolve().parents[3] / "data" / "maps"


def test_loads_the_shipped_map() -> None:
    defn = MapRegistry(REPO_MAPS).load(MapId("czechia"))
    assert len(defn.regions) >= 12
    assert defn.neighbours(defn.region_ids()[0])


def test_available_lists_shipped_maps() -> None:
    assert MapId("czechia") in MapRegistry(REPO_MAPS).available()


def test_invalid_map_raises_with_problems(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "map.json").write_text(
        json.dumps(
            {
                "map_id": "broken",
                "regions": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
                "adjacency": {"a": ["b"], "b": []},
            }
        )
    )
    with pytest.raises(InvalidMapError) as excinfo:
        MapRegistry(tmp_path).load(MapId("broken"))
    assert "asymmetric" in str(excinfo.value)


def test_structurally_invalid_map_raises(tmp_path: Path) -> None:
    broken = tmp_path / "malformed"
    broken.mkdir()
    (broken / "map.json").write_text(
        json.dumps(
            {
                "map_id": "malformed",
                "regions": [{"id": "a"}],  # missing "name" key
                "adjacency": {"a": []},
            }
        )
    )
    with pytest.raises(InvalidMapError):
        MapRegistry(tmp_path).load(MapId("malformed"))


def test_unknown_map_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidMapError):
        MapRegistry(tmp_path).load(MapId("nope"))


def test_shipped_map_supports_four_bases() -> None:
    defn = MapRegistry(REPO_MAPS).load(MapId("czechia"))
    # load() already validates, so reaching here proves an independent set of 4 exists.
    assert RegionId("praha") in set(defn.region_ids())
