"""§10.6's admin preset DTOs — the write body and the two read shapes it
does not share with `api/schemas/presets.py`'s public `PresetSummary`.
"""

from pydantic import BaseModel, ConfigDict

from triviador.api.schemas.presets import RulesView


class PresetDetail(BaseModel):
    """`PresetSummary` plus `is_active`: the admin screen shows retired
    presets (Spec 1B §6.1's soft delete) where the public listing must
    not."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    is_default: bool
    is_active: bool
    rules: RulesView


class PresetWriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    is_default: bool
    rules: RulesView


class PresetCoverage(BaseModel):
    """§10.6's readout, and its honesty in a field.

    `informative` is `True` and always will be: between reading this and
    starting a game an admin can deactivate a question, so the
    authoritative check is the one `StartGame` makes in the transaction
    that draws the pool. The field exists so the screen has something to
    render that sentence from rather than inventing it.
    """

    model_config = ConfigDict(extra="forbid")

    required: dict[str, int]
    bank: dict[str, int]
    sufficient: bool
    informative: bool = True
