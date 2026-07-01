#!/usr/bin/env bash
set -euo pipefail

XVFB_WHD=${XVFB_WHD:-1920x1080x24}
PORT=${PORT:-9222}
CHROME_PORT=${CHROME_PORT:-9223}
WINDOW_SIZE=${WINDOW_SIZE:-1920,1080}

PROXY_ARGS=()
if [ -n "${PROXY:-}" ]; then
  PROXY_ARGS=(--proxy-server="$PROXY")
  echo "[chrome-cdp] proxy: $PROXY"
fi

export DISPLAY=:99
export XDG_RUNTIME_DIR="/tmp/runtime"
export FONTCONFIG_CACHE_DIR="/tmp/.cache/fontconfig"
export HOME="/tmp"
mkdir -p "$XDG_RUNTIME_DIR" /tmp/.X11-unix /tmp/.cache/fontconfig /tmp/.pki/nssdb
chmod 1777 /tmp/.X11-unix
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

Xvfb :99 -screen 0 "$XVFB_WHD" -ac +extension GLX +render -noreset &
sleep 2

CHROME_USER_DATA="/tmp/chrome-user-data"
mkdir -p "$CHROME_USER_DATA"
rm -rf "$CHROME_USER_DATA"/Singleton* 2>/dev/null || true

# Bind Chrome to localhost and expose it through socat so remote CDP clients
# connect to $PORT while Chrome itself never listens on a public interface.
socat TCP4-LISTEN:"$PORT",fork TCP4:127.0.0.1:"$CHROME_PORT" &

echo "[chrome-cdp] starting Chrome (headless=new) on :$CHROME_PORT, exposed on :$PORT"

# --headless=new is required to load extensions; legacy --headless ignores them.
exec /opt/google/chrome/chrome \
  --headless=new \
  --remote-debugging-port="$CHROME_PORT" \
  --remote-allow-origins=* \
  --user-data-dir="$CHROME_USER_DATA" \
  --window-size="$WINDOW_SIZE" \
  --no-sandbox \
  --disable-dev-shm-usage \
  "${PROXY_ARGS[@]}" \
  --disable-blink-features=AutomationControlled \
  --enable-webgl \
  --ignore-gpu-blocklist \
  --use-gl=angle \
  --use-angle=gl \
  --enable-gpu-rasterization \
  --load-extension=/opt/stealth \
  about:blank
