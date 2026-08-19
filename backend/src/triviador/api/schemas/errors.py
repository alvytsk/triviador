"""The one response shape every failure takes (§6.3, Spec 1 §11.1)."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from triviador.api.errors import ApiErrorCode
from triviador.domain.game.actions import RejectCode


class ErrorEnvelope(BaseModel):
    """`code` is a closed union of two disjoint enums.

    `ApiErrorCode` is "the server could not, or would not, do this";
    `RejectCode` is "the domain refused this command". Keeping both in one
    field means a client switches once. `test_envelope.py` asserts the two
    value sets never overlap.
    """

    model_config = ConfigDict(extra="forbid")

    code: ApiErrorCode | RejectCode
    message: str
    details: dict[str, Any] | None = None
