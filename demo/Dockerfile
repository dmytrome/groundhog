FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY mcp/pyproject.toml ./mcp/pyproject.toml
COPY mcp/README.md ./mcp/README.md
COPY mcp/src ./mcp/src
RUN pip install --no-cache-dir ./mcp fastapi "uvicorn[standard]"

COPY demo/app.py ./demo/app.py
COPY demo/static ./demo/static

# Internet-facing service that fetches attacker-chosen URLs — don't run as root.
RUN useradd --system --no-create-home app
USER app

WORKDIR /app/demo
EXPOSE 8000
# --proxy-headers + --forwarded-allow-ips: trust Caddy's X-Forwarded-For so the
# per-IP limiter keys on the real visitor, not the reverse-proxy container.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
