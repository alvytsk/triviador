"""Canonical map digest. Formatting must not change the hash; content must."""

from triviador.domain.maps.digest import canonical_digest


def test_key_order_and_whitespace_do_not_change_the_digest() -> None:
    a = {"map_id": "t", "regions": [{"id": "a", "name": "A"}]}
    b = {"regions": [{"name": "A", "id": "a"}], "map_id": "t"}
    assert canonical_digest(a) == canonical_digest(b)


def test_a_content_change_changes_the_digest() -> None:
    a = {"adjacency": {"a": ["b"], "b": ["a"]}}
    b = {"adjacency": {"a": ["c"], "c": ["a"]}}
    assert canonical_digest(a) != canonical_digest(b)


def test_list_order_is_significant() -> None:
    """Adjacency lists are ordered in the file; a reordering is a real edit as
    far as this digest is concerned. Being strict here is deliberate — a false
    positive costs one operator confirmation, a false negative silently replays
    a game against different adjacency."""
    assert canonical_digest({"x": ["a", "b"]}) != canonical_digest({"x": ["b", "a"]})


def test_non_ascii_names_are_stable() -> None:
    value = {"name": "Královéhradecký"}
    assert canonical_digest(value) == canonical_digest({"name": "Královéhradecký"})
    assert len(canonical_digest(value)) == 64
