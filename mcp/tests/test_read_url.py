import pytest

from groundhog_mcp.tools.read_url import read_url


async def test_read_url_rejects_unknown_format():
    # Validation happens at the boundary, before any engine call.
    with pytest.raises(ValueError):
        await read_url("https://example.com/", format="json")
