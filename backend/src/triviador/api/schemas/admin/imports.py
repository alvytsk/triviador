from datetime import datetime

from pydantic import BaseModel, ConfigDict

from triviador.services.admin import ImportStatus


class ImportRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int
    reason: str


class ImportNotice(BaseModel):
    """§10.2's warning channel: duplicate prompts, inside this upload or
    already in the bank. Distinct from `ImportRejection` in the contract
    as well as in the code, so 7B cannot render one as the other."""

    model_config = ConfigDict(extra="forbid")

    line: int
    reason: str


class ImportSummary(BaseModel):
    """`confirmable` is computed by the server, not inferred by the client
    from `rejected_count == 0`. The rule is §10.3's, it also depends on
    status and expiry, and a client that re-derives it will eventually
    derive it differently."""

    model_config = ConfigDict(extra="forbid")

    import_id: str
    upload_sha256: str
    filename: str
    staged_key: str | None
    row_count: int
    rejected_count: int
    rejections: list[ImportRejection]
    notices: list[ImportNotice]
    status: ImportStatus
    confirmable: bool
    expires_at: datetime
