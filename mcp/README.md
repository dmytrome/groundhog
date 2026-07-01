# groundhog-mcp

Safe, self-hosted web grounding for AI agents and crawlers. An MCP server that fetches
live web pages through a real, stealth-patched Chrome (over CDP) and returns clean
Markdown with provenance — with a built-in SSRF guard and per-domain rate limiting.

Requires a running stealth browser (CDP endpoint). See the project README for setup:
https://github.com/dmytrome/groundhog

## Install

```bash
uvx groundhog-mcp
```

MCP client config (Claude Desktop / Cursor / Windsurf):

```json
{
  "mcpServers": {
    "groundhog": {
      "command": "uvx",
      "args": ["groundhog-mcp"],
      "env": { "CDP_URL": "http://127.0.0.1:9222" }
    }
  }
}
```

## Tools

- `read_url(url, format="markdown", max_tokens=None)` — fetch a page and return clean
  Markdown plus provenance (`final_url`, `title`, `fetched_at`, `truncated`).
- `status()` — check whether the stealth browser is reachable.

Full documentation: https://github.com/dmytrome/groundhog

<!-- mcp-name: io.github.dmytrome/groundhog-mcp -->
