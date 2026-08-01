# The MCP server and the browser it drives, in one image.
#
# The repo's root Dockerfile builds the *browser* — it exposes CDP on 9222 and never
# speaks MCP. A registry that builds a repo's root Dockerfile and then waits for an MCP
# handshake on stdio therefore gets a container that looks broken, which is why the
# Glama listing reported "cannot be installed". This image is the one to point such a
# build at: it starts Chrome, waits for it, and then runs the stdio server.
#
# Built on the published browser image rather than repeating its Chrome/font/Xvfb setup,
# so stealth conformance stays defined in exactly one place.
ARG BROWSER_IMAGE=ghcr.io/dmytrome/groundhog:latest
FROM ${BROWSER_IMAGE}

# uv fetches the interpreter itself, so the image does not inherit whatever Python the
# base distribution happens to ship — Debian bookworm's is 3.11, below this project's
# floor, and pydantic rejects `typing.TypedDict` there when it builds the tool schemas.
ARG PYTHON_VERSION=3.12

# Resolved by uv, the same tool the project is developed and tested with. Debian's pip
# resolved a tree without `lxml_html_clean`, which trafilatura needs and imports lazily
# — so the server built, started, and only then died on the first extraction.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV VIRTUAL_ENV=/opt/groundhog
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Installed from source in this repo, so the image matches the commit it is built from
# rather than whatever PyPI last published.
COPY . /src/mcp
RUN uv venv --python "$PYTHON_VERSION" "$VIRTUAL_ENV" && \
    uv pip install --no-cache /src/mcp && \
    rm -rf /src
# Fails the build rather than shipping an image whose server dies when a client first
# asks it for its tools — the shape of the bug this Dockerfile was written against.
RUN python -c "import asyncio; from groundhog_mcp.server import build_server; \
    names=[t.name for t in asyncio.run(build_server().list_tools())]; \
    assert names == ['read_url','research','search','status'], names; print('tools:', names)"

# The browser is this container's own, already running by the time the server starts —
# so the server must not try to launch one of its own.
ENV CDP_URL=http://127.0.0.1:9222
ENV GROUNDHOG_AUTO_START_BROWSER=false

COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/groundhog-entrypoint
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/groundhog-entrypoint"]
