"""Groundhog through `fetch_document` — the same path the MCP tools return to a caller.

Measured at the tool boundary rather than at the engine, because `threats` is what a
caller actually sees, and the character-level findings never appear on the rendered page.
"""

import os

from . import Fetched

NAME = "groundhog"

_FINDINGS = frozenset(
    {"hidden_css", "hidden_attribute", "hidden_template", "zero_width", "bidi", "tag"}
)


async def fetch_async(url: str, *, allow_private: bool) -> Fetched:
    if allow_private:
        os.environ["GROUNDHOG_BLOCK_PRIVATE_IPS"] = "false"
    from groundhog_mcp import document, engine

    try:
        doc = await document.fetch_document(url)
    except Exception as exc:  # noqa: BLE001
        return Fetched(content="", disclosed=False, error=str(exc))
    finally:
        await engine.shutdown_provider()
    reported = any(t["type"] in _FINDINGS for t in doc.threats)
    return Fetched(content=doc.markdown, disclosed=reported)
