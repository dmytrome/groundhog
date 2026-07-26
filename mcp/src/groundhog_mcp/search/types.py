from typing import TypedDict


class SearchHit(TypedDict):
    title: str
    url: str
    snippet: str
    engine: str
    score: float
    published: str | None


class SearchUnavailableError(RuntimeError):
    """The search backend could not answer, with a hint for how to fix it."""
