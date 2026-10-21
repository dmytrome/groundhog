from typing import TypedDict

from ..safety import CallerFacingError


class SearchHit(TypedDict):
    title: str
    url: str
    snippet: str
    engine: str
    score: float
    published: str | None


class SearchUnavailableError(CallerFacingError, RuntimeError):
    pass
