"""Two hashes, chosen for two different threats.

**Passwords: argon2id.** They are low-entropy and human-chosen, so the only
defence against a stolen `users` table is making each guess expensive.

**Session and invite tokens: SHA-256.** They are 256 bits from
`secrets.token_urlsafe`, so there is nothing to guess and no dictionary to
try — the hash exists only so a leaked database row cannot be replayed as a
credential. Using argon2 here instead would put ~50 ms of deliberate
key-stretching on *every authenticated request*, which is a self-inflicted
outage rather than a security property. It also makes the token
unlookupable: an argon2 hash is salted, so a token could only be found by
scanning every session row and verifying each.
"""

import hashlib
import secrets

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import Argon2Error, InvalidHashError


class Argon2Hasher:
    """Implements `services.identity.PasswordHasher`."""

    def __init__(self) -> None:
        self._hasher = _Argon2()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        try:
            return self._hasher.verify(hashed, password)
        except (Argon2Error, InvalidHashError):
            # A mismatch and a corrupt stored hash are both "no". The second
            # is not hypothetical: a truncated column, a hash written by a
            # different algorithm, or a row restored from a bad backup all
            # produce it, and a 500 there is an outage where a 401 is right.
            return False


def new_token() -> str:
    """32 bytes of `secrets` entropy, URL-safe: it rides in a cookie."""
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
