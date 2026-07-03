#!/usr/bin/env bash
set -euo pipefail

# Xvfb screen deliberately larger than the Chrome window, so the viewport does
# not equal the screen (a headless tell: CreepJS `hasVvpScreenRes`).
XVFB_WHD=${XVFB_WHD:-2560x1440x24}
PORT=${PORT:-9222}
CHROME_PORT=${CHROME_PORT:-9223}
WINDOW_SIZE=${WINDOW_SIZE:-1920,1080}

# Derive the UA from the installed Chrome so the version never drifts from the
# binary. Set it at launch so the baseline UA is clean in every scope — main
# frame, network, and Web/Service Worker globals; a client-side per-context
# override does not reach worker scope, so the default HeadlessChrome token would
# otherwise leak there. Chrome freezes the UA's minor digits to 0.0.0.
CHROME_MAJOR=$(/opt/google/chrome/chrome --version 2>/dev/null | grep -oE '[0-9]+' | head -1 || true)
if [[ -z "$CHROME_MAJOR" ]]; then
  echo "[chrome-cdp] WARN: could not read Chrome version; UA left as default" >&2
  DEFAULT_UA=""
else
  DEFAULT_UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${CHROME_MAJOR}.0.0.0 Safari/537.36"
fi
USER_AGENT=${USER_AGENT:-$DEFAULT_UA}

UA_ARGS=()
if [[ -n "$USER_AGENT" ]]; then
  UA_ARGS=(--user-agent="$USER_AGENT")
  echo "[chrome-cdp] user-agent: $USER_AGENT"
fi

PROXY_ARGS=()
if [[ -n "${PROXY:-}" ]]; then
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

# GPU backend: hardware acceleration when a real GPU is reachable, otherwise
# Mesa llvmpipe — a coherent software renderer that VMs/servers/CI emit (unlike
# the bare "Google SwiftShader" tell).
GPU_ARGS=(--ignore-gpu-blocklist --enable-gpu-rasterization --use-gl=angle --use-angle=gl)
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  GPU_KIND=nvidia
elif [[ -e /dev/dri/renderD128 ]]; then
  GPU_KIND=/dev/dri
else
  GPU_KIND=software
fi
if [[ "$GPU_KIND" != software ]]; then
  GPU_ARGS=(--ignore-gpu-blocklist --enable-gpu-rasterization --use-gl=angle --use-angle=gl-egl)
fi
echo "[chrome-cdp] gpu: $GPU_KIND"

echo "[chrome-cdp] starting Chrome (headful under Xvfb) on :$CHROME_PORT, exposed on :$PORT"

# Headful under Xvfb, not --headless=new: headless carries extra fingerprint
# tells and reports a HeadlessChrome browser string; the virtual display lets
# real (headful) Chrome run and engages the GPU compositing path.
exec /opt/google/chrome/chrome \
  --no-first-run \
  --no-default-browser-check \
  --remote-debugging-port="$CHROME_PORT" \
  --remote-allow-origins=* \
  --user-data-dir="$CHROME_USER_DATA" \
  "${UA_ARGS[@]}" \
  --window-size="$WINDOW_SIZE" \
  --no-sandbox \
  --disable-dev-shm-usage \
  "${PROXY_ARGS[@]}" \
  --disable-blink-features=AutomationControlled \
  --use-fake-device-for-media-stream \
  --force-dark-mode \
  --enable-webgl \
  "${GPU_ARGS[@]}" \
  about:blank
