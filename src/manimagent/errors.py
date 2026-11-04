"""Application errors shared by workflows, storage and API adapters."""

from manimagent.schemas import Error


class ContentError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 422, state: str = "failed"):
        super().__init__(message)
        self.error = Error(code=code, message=message)
        self.http_status = status
        self.state = state
