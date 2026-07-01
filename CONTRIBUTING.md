# Contributing

Thanks for your interest in Groundhog.

## Development setup

```bash
cd mcp
uv sync
uv run pytest -q          # unit tests (live tests are skipped without a browser)
uv run ruff check .       # lint
```

Live/integration tests need a running stealth browser and are opt-in:

```bash
docker compose up --build -d
cd mcp && RUN_LIVE=1 CDP_URL=http://127.0.0.1:9222 uv run pytest -q
```

## Pull requests

- Keep changes focused and the diff small; one logical change per PR.
- Lint and tests must pass.
- Write terse commit messages.

## Developer Certificate of Origin

By contributing, you certify the [DCO](https://developercertificate.org/). Sign off your
commits:

```bash
git commit -s -m "your message"
```

## Scope

Groundhog stays a minimal, safe grounding layer. Please open an issue to discuss before
adding new tools or broad features.
