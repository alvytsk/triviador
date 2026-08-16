"""Content digest for map topology.

Pure: hashes a value already in memory. The *reading* of map.json lives in
`triviador.maps.registry`, outside the domain.
"""

import hashlib
import json


def canonical_digest(raw: object) -> str:
    """sha256 of the canonical JSON serialization of `raw`.

    Canonical, not the file's bytes: reformatting map.json must not read as a
    map change, or every cosmetic edit would refuse to load every historical
    game that used it.
    """
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
