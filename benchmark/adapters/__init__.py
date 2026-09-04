"""Fetch-layer adapters under test.

An adapter answers one question: given a URL, what text reaches the model, and did the
fetcher say anything about text it removed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Fetched:
    content: str
    disclosed: bool
    error: str | None = None
