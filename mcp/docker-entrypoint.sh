#!/bin/sh
# Start the browser, then hand stdio to the MCP server.
set -eu

CDP_PORT="${CDP_PORT:-9222}"
READY_TRIES="${CDP_READY_TRIES:-60}"

# stdout is the MCP transport: a single line of browser logging on it corrupts the
# JSON-RPC stream and the client drops the connection. The browser's entrypoint is
# chatty by design, so everything it writes goes to stderr, where a host that captures
# logs will still show it.
/entrypoint.sh >&2 &
browser_pid=$!

i=0
while [ "$i" -lt "$READY_TRIES" ]; do
  if curl -sf "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
    break
  fi
  # Report the browser dying rather than waiting out the full timeout for something
  # that is never going to answer.
  if ! kill -0 "$browser_pid" 2>/dev/null; then
    echo "groundhog: the browser exited before CDP came up" >&2
    exit 1
  fi
  i=$((i + 1))
  sleep 1
done

if [ "$i" -ge "$READY_TRIES" ]; then
  echo "groundhog: CDP was not ready after ${READY_TRIES}s" >&2
  exit 1
fi

echo "groundhog: browser ready on :${CDP_PORT}, starting the MCP server" >&2
exec groundhog-mcp "$@"
