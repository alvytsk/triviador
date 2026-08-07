import json
from dataclasses import dataclass
from pathlib import Path

from triviador.domain.ids import MapId, RegionId
from triviador.domain.maps.definition import MapDefinition, Region
from triviador.domain.maps.validation import validate_map


class InvalidMapError(Exception):
    """A map directory is missing, malformed, or structurally invalid."""


@dataclass(frozen=True)
class MapRegistry:
    root: Path

    def available(self) -> tuple[MapId, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(
            MapId(child.name)
            for child in sorted(self.root.iterdir())
            if (child / "map.json").is_file()
        )

    def load(self, map_id: MapId) -> MapDefinition:
        path = self.root / map_id / "map.json"
        if not path.is_file():
            raise InvalidMapError(f"map {map_id!r}: no map.json at {path}")

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidMapError(f"map {map_id!r}: malformed JSON — {exc}") from exc

        defn = MapDefinition(
            map_id=MapId(raw["map_id"]),
            regions=tuple(Region(RegionId(r["id"]), r["name"]) for r in raw["regions"]),
            adjacency={
                RegionId(k): frozenset(RegionId(n) for n in v) for k, v in raw["adjacency"].items()
            },
        )

        problems = validate_map(defn)
        if problems:
            raise InvalidMapError(f"map {map_id!r} is invalid: " + "; ".join(problems))
        return defn
