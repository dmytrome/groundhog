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
CHROME_MAJOR=$(/usr/local/bin/chrome --version 2>/dev/null | grep -oE '[0-9]+' | head -1 || true)
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

# Proxy + geo-coherence: geolocate the proxy's exit IP and align the browser's
# timezone and locale to it. A UTC clock or en-US locale behind a foreign IP is
# itself a block signal; matching them is what lets cheap proxies pass. The
# country → locale table is CLDR likely-subtags, generated into locales.map.
# Primary geo source is Bright Data's exit-IP endpoint (built for lookups
# through a proxy, so not per-IP rate-limited); ip-api.com is the fallback,
# filled per-field so one endpoint hiccup does not drop geo-coherence.
RELAY_PORT=${RELAY_PORT:-8888}
LOCALES_MAP=${LOCALES_MAP:-/opt/locales.map}
GEO_TIMEOUT=${GEO_TIMEOUT:-15}
PROXY_ARGS=()
LANG_ARGS=()
if [[ -n "${PROXY:-}" ]]; then
  # Validate at the boundary: only [scheme://][user:pass@]host:port. This rejects
  # newlines/control chars, which would otherwise inject directives into the
  # tinyproxy config written below.
  if ! [[ "$PROXY" =~ ^([A-Za-z0-9]+://)?([^@[:space:]]+@)?[A-Za-z0-9._-]+:[0-9]+$ ]]; then
    echo "[chrome-cdp] ERROR: PROXY is malformed; expected [scheme://][user:pass@]host:port" >&2
    exit 1
  fi
  echo "[chrome-cdp] proxy: $(printf '%s' "$PROXY" | sed -E 's#//[^/@]*@#//#')"
  GEO=$(curl -s --proxy "$PROXY" --max-time "$GEO_TIMEOUT" https://geo.brdtest.com/mygeo.json 2>/dev/null || true)
  PTZ=$(printf '%s' "$GEO" | jq -r '.geo.tz // empty' 2>/dev/null || true)
  PCC=$(printf '%s' "$GEO" | jq -r '.country // empty' 2>/dev/null || true)
  if [[ -z "$PTZ" || -z "$PCC" ]]; then
    GEO=$(curl -s --proxy "$PROXY" --max-time "$GEO_TIMEOUT" http://ip-api.com/json 2>/dev/null || true)
    PTZ=${PTZ:-$(printf '%s' "$GEO" | jq -r '.timezone // empty' 2>/dev/null || true)}
    PCC=${PCC:-$(printf '%s' "$GEO" | jq -r '.countryCode // empty' 2>/dev/null || true)}
  fi
  if [[ -n "$PTZ" && -n "$PCC" ]]; then
    export TZ="$PTZ"
    LOCALE=$(grep -m1 "^$PCC=" "$LOCALES_MAP" 2>/dev/null | cut -d= -f2)
    LOCALE=${LOCALE:-en-US}
    LANG_ARGS=(--lang="$LOCALE")
    echo "[chrome-cdp] proxy geo: country=$PCC tz=$PTZ lang=$LOCALE"
  else
    echo "[chrome-cdp] WARN: could not geolocate proxy exit IP; using configured TZ" >&2
  fi
  # Chrome's --proxy-server cannot carry credentials (it fails auth entirely), so
  # route it through a local tinyproxy that injects them into the upstream. Chrome
  # sees a plain no-auth proxy; the engine stays untouched (no CDP auth handling).
  if [[ "$PROXY" == *"://"* ]]; then
    UP_SCHEME="${PROXY%%://*}"
    UP_REST="${PROXY#*://}"
  else
    UP_SCHEME=http
    UP_REST="$PROXY"
  fi
  case "$UP_SCHEME" in
    socks5|socks5h) UP_TYPE=socks5 ;;
    socks4|socks4a) UP_TYPE=socks4 ;;
    *) UP_TYPE=http ;;
  esac
  # DisableViaHeader: don't announce a proxy hop via the `Via` header on plain
  # HTTP requests. HTTPS is CONNECT-tunneled so no headers are added there.
  # umask 077 so the credentials in the config are never briefly world-readable.
  (umask 077; cat > /tmp/tinyproxy.conf <<EOF
Port $RELAY_PORT
Listen 127.0.0.1
Timeout 600
LogLevel Warning
User tinyproxy
Group tinyproxy
DisableViaHeader Yes
Upstream $UP_TYPE $UP_REST
EOF
  )
  tinyproxy -d -c /tmp/tinyproxy.conf &
  RELAY_PID=$!
  sleep 1
  if ! kill -0 "$RELAY_PID" 2>/dev/null; then
    echo "[chrome-cdp] WARN: proxy relay exited on startup; check PROXY" >&2
  fi
  PROXY_ARGS=(--proxy-server="http://127.0.0.1:$RELAY_PORT")
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
exec /usr/local/bin/chrome \
  --no-first-run \
  --no-default-browser-check \
  --remote-debugging-port="$CHROME_PORT" \
  --remote-allow-origins=* \
  --user-data-dir="$CHROME_USER_DATA" \
  "${UA_ARGS[@]}" \
  "${LANG_ARGS[@]}" \
  --window-size="$WINDOW_SIZE" \
  --no-sandbox \
  --disable-dev-shm-usage \
  "${PROXY_ARGS[@]}" \
  --disable-blink-features=AutomationControlled \
  --use-fake-device-for-media-stream \
  --force-webrtc-ip-handling-policy=disable_non_proxied_udp \
  --force-dark-mode \
  --enable-webgl \
  "${GPU_ARGS[@]}" \
  about:blank
