"""Two hashes with two different jobs."""

from triviador.db.security import Argon2Hasher, new_token, token_digest


def test_a_password_verifies_against_its_own_hash() -> None:
    hasher = Argon2Hasher()
    hashed = hasher.hash("correct horse")
    assert hasher.verify("correct horse", hashed)


def test_a_wrong_password_is_false_rather_than_an_exception() -> None:
    """argon2-cffi raises on mismatch. A route that has to catch an
    exception to learn "wrong password" is a route that will eventually
    catch the wrong one and authenticate somebody."""
    hasher = Argon2Hasher()
    assert hasher.verify("wrong", hasher.hash("right")) is False


def test_a_corrupt_stored_hash_is_false_rather_than_a_500() -> None:
    assert Argon2Hasher().verify("anything", "not-a-hash") is False


def test_two_hashes_of_one_password_differ() -> None:
    hasher = Argon2Hasher()
    assert hasher.hash("same") != hasher.hash("same")


def test_tokens_are_unguessable_and_distinct() -> None:
    tokens = {new_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 40 for t in tokens)


def test_a_token_digest_is_stable_and_hides_the_token() -> None:
    token = new_token()
    assert token_digest(token) == token_digest(token)
    assert token not in token_digest(token)
    assert len(token_digest(token)) == 64
