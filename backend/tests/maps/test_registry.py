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


def test_load_with_digest_returns_a_stable_sha256() -> None:
    first = MapRegistry(REPO_MAPS).load_with_digest(MapId("czechia"))
    second = MapRegistry(REPO_MAPS).load_with_digest(MapId("czechia"))
    assert first.sha256 == second.sha256
    assert len(first.sha256) == 64
    assert first.definition == second.definition


def test_load_still_returns_a_bare_definition() -> None:
    registry = MapRegistry(REPO_MAPS)
    assert registry.load(MapId("czechia")) == registry.load_with_digest(MapId("czechia")).definition


def test_reformatting_map_json_does_not_change_the_digest(tmp_path: Path) -> None:
    """Canonical digest, not file bytes: a whitespace-only edit must not
    invalidate every historical game that used this map."""
    source = json.loads((REPO_MAPS / "czechia" / "map.json").read_text(encoding="utf-8"))
    original = MapRegistry(REPO_MAPS).load_with_digest(MapId("czechia")).sha256

    reformatted = tmp_path / "czechia"
    reformatted.mkdir()
    (reformatted / "map.json").write_text(json.dumps(source, indent=4, sort_keys=True))

    assert MapRegistry(tmp_path).load_with_digest(MapId("czechia")).sha256 == original
