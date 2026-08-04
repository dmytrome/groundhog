# Installing Groundhog

Instructions for an AI assistant setting this server up on a user's machine.

## What to add

Groundhog runs from PyPI through `uvx`. Add this to the user's MCP configuration:

```json
{
  "mcpServers": {
    "groundhog": {
      "command": "uvx",
      "args": ["groundhog-mcp"]
    }
  }
}
```

Nothing needs to be cloned or built, and there is no API key to obtain.

## The one prerequisite

Groundhog drives a real Chrome, which runs in a container. **The user needs Docker or
Podman installed and running.** On the first fetch, Groundhog pulls and starts that
container itself — there is no manual step, but the pull takes a few minutes the first
time. Later fetches are instant.

Check before you finish:

```bash
docker info    # or: podman info
```

If neither is present, do not treat the install as complete. Either point the user at
https://docs.docker.com/get-docker/, or use the alternative below.

## Alternative: a browser the user already runs

If the user cannot install a container runtime, or already runs a CDP-speaking browser,
skip the auto-start entirely:

```json
{
  "mcpServers": {
    "groundhog": {
      "command": "uvx",
      "args": ["groundhog-mcp"],
      "env": {
        "CDP_URL": "http://127.0.0.1:9222",
        "GROUNDHOG_AUTO_START_BROWSER": "false"
      }
    }
  }
}
```

`CDP_URL` may be remote. The endpoint is unauthenticated, so keep it on a private
network or behind a tunnel.

## Verifying the install

Call the `status` tool. It reports `browser_reachable`, the CDP endpoint, and — when the
browser is not reachable — a `hint` naming the exact remediation. That is the fastest
check that the whole path works; do not report success without it.

The first `read_url` call may take a few minutes while the browser image downloads. That
is expected once, not a failure.

## Optional configuration

| Env var | Default | Purpose |
| ------- | ------- | ------- |
| `CDP_URL` | `http://127.0.0.1:9222` | Browser endpoint; set it to use your own |
| `GROUNDHOG_AUTO_START_BROWSER` | `true` | `false` to manage the container yourself |
| `SEARXNG_URL` | unset | Your SearXNG instance for `search`; needs `formats: [html, json]` |
| `GROUNDHOG_MAX_TOKENS` | `20000` | Token budget before truncation |

The full list is in the README. None of it is required for a working install.
