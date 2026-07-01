# Live integration tests

Connects to the running container over CDP and asserts it passes real anti-bot
detectors (bot.sannysoft.com, areyouheadless), saving a full-page screenshot of
each as proof. Requires Docker, network access, and the container running.

```bash
# from the repo root
docker compose up --build -d

cd tests
npm install
CDP_URL=http://127.0.0.1:9222 npm test
```

- `CDP_URL` defaults to `http://127.0.0.1:9222`. Use `127.0.0.1`, **not**
  `localhost` — Playwright resolves `localhost` to IPv6 `::1`, which the
  container does not listen on.
- Screenshots are written to `tests/screenshots/` (gitignored).
- The tests set a realistic User-Agent and viewport before browsing — the same
  thing every client must do (the container does not override the UA itself).
  They are network-dependent and may be affected if the detection sites change.
