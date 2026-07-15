FROM debian:bookworm-slim

ARG TARGETARCH

# Google ships no linux/arm64 Chrome, so arm64 (e.g. Apple Silicon dev machines,
# where the amd64 image runs under emulation and crashes on heavy pages) gets
# Debian's Chromium instead — same engine, same CDP surface. Stealth conformance
# is validated on the amd64 real-Chrome image; treat arm64 as a development
# build. Both install paths end at /usr/local/bin/chrome so the entrypoint has a
# single binary reference.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates wget gnupg curl xvfb tini socat && \
    if [ "$TARGETARCH" = "arm64" ]; then \
      apt-get install -y --no-install-recommends chromium && \
      ln -s /usr/bin/chromium /usr/local/bin/chrome; \
    else \
      wget -qO- https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg && \
      echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list && \
      apt-get update && apt-get install -y --no-install-recommends google-chrome-stable && \
      ln -s /opt/google/chrome/chrome /usr/local/bin/chrome; \
    fi && \
    rm -rf /var/lib/apt/lists/*

# Separate layer so it doesn't invalidate the cached Chrome install above.
# tzdata: lets Chrome's Intl report a timezone matching the exit IP's geo (set TZ).
# fonts: a slim image ships almost none, which reads as a headless/VM tell; a
# realistic desktop-Linux font set makes font enumeration look like a real box.
# tinyproxy: local relay that injects proxy credentials upstream, since Chrome's
# --proxy-server cannot authenticate.
RUN apt-get update && apt-get install -y --no-install-recommends \
      tzdata jq tinyproxy \
      fonts-liberation fonts-croscore fonts-dejavu fonts-freefont-ttf fonts-noto-core && \
    rm -rf /var/lib/apt/lists/*

# CLDR likely-subtags country → locale table, consulted at launch to align the
# browser locale with the proxy exit-IP's country.
COPY locales.map /opt/locales.map
COPY --chmod=755 entrypoint.sh /entrypoint.sh

EXPOSE 9222
ENTRYPOINT ["/usr/bin/tini","--","/entrypoint.sh"]
